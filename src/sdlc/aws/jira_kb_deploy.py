"""`jira-kb-create`: creates a second Managed Knowledge Base + S3 data source,
dedicated to the Jira downloader's corpus — entirely separate from the Confluence
KB `aws-deploy` creates. Reuses the storage stack's existing S3 bucket + KB service
role (storage.yaml, deployed by `aws-deploy`); no new CloudFormation stack. Run by
hand, once; never called by `aws-deploy` or any other command (see CLAUDE.md "Jira
downloader" — this pipeline is explicit-trigger-only throughout).

Recorded in the same resource ledger `aws-deploy` uses, with kind "knowledge-base"/
"data-source" — the same kinds `aws-destroy` already knows how to delete, so no
change to destroy.py is needed for these to be torn down along with everything
else it reverses.
"""
from pathlib import Path

import yaml

from sdlc.aws import cfn, kb
from sdlc.aws.deploy import _account_id
from sdlc.aws.ledger import Ledger, Resource

STORAGE_TEMPLATE_PATH = "infra/aws/templates/storage.yaml"


def deploy_jira_kb(cfg: dict, *, config_path: str = "config.yaml", dry_run: bool = True) -> None:
    aws = cfg["aws"]
    region, bucket_name = aws["region"], aws["bucket"]
    stack_name = f"{aws['stack_prefix']}-storage"
    ledger = Ledger()

    if dry_run:
        print("[dry-run] would read the storage stack's outputs (bucket, KB service role) "
              "and create a second Managed Knowledge Base + S3 data source for Jira")
        kb.ensure_knowledge_base(name=f"{aws['stack_prefix']}-jira-kb", role_arn="(dry-run)",
                                  region=region, embedding_model_type=aws["embedding_model_type"],
                                  embedding_model_arn=aws["embedding_model_arn"], dry_run=True)
        kb.ensure_data_source(knowledge_base_id="(dry-run)", name=f"{aws['stack_prefix']}-jira-s3",
                               bucket_name=bucket_name, bucket_owner_account_id="(dry-run)",
                               metadata_prefix=aws["jira_docs_prefix"], region=region, dry_run=True)
        return

    outputs = cfn.stack_outputs(stack_name=stack_name, region=region)
    role_arn = outputs["KBServiceRoleArn"]

    kb_id = kb.ensure_knowledge_base(
        name=f"{aws['stack_prefix']}-jira-kb", role_arn=role_arn, region=region,
        embedding_model_type=aws["embedding_model_type"],
        embedding_model_arn=aws["embedding_model_arn"], dry_run=False,
    )
    ledger.append(Resource(kind="knowledge-base", id=kb_id), dry_run=False)

    data_source_id = kb.ensure_data_source(
        knowledge_base_id=kb_id, name=f"{aws['stack_prefix']}-jira-s3", bucket_name=bucket_name,
        bucket_owner_account_id=_account_id(region), metadata_prefix=aws["jira_docs_prefix"],
        region=region, dry_run=False,
    )
    ledger.append(Resource(kind="data-source", id=data_source_id,
                            extra={"knowledge_base_id": kb_id}), dry_run=False)

    cfg["aws"]["jira_knowledge_base_id"] = kb_id
    cfg["aws"]["jira_data_source_id"] = data_source_id
    Path(config_path).write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"jira_knowledge_base_id={kb_id} jira_data_source_id={data_source_id} "
          f"(written to {config_path})")
