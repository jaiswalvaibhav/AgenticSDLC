# Autonomous Data SDLC

A Jira-driven workflow orchestrator plus an analyst agent that turns a Data Design
Solution (DDS) and a Technical Design Solution (TDS) in Confluence into Jira Epics,
Stories and Sub-tasks — with full traceability back to the source pages.

One codebase, selected by `profile: local | aws` in `config.yaml`. Both profiles use
AWS for Claude (Bedrock) and search (a Bedrock Managed Knowledge Base) — there's no
local RAG, vision model or vector store. They differ only in where state lives and
where the agent/orchestrator run:

| | `local` | `aws` |
|---|---|---|
| State | `.state/` folder | S3 |
| Analyst agent runs on | your laptop | AgentCore Runtime |
| Orchestrator runs via | `sync-progress` / `watch-progress` | a scheduled Lambda |

See [`CLAUDE.md`](CLAUDE.md) for the architecture, every command, and the decisions
made along the way (with what's verified against official docs vs. what still needs
your confirmation). [`docs/BRIEF.md`](docs/BRIEF.md) is the original project brief.

This is a public, generic codebase: no employer names, domains or real data anywhere.
Your own enterprise use case goes in a sibling `usecases/<name>/` folder (gitignored
under `usecases/enterprise*/`), with no code changes.

## Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- An AWS account with Bedrock access in `ap-southeast-2` (or another region with the
  Claude models and a Managed Knowledge Base available), and credentials configured
  (`aws configure` / SSO) — **both profiles need this**, even `local`
- An Atlassian Cloud site (Confluence + Jira), with an API token
  ([id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens))
- For seeding the demo diagrams: the `dot` binary (`brew install graphviz` /
  `apt install graphviz`) — everything else works without it
