"""`aws-deploy`'s agent half: zips+uploads the orchestrator Lambda, deploys
infra/aws/templates/agent.yaml (IAM roles, the Lambda, the EventBridge schedule), then
runs the `agentcore` CLI toolkit to build+push the container and create the AgentCore
Runtime itself (CloudFormation can't do that part — see agent.yaml's header comment).

NOT run against real AWS this session (it would build+push a real container image and
create billable resources) — written per the verified `agentcore configure`/`launch`
CLI shape (CLAUDE.md), but double-check the two unverified bits flagged below the first
time this actually runs: `agentcore launch`'s output format and the resulting
`.bedrock_agentcore.yaml`'s exact ARN key.
"""
import re
import shlex
import subprocess
import tempfile
import zipfile
from pathlib import Path

import boto3
import yaml

from sdlc.aws import cfn
from sdlc.aws.ledger import Ledger, Resource
from sdlc.config import save_aws_values

TEMPLATE_PATH = "infra/aws/templates/agent.yaml"
ENTRYPOINT = "src/sdlc/agents/agentcore_app.py"
AGENT_NAME = "sdlc_orchestrator"


def _zip_lambda_code(zip_path: Path) -> None:
    """Zips just the modules orchestrator_lambda.py's chain needs: config, sync_once,
    wiring, ports, workflow/, and the jira/s3/agentcore_runtime adapters — not the
    confluence/bedrock_kb/seed/pdf/diagrams/aws_sync modules or the agents/ package."""
    keep_files = {
        "sdlc/__init__.py", "sdlc/config.py", "sdlc/ports.py", "sdlc/wiring.py",
        "sdlc/sync_once.py",
        "sdlc/adapters/__init__.py", "sdlc/adapters/jira.py", "sdlc/adapters/jira_polling.py",
        "sdlc/adapters/local_store.py", "sdlc/adapters/s3_store.py", "sdlc/adapters/agentcore_runtime.py",
        "sdlc/aws/__init__.py", "sdlc/aws/orchestrator_lambda.py",
        "sdlc/workflow/__init__.py", "sdlc/workflow/registry.py", "sdlc/workflow/orchestrator.py",
    }
    src_root = Path("src")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in keep_files:
            path = src_root / rel
            if path.exists():
                zf.write(path, rel)
        zf.write("config.yaml", "config.yaml")
        zf.write("config/workflow.yaml", "config/workflow.yaml")


def _upload_lambda_code(cfg: dict, *, dry_run: bool) -> tuple[str, str]:
    bucket, key = cfg["aws"]["bucket"], f"{cfg['aws']['state_prefix']}lambda/orchestrator.zip"
    if dry_run:
        print(f"[dry-run] would zip the orchestrator Lambda and upload to s3://{bucket}/{key}")
        return bucket, key
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "orchestrator.zip"
        _zip_lambda_code(zip_path)
        boto3.client("s3", region_name=cfg["aws"]["region"]).upload_file(str(zip_path), bucket, key)
    return bucket, key


def _run_agentcore_cli(cmd: list[str], *, dry_run: bool) -> str:
    if dry_run:
        print(f"[dry-run] would run: {shlex.join(cmd)}")
        return ""
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    print(result.stdout)
    return result.stdout


def _read_agent_runtime_arn() -> str:
    """Reads the ARN `agentcore launch` creates: `.bedrock_agentcore.yaml`'s
    `agents.<default_agent>.bedrock_agentcore.agent_arn` — confirmed against a real
    `agentcore configure`/`launch` run (bedrock-agentcore-starter-toolkit 0.3.14)."""
    config_path = Path(".bedrock_agentcore.yaml")
    if not config_path.exists():
        raise RuntimeError(".bedrock_agentcore.yaml not found after agentcore launch")
    data = yaml.safe_load(config_path.read_text())
    agent_name = data.get("default_agent")
    arn = data.get("agents", {}).get(agent_name, {}).get("bedrock_agentcore", {}).get("agent_arn")
    if arn:
        return arn
    # Fall back to a nested search, in case the toolkit's shape changes again.
    found = re.search(r"arn:aws:bedrock-agentcore:[^\s\"']+:runtime/[^\s\"']+", config_path.read_text())
    if found:
        return found.group(0)
    raise RuntimeError(f"couldn't find the agent runtime ARN in {config_path} — "
                        "check its contents and update _read_agent_runtime_arn")


def deploy(cfg: dict, *, config_path: str = "config.yaml", dry_run: bool = True) -> None:
    aws = cfg["aws"]
    region, stack_name = aws["region"], f"{aws['stack_prefix']}-agent"
    ledger = Ledger()

    bucket, key = _upload_lambda_code(cfg, dry_run=dry_run)
    kb_arn = f"arn:aws:bedrock:{region}:*:knowledge-base/{aws['knowledge_base_id'] or '(dry-run)'}"
    state_bucket_arn = f"arn:aws:s3:::{aws['bucket']}"

    atlassian = cfg["atlassian"]
    cfn.deploy_stack(
        template_path=TEMPLATE_PATH, stack_name=stack_name, region=region,
        parameters={
            "ProjectTag": aws["tags"].get("project", "agentic-sdlc"),
            "KnowledgeBaseArn": kb_arn, "LambdaCodeBucket": bucket, "LambdaCodeKey": key,
            "StateBucketArn": state_bucket_arn,
            "AtlassianBaseUrl": atlassian["base_url"], "AtlassianEmail": atlassian["email"],
            "AtlassianApiToken": atlassian["api_token"], "JiraProjectKey": cfg["jira"]["project_key"],
        },
        tags=aws["tags"], dry_run=dry_run,
        secret_keys=frozenset({"AtlassianApiToken"}),
    )
    ledger.append(Resource(kind="cfn-stack", id=stack_name), dry_run=dry_run)

    if dry_run:
        print(f"[dry-run] would run: agentcore configure -e {ENTRYPOINT} -er <AgentExecutionRoleArn>")
        print("[dry-run] would run: agentcore launch")
        print("[dry-run] would update the Lambda's AGENT_RUNTIME_ARN env var once known")
        return

    outputs = cfn.stack_outputs(stack_name=stack_name, region=region)
    role_arn, lambda_name = outputs["AgentExecutionRoleArn"], outputs["OrchestratorLambdaName"]

    _run_agentcore_cli(["agentcore", "configure", "-e", ENTRYPOINT, "-er", role_arn,
                        "-n", AGENT_NAME, "--non-interactive"], dry_run=False)
    _run_agentcore_cli(["agentcore", "launch", "-auc"], dry_run=False)
    agent_runtime_arn = _read_agent_runtime_arn()
    ledger.append(Resource(kind="agentcore-runtime", id=agent_runtime_arn), dry_run=False)

    lambda_client = boto3.client("lambda", region_name=region)
    current_vars = lambda_client.get_function_configuration(
        FunctionName=lambda_name)["Environment"]["Variables"]
    lambda_client.update_function_configuration(
        FunctionName=lambda_name, Environment={"Variables": {
            **current_vars, "SDLC__AWS__AGENT_RUNTIME_ARN": agent_runtime_arn,
        }},
    )

    save_aws_values({"agent_runtime_arn": agent_runtime_arn}, config_path)
    print(f"agent_runtime_arn={agent_runtime_arn} (written to {config_path})")
