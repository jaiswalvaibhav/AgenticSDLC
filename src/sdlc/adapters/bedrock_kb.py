"""KnowledgeIndex backed by a Bedrock Managed Knowledge Base.

Verified (Oct 2026): Retrieve on a managed KB uses `managedSearchConfiguration`, not
`vectorSearchConfiguration` (that field is for customer-managed KBs only) — this is why
we don't use the Strands `retrieve` tool, which hardcodes `vectorSearchConfiguration`.
Hybrid search + managed reranking are on by default. `startsWith`/`stringContains`
filters aren't supported for managed KBs, so the use_case scope uses `equals`.
"""
import boto3

from sdlc.ports import Chunk


class BedrockKnowledgeIndex:
    def __init__(self, knowledge_base_id: str, region: str):
        self.knowledge_base_id = knowledge_base_id
        self._client = boto3.client("bedrock-agent-runtime", region_name=region)

    def search(self, query: str, use_case: str, top_k: int = 10) -> list[Chunk]:
        resp = self._client.retrieve(
            knowledgeBaseId=self.knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "managedSearchConfiguration": {
                    "filter": {"equals": {"key": "use_case", "value": use_case}},
                },
            },
        )
        results = resp.get("retrievalResults", [])[:top_k]
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
