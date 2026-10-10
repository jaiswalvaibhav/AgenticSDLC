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
per the user's choice — see docs/DECISIONS.md, Phase 7, for the plaintext-vs-Secrets-Manager
tradeoff. load_config() already reads env var overrides the same way it does locally.
"""
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from sdlc.adapters.s3_store import S3ObjectStore
from sdlc.agents.analyst.tasks import TASKS
from sdlc.config import load_config
from sdlc.wiring import confluence_client, jira_client, knowledge_index as knowledge_index_factory

app = BedrockAgentCoreApp()


def _dispatch(payload: dict) -> dict:
    """Pulled out of the @app.entrypoint function so it's unit-testable without the
    AgentCore runtime server itself."""
    task_id, context = payload["task_id"], payload["context"]
    task = TASKS.get(task_id)
    if not task or not task.run:
        return {"status": "error", "reason": f"no runnable analyst task for {task_id!r}"}

    cfg = load_config()
    doc_source = confluence_client(cfg)
    tickets = jira_client(cfg)
    index = knowledge_index_factory(cfg)
    # Always S3 here: this is the container the AgentCore Runtime deploys, which
    # only ever runs under profile=aws (unlike wiring.object_store, which also
    # handles profile=local for the CLI/Lambda).
    store = S3ObjectStore(bucket=cfg["aws"]["bucket"], region=cfg["aws"]["region"],
                           prefix=cfg["aws"]["state_prefix"])

    return task.run(context, doc_source=doc_source, knowledge_index=index,
                     tickets=tickets, store=store, cfg=cfg, dry_run=context.get("dry_run", True))


@app.entrypoint
def invoke(payload: dict) -> dict:
    return _dispatch(payload)


if __name__ == "__main__":
    app.run()
