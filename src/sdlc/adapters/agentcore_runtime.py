"""AgentRuntime for profile=aws: invokes the deployed AgentCore Runtime (the
container running src/sdlc/agents/agentcore_app.py) instead of running the analyst
agent in-process. Counterpart to LocalAgentRuntime.

Verified (Oct 2026): boto3.client("bedrock-agentcore").invoke_agent_runtime(
agentRuntimeArn=..., payload=<bytes>) returns a streaming response. NOT verified by an
actual call this session (no runtime has been deployed yet) — the exact response-body
key/shape below is my best read of the docs; double-check it against a real response
once infra/aws/templates/agent.yaml + `agentcore launch` have actually been run.
"""
import json

import boto3


class AgentCoreAgentRuntime:
    def __init__(self, agent_runtime_arn: str, region: str):
        if not agent_runtime_arn:
            raise ValueError(
                "config.yaml aws.agent_runtime_arn is not set — run `sdlc aws-deploy` "
                "(profile=aws) first; see CLAUDE.md for the agentcore launch step."
            )
        self.agent_runtime_arn = agent_runtime_arn
        self._client = boto3.client("bedrock-agentcore", region_name=region)

    def run(self, task_id: str, context: dict) -> dict:
        resp = self._client.invoke_agent_runtime(
            agentRuntimeArn=self.agent_runtime_arn,
            payload=json.dumps({"task_id": task_id, "context": context}).encode(),
        )
        body = resp["response"]
        data = body.read() if hasattr(body, "read") else body
        return json.loads(data)
