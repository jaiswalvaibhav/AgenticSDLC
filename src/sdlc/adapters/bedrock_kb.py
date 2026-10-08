"""KnowledgeIndex backed by a Bedrock Managed Knowledge Base.

Verified (Oct 2026): Retrieve on a managed KB uses `managedSearchConfiguration`, not
`vectorSearchConfiguration` (that field is for customer-managed KBs only) — this is why
we don't use the Strands `retrieve` tool, which hardcodes `vectorSearchConfiguration`.
Hybrid search + managed reranking are on by default.

use_case scoping is done client-side on the S3 key path (`.../<use_case>/...`, see
aws_sync.py's _pdf_key), not via a metadataAttributes filter: confirmed live against a
real KB that the Managed connector's S3 metadata sidecars (.metadata.json) never get
scanned (numberOfMetadataDocumentsScanned stayed 0 under every documented
connectorParameters shape we tried), so a server-side `equals` filter on `use_case`
silently returns nothing.
"""
import boto3

from sdlc.ports import Chunk

# Over-fetch before filtering client-side, since the use_case scope can't be pushed
# down to Retrieve's filter (see module docstring).
_OVERFETCH_FACTOR = 5


class BedrockKnowledgeIndex:
    def __init__(self, knowledge_base_id: str, region: str):
        self.knowledge_base_id = knowledge_base_id
        self._client = boto3.client("bedrock-agent-runtime", region_name=region)

    def search(self, query: str, use_case: str, top_k: int = 10) -> list[Chunk]:
        resp = self._client.retrieve(
            knowledgeBaseId=self.knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                # numberOfResults: max 100 (Retrieve API limit).
                "managedSearchConfiguration": {"numberOfResults": min(top_k * _OVERFETCH_FACTOR, 100)},
            },
        )
        needle = f"/{use_case}/"
        results = [
            r for r in resp.get("retrievalResults", [])
            if needle in r.get("metadata", {}).get("_source_uri", "")
        ][:top_k]
        return [
            Chunk(
                text=r.get("content", {}).get("text", ""),
                score=r.get("score", 0.0),
                page_title=r.get("metadata", {}).get("title", ""),
                page_url=r.get("metadata", {}).get("url", ""),
                s3_uri=r.get("location", {}).get("s3Location", {}).get("uri", ""),
                metadata=r.get("metadata", {}),
            )
            for r in results
        ]
