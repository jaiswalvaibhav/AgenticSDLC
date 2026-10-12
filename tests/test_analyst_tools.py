from sdlc.agents.analyst.tools import make_search_knowledge_tool


class BrokenIndex:
    def search(self, query, use_case, top_k=10):
        raise RuntimeError("AccessDenied")


def test_a_failing_knowledge_base_is_reported_to_the_model_not_raised():
    tool = make_search_knowledge_tool(BrokenIndex(), "uc")
    out = tool("anything")
    assert "Knowledge base unavailable" in out and "AccessDenied" in out


def test_the_tool_reports_each_query_and_what_came_back():
    from sdlc.adapters.fake import FakeKnowledgeIndex
    from sdlc.ports import Chunk

    chunk = Chunk(text="Order grain", score=0.9, page_title="SR", page_url="u", s3_uri="s3://x")
    seen = []
    tool = make_search_knowledge_tool(FakeKnowledgeIndex([chunk]), "uc", on_search=lambda q: seen.append(("query", q)),
                                      on_result=lambda chunks, error: seen.append(("result", chunks, error)))
    assert "Order grain" in tool("orders")
    assert seen == [("query", "orders"), ("result", [chunk], None)]

    seen.clear()
    make_search_knowledge_tool(BrokenIndex(), "uc", on_search=lambda q: seen.append("query"),
                               on_result=lambda chunks, error: seen.append((chunks, error)))("x")
    assert seen == ["query", ([], "RuntimeError: AccessDenied")]
