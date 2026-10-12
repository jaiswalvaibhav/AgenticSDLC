"""Adapter factories shared by the CLI and the scheduled-orchestrator Lambda
(src/sdlc/aws/orchestrator_lambda.py) — kept Typer-free so the Lambda doesn't need it.

LocalAgentRuntime, ConfluenceClient and BedrockKnowledgeIndex are imported lazily,
inside the functions below, not at module scope: they transitively pull in the analyst
engine (bs4, and lazily Strands) and modules the scheduled Lambda's zip leaves out
(aws/agent_deploy.py's _zip_lambda_code), and the Lambda never needs them (profile=aws
always takes the AgentCoreAgentRuntime branch) — guarded by tests/test_orchestrator_lambda.py
and tests/test_agent_deploy.py.
"""
from typing import TYPE_CHECKING

from sdlc.adapters.agentcore_runtime import AgentCoreAgentRuntime
from sdlc.adapters.jira import JiraClient
from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.mock_jira import MockJiraClient
from sdlc.adapters.s3_store import S3ObjectStore

if TYPE_CHECKING:
    from sdlc.adapters.bedrock_kb import BedrockKnowledgeIndex
    from sdlc.adapters.confluence import ConfluenceClient


def confluence_client(cfg: dict) -> "ConfluenceClient":
    from sdlc.adapters.confluence import ConfluenceClient
    atlassian = cfg["atlassian"]
    return ConfluenceClient(
        base_url=atlassian["base_url"], email=atlassian["email"],
        api_token=atlassian["api_token"], space_key=cfg["confluence"]["space_key"],
    )


def jira_mode(cfg: dict) -> str:
    """`jira.mode`: "mock" (tickets in our own ObjectStore, shown by the UI's mock Jira) or
    "real" (Jira Cloud). A config with no `mode` is real, as it was before the mock existed."""
    mode = cfg["jira"].get("mode", "real")
    if mode not in ("mock", "real"):
        raise ValueError(f"jira.mode must be 'mock' or 'real', got {mode!r}")
    return mode


def jira_project_key(cfg: dict) -> str:
    """The project tickets are created in: the mock's own key in mock mode."""
    return cfg["jira"]["mock"]["project_key"] if jira_mode(cfg) == "mock" else cfg["jira"]["project_key"]


def jira_browse_url(cfg: dict, key: str) -> str:
    """Where a ticket opens: Jira's page, or (mock) the UI's own page, a path on the same origin
    (with ?profile= when the board isn't the local one, so the page reads the right store)."""
    if jira_mode(cfg) == "mock":
        return f"/jira/browse/{key}" + (f"?profile={cfg['profile']}" if cfg.get("profile") != "local" else "")
    return f"{cfg['atlassian']['base_url'].rstrip('/')}/browse/{key}"


def jira_client(cfg: dict) -> JiraClient | MockJiraClient:
    if jira_mode(cfg) == "mock":
        mock = cfg["jira"]["mock"]
        return MockJiraClient(object_store(cfg), project_key=mock["project_key"],
                               project_name=mock.get("project_name", "Mock SDLC"),
                               issue_types=mock.get("issue_types"), statuses=mock.get("statuses"))
    atlassian = cfg["atlassian"]
    return JiraClient(
        base_url=atlassian["base_url"], email=atlassian["email"],
        api_token=atlassian["api_token"], project_key=cfg["jira"]["project_key"],
    )


def knowledge_index(cfg: dict, kb_id: str | None = None) -> "BedrockKnowledgeIndex":
    """kb_id defaults to the Confluence KB (aws.knowledge_base_id); pass
    cfg["aws"]["jira_knowledge_base_id"] for the separate Jira KB (see jira_search)."""
    from sdlc.adapters.bedrock_kb import BedrockKnowledgeIndex
    return BedrockKnowledgeIndex(knowledge_base_id=kb_id or cfg["aws"]["knowledge_base_id"],
                                  region=cfg["aws"]["region"])


def kb_enabled(cfg: dict) -> tuple[bool, str]:
    """Whether the analyst agent gets a Knowledge Base, and why not: `analyst.use_knowledge_base`
    (default true) and a non-empty `aws.knowledge_base_id` must both hold."""
    if not cfg.get("analyst", {}).get("use_knowledge_base", True):
        return False, "analyst.use_knowledge_base is false"
    if not cfg["aws"].get("knowledge_base_id"):
        return False, "aws.knowledge_base_id is not set"
    return True, ""


def object_store(cfg: dict):
    """local profile: a local folder. aws profile: S3, so the Lambda, the AgentCore
    agent and any local-profile run against the same bucket all share state."""
    if cfg["profile"] == "aws":
        return S3ObjectStore(bucket=cfg["aws"]["bucket"], region=cfg["aws"]["region"],
                              prefix=cfg["aws"]["state_prefix"])
    return LocalObjectStore(cfg["state_dir"])


def agent_runtime(cfg: dict, tickets, store):
    """local profile runs the analyst agent in-process. aws profile invokes the
    AgentCore Runtime deployed by aws-deploy (see infra/aws/templates/agent.yaml)."""
    if cfg["profile"] == "aws":
        return AgentCoreAgentRuntime(agent_runtime_arn=cfg["aws"]["agent_runtime_arn"], region=cfg["aws"]["region"])
    from sdlc.agents.runtime import LocalAgentRuntime
    return LocalAgentRuntime(doc_source=confluence_client(cfg), knowledge_index=knowledge_index(cfg) if kb_enabled(cfg)[0] else None,
                              tickets=tickets, store=store, cfg=cfg)
