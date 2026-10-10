"""`aws-deploy`: storage stack (CloudFormation) + Managed Knowledge Base + data source
(CLI/boto3 step — see kb.py). Idempotent, dry-run by default, records what it created in
the resource ledger so `aws-destroy` can reverse it, and writes the resolved ids back
into config.yaml so later commands (aws-sync, search) don't need them passed again.
"""
import boto3

from sdlc.aws import cfn, kb
from sdlc.aws.ledger import Ledger, Resource
from sdlc.config import save_aws_values

TEMPLATE_PATH = "infra/aws/templates/storage.yaml"


def _account_id(region: str) -> str:
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


def create_kb_and_data_source(aws: dict, ledger: Ledger, *, name_prefix: str, docs_prefix: str,
                               role_arn: str, bucket_name: str, region: str, dry_run: bool,
                               log_label: str = "the Managed Knowledge Base + S3 data source",
                               ) -> tuple[str, str]:
    """Creates (or finds) a Managed Knowledge Base + S3 data source named
    "<name_prefix>-kb"/"<name_prefix>-s3", records ledger entries for each, and
    returns (knowledge_base_id, data_source_id). Shared by `aws-deploy` (the
    Confluence KB) and `jira-kb-create` (the separate Jira KB, see jira_kb_deploy.py)."""
    if dry_run:
        print(f"[dry-run] would read the storage stack's outputs (bucket, KB service role) "
              f"and create {log_label}")
        kb.ensure_knowledge_base(name=f"{name_prefix}-kb", role_arn="(dry-run)",
                                  region=region, embedding_model_type=aws["embedding_model_type"],
                                  embedding_model_arn=aws["embedding_model_arn"], dry_run=True)
        kb.ensure_data_source(knowledge_base_id="(dry-run)", name=f"{name_prefix}-s3",
                               bucket_name=bucket_name, bucket_owner_account_id="(dry-run)",
                               metadata_prefix=docs_prefix, region=region, dry_run=True)
        return "(dry-run)", "(dry-run)"

    kb_id = kb.ensure_knowledge_base(
        name=f"{name_prefix}-kb", role_arn=role_arn, region=region,
        embedding_model_type=aws["embedding_model_type"],
        embedding_model_arn=aws["embedding_model_arn"], dry_run=False,
    )
    ledger.append(Resource(kind="knowledge-base", id=kb_id), dry_run=False)

    data_source_id = kb.ensure_data_source(
        knowledge_base_id=kb_id, name=f"{name_prefix}-s3", bucket_name=bucket_name,
        bucket_owner_account_id=_account_id(region), metadata_prefix=docs_prefix,
        region=region, dry_run=False,
    )
    ledger.append(Resource(kind="data-source", id=data_source_id,
                            extra={"knowledge_base_id": kb_id}), dry_run=False)
    return kb_id, data_source_id


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

    role_arn = "(dry-run)" if dry_run else cfn.stack_outputs(stack_name=stack_name, region=region)["KBServiceRoleArn"]
    kb_id, data_source_id = create_kb_and_data_source(
        aws, ledger, name_prefix=aws["stack_prefix"], docs_prefix=aws["docs_prefix"],
        role_arn=role_arn, bucket_name=bucket_name, region=region, dry_run=dry_run,
    )
    if dry_run:
        return

    save_aws_values({"kb_role_arn": role_arn, "knowledge_base_id": kb_id,
                      "data_source_id": data_source_id}, config_path)
    print(f"knowledge_base_id={kb_id} data_source_id={data_source_id} "
          f"(written to {config_path})")
