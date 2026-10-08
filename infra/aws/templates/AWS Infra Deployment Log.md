# AWS Infra Deployment Log

Oct 8, 2026

## Overview

Deploying the Autonomous Data SDLC's AWS infrastructure (profile: aws) for use case `demo_order_fulfilment`, account `489675988515`, region `ap-southeast-2`. Two CloudFormation stacks plus two CLI-driven steps (CloudFormation can't create a Bedrock Managed Knowledge Base or an AgentCore Runtime).

**Status: blocked on an AWS service quota.** Everything below is done except the final AgentCore Runtime creation, which is waiting on AWS to approve a "Total Agents per Account" increase (requested 8 Oct 2026, pending).

## 1. Storage stack (S3 bucket + KB IAM role)

CloudFormation, template `infra/aws/templates/storage.yaml`, stack `sdlc-storage`.

```
aws cloudformation deploy \
  --template-file infra/aws/templates/storage.yaml \
  --stack-name sdlc-storage --region ap-southeast-2 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides BucketName=agentic-sdlc-state ProjectTag=agentic-sdlc \
  --tags project=agentic-sdlc
```

Created: S3 bucket `agentic-sdlc-state`, IAM role `sdlc-storage-KBServiceRole-zkFW0P6wUqXs` (for the Knowledge Base).

## 2. Bedrock Managed Knowledge Base + S3 data source

Created via boto3 (`bedrock-agent` client) — no CloudFormation support for this resource type.

```python
bedrock_agent.create_knowledge_base(
    name="sdlc-kb",
    roleArn="arn:aws:iam::489675988515:role/sdlc-storage-KBServiceRole-zkFW0P6wUqXs",
    knowledgeBaseConfiguration={
        "type": "MANAGED",
        "managedKnowledgeBaseConfiguration": {"embeddingModelType": "MANAGED"},
    },
)
bedrock_agent.create_data_source(
    knowledgeBaseId="1IMYCIP6SW", name="sdlc-s3",
    dataSourceConfiguration={"type": "S3", "s3Configuration": {"bucketArn": "arn:aws:s3:::agentic-sdlc-state"}},
)
```

Created: Knowledge Base `1IMYCIP6SW`, data source `KVKVRRWGEK`. No documents ingested yet — that happens on `sdlc aws-sync`.

## 3. Orchestrator Lambda code upload

Zipped the minimal module set (`sync_once`, Jira/S3/AgentCore adapters, workflow registry/orchestrator) and uploaded via boto3 `s3` client, so the CloudFormation stack below has code to attach.

```python
s3.upload_file("orchestrator.zip", "agentic-sdlc-state", "state/lambda/orchestrator.zip")
```

## 4. Agent stack (execution role, Lambda, EventBridge schedule)

CloudFormation, template `infra/aws/templates/agent.yaml`, stack `sdlc-agent`. The Atlassian token is passed as a `NoEcho` parameter — masked in logs/dry-run output, stored only as plaintext Lambda env vars on the deployed function (not Secrets Manager; the simplicity/visibility trade-off was an explicit earlier decision).

```
aws cloudformation deploy \
  --template-file infra/aws/templates/agent.yaml \
  --stack-name sdlc-agent --region ap-southeast-2 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ProjectTag=agentic-sdlc \
    KnowledgeBaseArn=arn:aws:bedrock:ap-southeast-2:*:knowledge-base/1IMYCIP6SW \
    LambdaCodeBucket=agentic-sdlc-state LambdaCodeKey=state/lambda/orchestrator.zip \
    StateBucketArn=arn:aws:s3:::agentic-sdlc-state \
    AtlassianBaseUrl=https://vaibhavjaiswal.atlassian.net/ \
    AtlassianEmail=vaibhavjaiswal248@gmail.com \
    AtlassianApiToken=*** JiraProjectKey=SCRUM \
  --tags project=agentic-sdlc
```

Created: `AgentExecutionRole` (`sdlc-agent-AgentExecutionRole-Jr8zTuq4GRq5`), `OrchestratorLambda` + its role, `SchedulerRule` (EventBridge, `rate(5 minutes)`), `SchedulerInvokePermission`.

## 5. AgentCore Runtime

CloudFormation can't create this resource type; done via the `agentcore` CLI (`bedrock-agentcore-starter-toolkit` 0.3.14, installed in the project's `.venv`).

```
agentcore configure -e src/sdlc/agents/agentcore_app.py \
  -er arn:aws:iam::489675988515:role/sdlc-agent-AgentExecutionRole-Jr8zTuq4GRq5 \
  -n sdlc_orchestrator --non-interactive

agentcore launch -auc
```

`configure` succeeded: wrote `.bedrock_agentcore.yaml`, auto-selected `direct_code_deploy` (not a container build), auto-created an S3 bucket for CodeBuild sources (`bedrock-agentcore-codebuild-sources-489675988515-ap-southeast-2`).

`launch` got through memory creation (failed with `AccessDeniedException`, non-fatal — continued without memory, which this agent doesn't use) and the 50MB package upload, then failed at `CreateAgentRuntime` with `ServiceQuotaExceededException` — see the blocker below.

## 6. Quota blocker

```
aws service-quotas get-service-quota \
  --service-code bedrock-agentcore --quota-code L-F4575653 --region ap-southeast-2
aws service-quotas get-aws-default-service-quota \
  --service-code bedrock-agentcore --quota-code L-F4575653 --region ap-southeast-2
```

`Total Agents per Account` (quota `L-F4575653`) was **0** on this account, in every region checked (`ap-southeast-2`, `us-east-1`, `us-west-2`, `eu-west-1`, `eu-central-1`, `ap-southeast-1`, `ap-northeast-1`, `ap-south-1`) — not a region-specific issue. The AWS-wide default is 1,000, so `aws service-quotas request-service-quota-increase` refused a small ask (it requires a value above the default). No AWS Support API access either (Basic support plan).

**Resolved by requesting the increase through the Service Quotas console** (user action, 8 Oct 2026): `Total Agents per Account` → 1,000, status **Pending** AWS review.

## 7. Remaining steps (once the quota is approved)

- [ ] Re-run `agentcore launch -auc`
- [ ] Read the new runtime ARN from `.bedrock_agentcore.yaml` (`agents.sdlc_orchestrator.bedrock_agentcore.agent_arn`)
- [ ] Patch the Lambda's `SDLC__AWS__AGENT_RUNTIME_ARN` env var (merging with existing vars, not replacing them)
- [ ] Write `agent_runtime_arn` back into `config.yaml`
- [ ] Run `sdlc aws-sync` to render/ingest Confluence pages into the Knowledge Base
- [ ] Verify the EventBridge-scheduled Lambda runs end to end (`sdlc sync-progress` equivalent, on AWS)
