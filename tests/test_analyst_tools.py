from sdlc.agents.analyst.tools import make_search_knowledge_tool


class BrokenIndex:
    def search(self, query, use_case, top_k=10):
        raise RuntimeError("AccessDenied")


def test_a_failing_knowledge_base_is_reported_to_the_model_not_raised():
    tool = make_search_knowledge_tool(BrokenIndex(), "uc")
    out = tool("anything")
    assert "Knowledge base unavailable" in out and "AccessDenied" in out
