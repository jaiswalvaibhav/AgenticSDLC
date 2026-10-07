"""`aws-deploy`: storage stack (CloudFormation) + Managed Knowledge Base + data source
(CLI/boto3 step — see kb.py). Idempotent, dry-run by default, records what it created in
the resource ledger so `aws-destroy` can reverse it, and writes the resolved ids back
into config.yaml so later commands (aws-sync, search) don't need them passed again.
"""
from pathlib import Path

import boto3
import yaml

from sdlc.aws import cfn, kb
from sdlc.aws.ledger import Ledger, Resource

TEMPLATE_PATH = "infra/aws/templates/storage.yaml"


def _account_id(region: str) -> str:
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


def deploy(cfg: dict, *, config_path: str = "config.yaml", dry_run: bool = True) -> None:
    aws = cfg["aws"]
    region, bucket_name = aws["region"], aws["bucket"]
    stack_name = f"{aws['stack_prefix']}-storage"
    ledger = Ledger()

    cfn.deploy_stack(
        template_path=TEMPLATE_PATH, stack_name=stack_name, region=region,
        parameters={"BucketName": bucket_name, "ProjectTag": aws["tags"].get("project", "agentic-sdlc")},
        tags=aws["tags"], dry_run=dry_run,
    )
    ledger.append(Resource(kind="cfn-stack", id=stack_name), dry_run=dry_run)

    if dry_run:
        print("[dry-run] would read stack outputs (bucket, KB service role) and "
              "create the Managed Knowledge Base + S3 data source")
        kb.ensure_knowledge_base(name=f"{aws['stack_prefix']}-kb", role_arn="(dry-run)",
                                  region=region, embedding_model_type=aws["embedding_model_type"],
                                  embedding_model_arn=aws["embedding_model_arn"], dry_run=True)
        kb.ensure_data_source(knowledge_base_id="(dry-run)", name=f"{aws['stack_prefix']}-s3",
                               bucket_name=bucket_name, bucket_owner_account_id="(dry-run)",
                               metadata_prefix=aws["docs_prefix"], region=region, dry_run=True)
        return

    outputs = cfn.stack_outputs(stack_name=stack_name, region=region)
    role_arn = outputs["KBServiceRoleArn"]

    kb_id = kb.ensure_knowledge_base(
        name=f"{aws['stack_prefix']}-kb", role_arn=role_arn, region=region,
        embedding_model_type=aws["embedding_model_type"],
        embedding_model_arn=aws["embedding_model_arn"], dry_run=False,
    )
    ledger.append(Resource(kind="knowledge-base", id=kb_id), dry_run=False)

    data_source_id = kb.ensure_data_source(
        knowledge_base_id=kb_id, name=f"{aws['stack_prefix']}-s3", bucket_name=bucket_name,
        bucket_owner_account_id=_account_id(region), metadata_prefix=aws["docs_prefix"],
        region=region, dry_run=False,
    )
    ledger.append(Resource(kind="data-source", id=data_source_id,
                            extra={"knowledge_base_id": kb_id}), dry_run=False)

    cfg["aws"]["kb_role_arn"] = role_arn
    cfg["aws"]["knowledge_base_id"] = kb_id
    cfg["aws"]["data_source_id"] = data_source_id
    Path(config_path).write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"knowledge_base_id={kb_id} data_source_id={data_source_id} "
          f"(written to {config_path})")
