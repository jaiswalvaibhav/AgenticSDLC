from sdlc.jira_citations import jira_url_from_s3_uri

BASE_URL = "https://x.atlassian.net"


def test_resolves_epic_level_uri():
    uri = "s3://bucket/jira/demo_order_fulfilment/jira/DEMO-1/issue.md"
    assert jira_url_from_s3_uri(uri, BASE_URL) == f"{BASE_URL}/browse/DEMO-1"


def test_resolves_deepest_subtask_key_in_nested_path():
    uri = "s3://bucket/jira/demo_order_fulfilment/jira/DEMO-1/DEMO-2/DEMO-3/diagram.png"
    assert jira_url_from_s3_uri(uri, BASE_URL) == f"{BASE_URL}/browse/DEMO-3"


def test_returns_none_for_non_jira_uri():
    uri = "s3://bucket/confluence/demo_order_fulfilment/2.pdf"
    assert jira_url_from_s3_uri(uri, BASE_URL) is None
