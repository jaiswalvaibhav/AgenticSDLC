"""The AgentCore Runtime entrypoint (profile=aws): deployed with `agentcore launch`
(see src/sdlc/aws/agent_deploy.py and CLAUDE.md). Wraps the same analyst task registry
LocalAgentRuntime dispatches to — this is the container that becomes the deployed
Runtime, invoked by AgentCoreAgentRuntime (adapters/agentcore_runtime.py) via
boto3 invoke_agent_runtime.

Verified (Oct 2026): BedrockAgentCoreApp()/@app.entrypoint/app.run() is the standard
wrapper (strandsagents.com + bedrock-agentcore-sdk-python). NOT run against a real
deployed Runtime this session — see CLAUDE.md for what's unverified by execution.

Credentials/config: profile=aws passes Atlassian credentials and config as plain
environment variables on the Runtime (create_agent_runtime's environmentVariables),
per the user's choice — see CLAUDE.md "Decisions" for the plaintext-vs-Secrets-Manager
tradeoff. load_config() already reads env var overrides the same way it does locally.
"""
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from sdlc.adapters.bedrock_kb import BedrockKnowledgeIndex
from sdlc.adapters.confluence import ConfluenceClient
from sdlc.adapters.jira import JiraClient
from sdlc.adapters.s3_store import S3ObjectStore
from sdlc.agents.analyst.tasks import TASKS
from sdlc.config import load_config

app = BedrockAgentCoreApp()


def _dispatch(payload: dict) -> dict:
    """Pulled out of the @app.entrypoint function so it's unit-testable without the
    AgentCore runtime server itself."""
    task_id, context = payload["task_id"], payload["context"]
    task = TASKS.get(task_id)
    if not task or not task.run:
        return {"status": "error", "reason": f"no runnable analyst task for {task_id!r}"}

    cfg = load_config()
    atlassian = cfg["atlassian"]
    doc_source = ConfluenceClient(base_url=atlassian["base_url"], email=atlassian["email"],
                                   api_token=atlassian["api_token"],
                                   space_key=cfg["confluence"]["space_key"])
    tickets = JiraClient(base_url=atlassian["base_url"], email=atlassian["email"],
                         api_token=atlassian["api_token"], project_key=cfg["jira"]["project_key"])
    knowledge_index = BedrockKnowledgeIndex(knowledge_base_id=cfg["aws"]["knowledge_base_id"],
                                             region=cfg["aws"]["region"])
    store = S3ObjectStore(bucket=cfg["aws"]["bucket"], region=cfg["aws"]["region"],
                           prefix=cfg["aws"]["state_prefix"])

    return task.run(context, doc_source=doc_source, knowledge_index=knowledge_index,
                     tickets=tickets, store=store, cfg=cfg, dry_run=context.get("dry_run", True))


@app.entrypoint
def invoke(payload: dict) -> dict:
    return _dispatch(payload)


if __name__ == "__main__":
    app.run()
