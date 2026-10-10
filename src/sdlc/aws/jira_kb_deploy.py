"""`jira-kb-create`: creates a second Managed Knowledge Base + S3 data source,
dedicated to the Jira downloader's corpus — entirely separate from the Confluence
KB `aws-deploy` creates. Reuses the storage stack's existing S3 bucket + KB service
role (storage.yaml, deployed by `aws-deploy`); no new CloudFormation stack. Run by
hand, once; never called by `aws-deploy` or any other command (see docs/DECISIONS.md "Jira
downloader" — this pipeline is explicit-trigger-only throughout).

Recorded in the same resource ledger `aws-deploy` uses, with kind "knowledge-base"/
"data-source" — the same kinds `aws-destroy` already knows how to delete, so no
change to destroy.py is needed for these to be torn down along with everything
else it reverses.
"""
from sdlc.aws import cfn
from sdlc.aws.deploy import create_kb_and_data_source
from sdlc.aws.ledger import Ledger
from sdlc.config import save_aws_values

STORAGE_TEMPLATE_PATH = "infra/aws/templates/storage.yaml"


def deploy_jira_kb(cfg: dict, *, config_path: str = "config.yaml", dry_run: bool = True) -> None:
    aws = cfg["aws"]
    region, bucket_name = aws["region"], aws["bucket"]
    stack_name = f"{aws['stack_prefix']}-storage"
    ledger = Ledger()

    role_arn = "(dry-run)" if dry_run else cfn.stack_outputs(stack_name=stack_name, region=region)["KBServiceRoleArn"]
    kb_id, data_source_id = create_kb_and_data_source(
        aws, ledger, name_prefix=f"{aws['stack_prefix']}-jira", docs_prefix=aws["jira_docs_prefix"],
        role_arn=role_arn, bucket_name=bucket_name, region=region, dry_run=dry_run,
        log_label="a second Managed Knowledge Base + S3 data source for Jira",
    )
    if dry_run:
        return

    save_aws_values({"jira_knowledge_base_id": kb_id, "jira_data_source_id": data_source_id}, config_path)
    print(f"jira_knowledge_base_id={kb_id} jira_data_source_id={data_source_id} "
          f"(written to {config_path})")
