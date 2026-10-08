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

TEMPLATE_PATH = "infra/aws/templates/agent.yaml"
ENTRYPOINT = "src/sdlc/agents/agentcore_app.py"


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
    """Reads the ARN `agentcore launch` creates. UNVERIFIED this session (no real
    launch was run) — the toolkit writes `.bedrock_agentcore.yaml`; adjust the key(s)
    tried here against what a real run actually produces."""
    config_path = Path(".bedrock_agentcore.yaml")
    if not config_path.exists():
        raise RuntimeError(".bedrock_agentcore.yaml not found after agentcore launch")
    data = yaml.safe_load(config_path.read_text())
    for key in ("agent_runtime_arn", "agentRuntimeArn", "runtime_arn"):
        if key in data:
            return data[key]
    # Fall back to a nested search, since the exact shape isn't confirmed.
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

    cfn.deploy_stack(
        template_path=TEMPLATE_PATH, stack_name=stack_name, region=region,
        parameters={
            "ProjectTag": aws["tags"].get("project", "agentic-sdlc"),
            "KnowledgeBaseArn": kb_arn, "LambdaCodeBucket": bucket, "LambdaCodeKey": key,
            "StateBucketArn": state_bucket_arn,
        },
        tags=aws["tags"], dry_run=dry_run,
    )
    ledger.append(Resource(kind="cfn-stack", id=stack_name), dry_run=dry_run)

    if dry_run:
        print(f"[dry-run] would run: agentcore configure -e {ENTRYPOINT} -er <AgentExecutionRoleArn>")
        print("[dry-run] would run: agentcore launch")
        print("[dry-run] would update the Lambda's AGENT_RUNTIME_ARN env var once known")
        return

    outputs = cfn.stack_outputs(stack_name=stack_name, region=region)
    role_arn, lambda_name = outputs["AgentExecutionRoleArn"], outputs["OrchestratorLambdaName"]

    _run_agentcore_cli(["agentcore", "configure", "-e", ENTRYPOINT, "-er", role_arn], dry_run=False)
    _run_agentcore_cli(["agentcore", "launch"], dry_run=False)
    agent_runtime_arn = _read_agent_runtime_arn()
    ledger.append(Resource(kind="agentcore-runtime", id=agent_runtime_arn), dry_run=False)

    boto3.client("lambda", region_name=region).update_function_configuration(
        FunctionName=lambda_name, Environment={"Variables": {
            "SDLC__PROFILE": "aws", "SDLC__AWS__AGENT_RUNTIME_ARN": agent_runtime_arn,
        }},
    )

    cfg["aws"]["agent_runtime_arn"] = agent_runtime_arn
    Path(config_path).write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"agent_runtime_arn={agent_runtime_arn} (written to {config_path})")
