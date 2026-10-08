"""The scheduled orchestrator (profile=aws): one Lambda function, triggered by the
EventBridge rule in infra/aws/templates/agent.yaml, running exactly the same
sync_once() cycle `sdlc sync-progress` runs locally — just on a schedule instead of
manually/looping, per CLAUDE.md/BRIEF.md ("the same one-shot command on a schedule").

Deployed as a zip (see src/sdlc/aws/agent_deploy.py): its dependency footprint is
deliberately light — boto3 (built into the Lambda runtime) + requests + pyyaml — it
never imports Strands/WeasyPrint/bs4/graphviz; the heavy analyst-agent work happens on
AgentCore, invoked via AgentCoreAgentRuntime, not in this function.
"""
from sdlc.config import load_config
from sdlc.sync_once import sync_once


def handler(event, context):  # noqa: ARG001 - the Lambda handler signature is fixed
    cfg = load_config()
    result = sync_once(cfg, dry_run=False)
    return {"statusCode": 200, "body": result}
