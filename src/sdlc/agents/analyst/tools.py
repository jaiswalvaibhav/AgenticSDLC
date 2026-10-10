"""The analyst agent's search_knowledge tool. Our own, not Strands' built-in `retrieve`
tool — that one hardcodes vectorSearchConfiguration, which a Managed Knowledge Base
doesn't support (see docs/DECISIONS.md, Phase 0). Scoped to one use_case so other use cases
in the same KB never leak into results."""
from strands import tool

from sdlc.ports import KnowledgeIndex


def make_search_knowledge_tool(knowledge_index: KnowledgeIndex, use_case: str):
    @tool
    def search_knowledge(query: str) -> str:
        """Search supporting Confluence pages for this use case: stakeholder
        requirements, source data analysis, conceptual data model, feasibility
        analysis, glossary. Use this for anything not already given to you in full
        (the Data Design Solution and Technical Design Solution are given in full,
        above — don't search for those).

        Args:
            query: a natural-language search query.
        """
        chunks = knowledge_index.search(query, use_case=use_case, top_k=5)
        if not chunks:
            return "No results."
        return "\n\n".join(
            f"[{c.page_title}] {c.text}\n(source: {c.page_url}, s3: {c.s3_uri})" for c in chunks
        )

    return search_knowledge
