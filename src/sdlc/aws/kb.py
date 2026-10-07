"""Creates the Bedrock Managed Knowledge Base + its S3 data source. Idempotent (checks
by name first) and dry-run by default. A CLI/boto3 step, not CloudFormation — a Managed
KB with embeddingModelType=MANAGED reportedly can't be deployed via CloudFormation
(schema conflict between the template and the service API). See CLAUDE.md.

Verified against developer.aws.../bedrock/.../kb-managed-create.html and
kb-managed-ds-s3.html (Oct 2026): create-knowledge-base with
{"type": "MANAGED", "managedKnowledgeBaseConfiguration": {"embeddingModelType": ...}},
then create-data-source with type MANAGED_KNOWLEDGE_BASE_CONNECTOR / connectorParameters
type S3.
"""
import boto3


def _client(region: str):
    return boto3.client("bedrock-agent", region_name=region)


def find_knowledge_base(*, name: str, region: str) -> dict | None:
    client = _client(region)
    paginator = client.get_paginator("list_knowledge_bases")
    for page in paginator.paginate():
        for kb in page["knowledgeBaseSummaries"]:
            if kb["name"] == name:
                return kb
    return None


def find_data_source(*, knowledge_base_id: str, name: str, region: str) -> dict | None:
    client = _client(region)
    paginator = client.get_paginator("list_data_sources")
    for page in paginator.paginate(knowledgeBaseId=knowledge_base_id):
        for ds in page["dataSourceSummaries"]:
            if ds["name"] == name:
                return ds
    return None


def ensure_knowledge_base(*, name: str, role_arn: str, region: str,
                           embedding_model_type: str = "MANAGED",
                           embedding_model_arn: str = "",
                           dry_run: bool = True) -> str:
    """Returns the knowledge base id (existing or newly created)."""
    existing = find_knowledge_base(name=name, region=region)
    if existing:
        return existing["knowledgeBaseId"]

    kb_config: dict = {"type": "MANAGED", "managedKnowledgeBaseConfiguration":
                        {"embeddingModelType": embedding_model_type}}
    if embedding_model_type == "CUSTOM":
        kb_config["managedKnowledgeBaseConfiguration"]["embeddingModelArn"] = embedding_model_arn
        kb_config["managedKnowledgeBaseConfiguration"]["embeddingModelConfiguration"] = \
            {"bedrockEmbeddingModelConfiguration": {"dimensions": 1024}}

    if dry_run:
        print(f"[dry-run] would create-knowledge-base name={name!r} role={role_arn} "
              f"config={kb_config}")
        return "(dry-run)"

    resp = _client(region).create_knowledge_base(
        name=name, roleArn=role_arn, knowledgeBaseConfiguration=kb_config,
    )
    return resp["knowledgeBase"]["knowledgeBaseId"]


def ensure_data_source(*, knowledge_base_id: str, name: str, bucket_name: str,
                        bucket_owner_account_id: str, metadata_prefix: str, region: str,
                        dry_run: bool = True) -> str:
    """Returns the data source id (existing or newly created)."""
    if knowledge_base_id == "(dry-run)":
        print(f"[dry-run] would create-data-source name={name!r} bucket={bucket_name}")
        return "(dry-run)"

    existing = find_data_source(knowledge_base_id=knowledge_base_id, name=name, region=region)
    if existing:
        return existing["dataSourceId"]

    ds_config = {
        "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
        "managedKnowledgeBaseConnectorConfiguration": {
            "mediaExtractionConfiguration": {
                "imageExtractionConfiguration": {"imageExtractionStatus": "ENABLED"},
            },
            "connectorParameters": {
                "type": "S3",
                "version": "1",
                "connectionConfiguration": {
                    "bucketName": bucket_name,
                    "bucketOwnerAccountId": bucket_owner_account_id,
                },
                "metadataFilesPrefix": metadata_prefix,
            },
        },
    }
    if dry_run:
        print(f"[dry-run] would create-data-source name={name!r} config={ds_config}")
        return "(dry-run)"

    resp = _client(region).create_data_source(
        knowledgeBaseId=knowledge_base_id, name=name, dataSourceConfiguration=ds_config,
    )
    return resp["dataSource"]["dataSourceId"]
