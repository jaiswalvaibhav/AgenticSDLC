"""jira.mode picks the ticket system; the project key and browse URL follow it."""
import pytest

from sdlc.adapters.jira import JiraClient
from sdlc.adapters.mock_jira import MockJiraClient
from sdlc.wiring import jira_browse_url, jira_client, jira_mode, jira_project_key


@pytest.fixture
def cfg(tmp_path):
    return {"profile": "local", "state_dir": str(tmp_path),
            "atlassian": {"base_url": "https://x.atlassian.net/", "email": "a@b.c", "api_token": "t"},
            "jira": {"mode": "mock", "project_key": "REAL",
                     "mock": {"project_key": "MOCK", "project_name": "Mock", "issue_types": ["Epic", "Story"],
                              "statuses": ["Open", "Closed"]}}}


def test_mock_mode_builds_the_mock_from_its_own_config(cfg):
    jira = jira_client(cfg)
    assert isinstance(jira, MockJiraClient)
    assert (jira.project_key, jira.issue_types, jira.statuses) == ("MOCK", ["Epic", "Story"], ["Open", "Closed"])
    assert jira_project_key(cfg) == "MOCK" and jira_browse_url(cfg, "MOCK-1") == "/jira/browse/MOCK-1"


def test_mock_mode_shares_the_state_dir_with_the_run(cfg, tmp_path):
    jira_client(cfg).create_issue("Epic", "e", dry_run=False)
    assert (tmp_path / "mock_jira/MOCK/issues/MOCK-1.json").exists()


def test_real_mode_builds_jira_cloud(cfg):
    cfg["jira"]["mode"] = "real"
    assert isinstance(jira_client(cfg), JiraClient)
    assert jira_project_key(cfg) == "REAL"
    assert jira_browse_url(cfg, "REAL-7") == "https://x.atlassian.net/browse/REAL-7"


def test_no_mode_means_real_and_a_bad_mode_is_rejected(cfg):
    del cfg["jira"]["mode"]
    assert jira_mode(cfg) == "real"
    cfg["jira"]["mode"] = "fake"
    with pytest.raises(ValueError, match="mock.*real"):
        jira_client(cfg)
