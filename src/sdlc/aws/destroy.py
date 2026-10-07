"""`aws-destroy`: deletes exactly what `aws-deploy` recorded in the resource ledger, in
reverse order (data sources and knowledge bases before the storage stack they depend
on). Dry-run by default. One-command destroy matters because this runs against a
time-boxed free-plan account.
"""
import boto3

from sdlc.aws import cfn
from sdlc.aws.ledger import Ledger


def destroy(cfg: dict, *, dry_run: bool = True) -> None:
    region = cfg["aws"]["region"]
    ledger = Ledger()
    client = boto3.client("bedrock-agent", region_name=region)

    for resource in reversed(ledger.resources):
        if resource.kind == "data-source":
            kb_id = resource.extra["knowledge_base_id"]
            if dry_run:
                print(f"[dry-run] would delete-data-source {resource.id} (kb {kb_id})")
            else:
                client.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=resource.id)
        elif resource.kind == "knowledge-base":
            if dry_run:
                print(f"[dry-run] would delete-knowledge-base {resource.id}")
            else:
                client.delete_knowledge_base(knowledgeBaseId=resource.id)
        elif resource.kind == "cfn-stack":
            cfn.delete_stack(stack_name=resource.id, region=region, dry_run=dry_run)
        else:
            print(f"[warn] unknown ledger resource kind {resource.kind!r}, skipping")
            continue
        ledger.remove(resource.kind, resource.id, dry_run=dry_run)

    if not ledger.resources:
        print("nothing left in the ledger" if not dry_run else "[dry-run] ledger is empty")