- For the `aws` profile only: Docker (for `agentcore launch`'s container build)

## Setup (both profiles)

```bash
git clone <this repo> && cd AgenticSDLC
uv sync
cp .env.example .env   # fill in your Atlassian base URL, email, API token
```

In Confluence, create a space (e.g. `DEMO`); in Jira, create a **team-managed**
project with Epic/Story/Sub-task and To Do/In Progress/Done (matches
`usecases/demo_order_fulfilment/terminology.yaml` — if your statuses or hierarchy
differ, see CLAUDE.md's rollup section). Put the space key and project key in `.env`:

```bash
SDLC__CONFLUENCE__SPACE_KEY=DEMO
SDLC__JIRA__PROJECT_KEY=DEMO
```

Both profiles need Bedrock + the Knowledge Base, so **run this once regardless of
profile**:

```bash
uv run sdlc aws-deploy --no-dry-run   # S3 bucket, Managed KB, and (aws profile only
                                       # in effect) the agent infra — see below
```

This writes `knowledge_base_id`, `data_source_id` and `kb_role_arn` back into
`config.yaml`. Then find and set a Claude model for the agent — `ap-southeast-2`
needs a cross-region inference profile, not a plain model id:

```bash
aws bedrock list-inference-profiles --region ap-southeast-2
# set aws.llm_model_id in config.yaml to one of the Claude profile ids returned
```

## Local quick start (`profile: local`, the default)

```bash
# 1. Seed the demo Confluence pages + diagrams (dry-run first, then for real)
uv run sdlc seed --use-case demo_order_fulfilment
uv run sdlc seed --use-case demo_order_fulfilment --no-dry-run
# -> fills in usecases/demo_order_fulfilment/page_roles.yaml with real page ids

# 2. Sync the pages into the local corpus (.data/corpus/), then into the KB
uv run sdlc sync --root-page-id <usecase-root-page-id> --no-dry-run
uv run sdlc aws-sync --no-dry-run   # renders PDFs, uploads to S3, runs KB ingestion

# 3. Search the Knowledge Base
uv run sdlc search "on-time delivery"

# 4. Start the workflow: one epic + step tickets under it
uv run sdlc workflow start --use-case demo_order_fulfilment --no-dry-run
#    ...work the manual steps in Jira (attach artifacts, mark Done)...

# 5. Poll Jira for progress: rollup, step readiness, running the analyst agent
#    on automated steps, and checking the sdlc-approved/-rejected labels
uv run sdlc sync-progress --no-dry-run
#    or loop it: uv run sdlc watch-progress --no-dry-run

# 6. Once Solution Requirements is awaiting approval, add the `sdlc-approved`
#    label to its ticket, then run sync-progress again to apply the plan
```

`analyst-plan`/`analyst-apply` are manual escape hatches around steps 5–6, for
testing without round-tripping through Jira labels:

```bash
uv run sdlc analyst-plan --use-case demo_order_fulfilment --step solution_requirements --no-dry-run
uv run sdlc analyst-apply --use-case demo_order_fulfilment --step solution_requirements --no-dry-run
```

### No Bedrock Claude model access yet?

The analyst agent normally calls Claude through Bedrock. If Bedrock model access isn't
granted yet on your AWS account, set `ANALYST_LLM_PROVIDER` to `anthropic` or `gemini` in
`.env` — `profile: local` only, see `.env.example`:

- `anthropic`: set `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_MODEL_ID`, default
  `claude-sonnet-5`). The agent calls the Anthropic API directly, billed to your own key.
- `gemini`: run `gcloud auth application-default login` once, then set
  `GOOGLE_CLOUD_PROJECT` (and optionally `GOOGLE_CLOUD_LOCATION`, default `us-central1`,
  and `GEMINI_MODEL_ID`, default `gemini-3.8-flash`). The agent calls Gemini via Vertex AI
  using those Application Default Credentials — no API key needed, billed to that GCP
  project.

Either way you're asked to confirm before every call since it's billed to your own
account, not AWS. Knowledge Base search is unaffected (it's a separate Bedrock call that
doesn't need Claude model access). Remove `ANALYST_LLM_PROVIDER` once Bedrock access
lands.

## AWS quick start (`profile: aws`)

Set `profile: aws` in `config.yaml`, then deploy the agent infra (the AgentCore
Runtime + the scheduled-orchestrator Lambda + its EventBridge schedule):

```bash
uv run sdlc aws-deploy --no-dry-run
```

This builds and pushes the analyst agent's container via the `agentcore` CLI toolkit
(needs Docker), creates the AgentCore Runtime, deploys the Lambda + EventBridge rule
via CloudFormation, and writes `agent_runtime_arn` back into `config.yaml`.

Everything else is the same commands as the local quick start above — `seed`, `sync`,
`aws-sync`, `search`, `workflow start`, `analyst-plan`/`analyst-apply` all work
identically. The one thing you *don't* need to run is `sync-progress`/`watch-progress`
— the deployed Lambda does that on its own schedule now (default every 5 minutes; see
`ScheduleExpression` in `infra/aws/templates/agent.yaml`).

To tear everything down (this is a time-boxed, free-plan account — one command
matters):

```bash
uv run sdlc aws-destroy --no-dry-run
```

This deletes exactly what `aws-deploy` recorded in `infra/aws/.ledger.json`, in
reverse order. **Known gap**: it doesn't delete the ECR image/repo or CodeBuild
project `agentcore launch` creates — clean those up manually
(`aws ecr delete-repository --force --repository-name <name>`) until that's wired in.

## Everything is dry-run by default

Every command that creates or changes a remote resource (Confluence, Jira, AWS)
defaults to `--dry-run` and prints what it would do. Pass `--no-dry-run` (or `--apply`
for `workflow start`) to actually do it.

## Tests

```bash
uv run pytest
```

Tests run against in-memory fakes (`src/sdlc/adapters/fake.py`) and mocked
Confluence/Jira/S3 calls — no real network or AWS account needed.

## Project layout

```
src/sdlc/            the package: config, ports, adapters, workflow, analyst agent, aws/
config.yaml          generic config (profile, region, ids — no secrets)
.env.example          → .env (gitignored): Atlassian credentials
config/workflow.yaml  the lifecycle step registry
usecases/<name>/      per-use-case terminology, page roles, seed templates
infra/aws/templates/  CloudFormation (storage.yaml, agent.yaml)
docs/BRIEF.md         the original project brief
CLAUDE.md             architecture, every command, and the decisions made
```
