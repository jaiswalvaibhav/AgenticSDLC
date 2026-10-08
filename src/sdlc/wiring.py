"""Adapter factories shared by the CLI and the scheduled-orchestrator Lambda
(src/sdlc/aws/orchestrator_lambda.py) — kept Typer-free so the Lambda doesn't need it.

LocalAgentRuntime is imported lazily, inside agent_runtime() below, not at module
scope: it transitively pulls in the analyst engine (bs4, and lazily Strands), which
the Lambda never needs (profile=aws always takes the AgentCoreAgentRuntime branch) and
which the brief wants kept out of the Lambda's dependency footprint.
"""
from sdlc.adapters.agentcore_runtime import AgentCoreAgentRuntime
from sdlc.adapters.bedrock_kb import BedrockKnowledgeIndex
from sdlc.adapters.confluence import ConfluenceClient
from sdlc.adapters.jira import JiraClient
from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.s3_store import S3ObjectStore


def confluence_client(cfg: dict) -> ConfluenceClient:
    atlassian = cfg["atlassian"]
    return ConfluenceClient(
        base_url=atlassian["base_url"], email=atlassian["email"],
        api_token=atlassian["api_token"], space_key=cfg["confluence"]["space_key"],
    )


def jira_client(cfg: dict) -> JiraClient:
    atlassian = cfg["atlassian"]
    return JiraClient(
        base_url=atlassian["base_url"], email=atlassian["email"],
        api_token=atlassian["api_token"], project_key=cfg["jira"]["project_key"],
    )


def knowledge_index(cfg: dict) -> BedrockKnowledgeIndex:
    return BedrockKnowledgeIndex(knowledge_base_id=cfg["aws"]["knowledge_base_id"], region=cfg["aws"]["region"])


def object_store(cfg: dict):
    """local profile: a local folder. aws profile: S3, so the Lambda, the AgentCore
    agent and any local-profile run against the same bucket all share state."""
    if cfg["profile"] == "aws":
        return S3ObjectStore(bucket=cfg["aws"]["bucket"], region=cfg["aws"]["region"],
                              prefix=cfg["aws"]["state_prefix"])
    return LocalObjectStore(cfg["state_dir"])


def agent_runtime(cfg: dict, tickets: JiraClient, store):
    """local profile runs the analyst agent in-process. aws profile invokes the
    AgentCore Runtime deployed by aws-deploy (see infra/aws/templates/agent.yaml)."""
    if cfg["profile"] == "aws":
        return AgentCoreAgentRuntime(agent_runtime_arn=cfg["aws"]["agent_runtime_arn"], region=cfg["aws"]["region"])
    from sdlc.agents.runtime import LocalAgentRuntime
    return LocalAgentRuntime(doc_source=confluence_client(cfg), knowledge_index=knowledge_index(cfg),
                              tickets=tickets, store=store, cfg=cfg)
