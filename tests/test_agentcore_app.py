from sdlc.agents.agentcore_app import _dispatch


def test_unknown_task_returns_error_without_building_any_adapters():
    """The one path testable without a real config.yaml/Atlassian/AWS setup — anything
    further requires the real adapters _dispatch constructs internally."""
    result = _dispatch({"task_id": "not_a_real_task", "context": {}})
    assert result == {"status": "error", "reason": "no runnable analyst task for 'not_a_real_task'"}
