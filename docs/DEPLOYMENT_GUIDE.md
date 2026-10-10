# Deployment & Operations Guide

A from-scratch, step-by-step walkthrough for installing, deploying and running this data sdlc 
project, aimed at someone new to the repo. For *why* things work this way, see
[`../CLAUDE.md`](../CLAUDE.md) (architecture) and [`DECISIONS.md`](DECISIONS.md)
(decision log). For a short overview, see [`../README.md`](../README.md) — this guide
goes deeper and covers every variation (local vs. aws, which steps run automated vs.
manual, the separate Jira downloader pipeline).

Every command that touches a remote system (Confluence, Jira, AWS) defaults to
`--dry-run` and only prints what it would do. Run it once dry, read the output, then
re-run with `--no-dry-run` (or `--apply` for `workflow start`) to actually do it. There are 2 profiles this application can be run in : `local` and `aws`. All
sample commands are show below.

---

## 1. Prerequisites

| Requirement | Needed for |
|---|---|
| Python 3.13 + [uv](https://docs.astral.sh/uv/) | everything |
| AWS account with Bedrock access in `ap-southeast-2` (or another region that has the Claude models + Managed Knowledge Base support), credentials via `aws configure`/SSO | **both** profiles — `local` still uses Bedrock for the LLM and the Knowledge Base |
| Atlassian Cloud site (Confluence + Jira) with an API token ([id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)) | everything |
| `dot` binary (`brew install graphviz` / `apt install graphviz`) | only `sdlc seed`'s diagram generation |
| Docker | only `profile: aws`'s `agentcore launch` container build |

## 2. Install

```bash
git clone <this repo> && cd AgenticSDLC
uv sync
cp .env.example .env
```

Edit `.env`:

```bash
SDLC__ATLASSIAN__BASE_URL=https://your-domain.atlassian.net
SDLC__ATLASSIAN__EMAIL=you@example.com
SDLC__ATLASSIAN__API_TOKEN=your-atlassian-api-token
SDLC__CONFLUENCE__SPACE_KEY=DEMO
SDLC__JIRA__PROJECT_KEY=DEMO
SDLC__PROFILE=local          # or: aws
AWS_PROFILE=your-aws-profile
```

Any `config.yaml` key can be overridden by an env var of the form
`SDLC__SECTION__KEY` (see `.env.example`).

Verify the merged config resolves correctly (secrets are masked in the output):

```bash
uv run sdlc config show
```

## 3. One-time Atlassian setup

- **Confluence**: create a space (e.g. key `DEMO`).
- **Jira**: create a **team-managed** project with issue types Epic / Story /
  Sub-task and statuses To Do / In Progress / Done. If your instance's issue type or
  status names differ from `usecases/<name>/terminology.yaml`'s defaults, edit that
  use case's `terminology.yaml` — no code changes needed (`src/sdlc/cli.py`,
  `orchestrator.py` etc. only ever go through this mapping). Confirm your Sub-task
  issue type's real name via `GET /issue/createmeta?projectKeys=<KEY>` before relying
  on it — some instances name it `Subtask` with no hyphen.

## 4. One-time AWS setup (both profiles)

This section has two parts: deploying the shared Knowledge Base infra (always
needed), and choosing which LLM a local agent calls (where `local` has a choice of
Bedrock Claude or an external LLM, and `aws` does not — it always uses Bedrock's
Claude LLM).

### 4.1 Deploy the Knowledge Base infra

Both `local` and `aws` profiles retrieve from the same Bedrock Managed Knowledge
Base — there's no local vector store, so this step is required either way. Run it
once, regardless of which profile you'll use day to day:

```bash
uv run sdlc aws-deploy --dry-run      # preview
uv run sdlc aws-deploy --no-dry-run   # S3 bucket, KB service role (CloudFormation),
                                       # Managed Knowledge Base + data source (CLI step)
```

This writes `knowledge_base_id`, `data_source_id` and `kb_role_arn` back into
`config.yaml`. Everything `aws-deploy` creates is recorded in
`infra/aws/.ledger.json` so `aws-destroy` can reverse it later.

### 4.2 Choose which LLM a local agent uses

Any agent that needs an LLM (today: the analyst agent, which generates
`solution_requirements` plans; the same switch will cover the engineer/tester
agents once they exist) uses Bedrock Claude by default:

| | `profile: local` | `profile: aws` |
|---|---|---|
| Bedrock Claude | default | always (only option) |
| Your own Anthropic/Gemini key | opt in via `EXTERNAL_LLM_PROVIDER` | ignored, even if set |

**Default — Bedrock Claude.** Pick a model id (`ap-southeast-2` needs a
cross-region inference profile id, not a plain model id) and set it as
`aws.llm_model_id` in `config.yaml`:

```bash
aws bedrock list-inference-profiles --region ap-southeast-2
# set aws.llm_model_id in config.yaml to one of the returned Claude profile ids
```

> **Known gotcha**: `claude-sonnet-5-5` currently breaks plan generation (the
> installed `strands-agents` hardcodes a forced `tool_choice` that model rejects).
> Use `claude-sonnet-5` until this is fixed upstream — see `DECISIONS.md`.

**Opt-in override for `profile: local` — direct Anthropic/Gemini key.** Set
`EXTERNAL_LLM_PROVIDER` in `.env`:

```bash
# Anthropic directly
EXTERNAL_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-api-key
ANTHROPIC_MODEL_ID=claude-sonnet-5          # optional, this is the default

# or Gemini via Vertex AI (run `gcloud auth application-default login` once first)
EXTERNAL_LLM_PROVIDER=gemini
GOOGLE_CLOUD_PROJECT=your-gcp-project
GOOGLE_CLOUD_LOCATION=us-central1           # optional, default shown
GEMINI_MODEL_ID=gemini-3.8-flash            # optional, default shown
```

Setting `EXTERNAL_LLM_PROVIDER` under `profile: aws` has no effect — the deployed
AgentCore container (`agents/agentcore_app.py`) always builds its model from
`aws.llm_model_id` and never reads that env var.

Knowledge Base search always goes through Bedrock regardless of this choice — it
doesn't need Claude model access, so the override above only affects plan
generation.

---

## 5. Profile A: `local` (default) — run everything from your laptop

With `profile: local` (default in `config.yaml`), state lives in `.state/` and the
analyst agent + orchestrator run on your machine.

### 5.1 Seed the demo Confluence content

```bash
uv run sdlc seed --use-case demo_order_fulfilment
uv run sdlc seed --use-case demo_order_fulfilment --no-dry-run
```

Creates the demo DDS/TDS/etc. page tree + diagrams in Confluence and fills in
`usecases/demo_order_fulfilment/page_roles.yaml` with the real page ids. For your own
use case, skip `seed` and populate `usecases/<name>/page_roles.yaml` by hand with the
`page_id` of each role's real Confluence page (anchor resolution never auto-proceeds
on an unconfigured `page_id` — it raises and blocks the step with a Jira comment until
you set it).

### 5.2 Sync the knowledge pipeline

```bash
# download the Confluence tree into the local corpus (.data/corpus/)
uv run sdlc sync --root-page-id <usecase-root-page-id> --dry-run
uv run sdlc sync --root-page-id <usecase-root-page-id> --no-dry-run

# render pages to PDF, upload to S3, ingest into the Managed KB
uv run sdlc aws-sync --dry-run
uv run sdlc aws-sync --no-dry-run
```

### 5.3 Search the Knowledge Base (sanity check)

```bash
uv run sdlc search "on-time delivery"
```

### 5.4 Start the Jira workflow

```bash
# preview what would be created first
uv run sdlc workflow preview --use-case demo_order_fulfilment

# create one Epic + one Story per lifecycle step (see config/workflow.yaml)
uv run sdlc workflow start --use-case demo_order_fulfilment          # dry-run
uv run sdlc workflow start --use-case demo_order_fulfilment --apply  # creates real tickets
```

Variations:

```bash
# start from a later step only (earlier steps assumed already done elsewhere)
uv run sdlc workflow start --use-case demo_order_fulfilment --from data_design_solution --apply

# start only specific steps (comma-separated step ids from config/workflow.yaml)
uv run sdlc workflow start --use-case demo_order_fulfilment --steps data_contract,test_automation --apply
```

`workflow start` is idempotent — re-running it with the same selection won't create
duplicate tickets (stable marker labels).

### 5.5 Work the manual steps, then poll for progress

Lets say if the lifecycle steps (`stakeholder_requirements`, `scope`, `data_design_solution`,
`technical_design_solution`, etc.) are manually updated, i.e. a human attaches the artifact to the
Story/Sub-task and moves it to Done in Jira. Then, the next step —
`solution_requirements` — can be run as (`analyst.solution_requirements`) which runs the
analyst agent once its inputs (`data_design_solution`, `technical_design_solution`)
are Done.

Poll Jira so the orchestrator can roll up statuses, detect newly-ready steps, and run
automated ones:

```bash
uv run sdlc sync-progress --dry-run
uv run sdlc sync-progress --no-dry-run

# or loop it continuously at jira.poll_interval_seconds (default 300s)
uv run sdlc watch-progress --no-dry-run
```

When `solution_requirements` becomes ready, `sync-progress` runs the analyst agent
automatically, writing `workflow/<use_case>/plan.json` + `.md` and publishing a
Confluence page, then moves the ticket to an awaiting-approval state.

### 5.6 Review and approve the generated plan

Review the plan (Confluence page / `plan.json`), then on its Jira ticket add the
label:

- `sdlc-approved` → next `sync-progress` run turns the plan into real Story +
  Engineering/Testing Sub-task tickets.
- `sdlc-rejected` → the step is sent back for rework.

```bash
uv run sdlc sync-progress --no-dry-run
```

### 5.7 Manual escape hatches (testing without round-tripping through Jira)

```bash
# regenerate a plan directly, bypassing orchestrator readiness gating
uv run sdlc analyst-plan --use-case demo_order_fulfilment --step solution_requirements --dry-run
uv run sdlc analyst-plan --use-case demo_order_fulfilment --step solution_requirements --no-dry-run

# apply a stored plan.json directly, bypassing the sdlc-approved label check
uv run sdlc analyst-apply --use-case demo_order_fulfilment --step solution_requirements --dry-run
uv run sdlc analyst-apply --use-case demo_order_fulfilment --step solution_requirements --no-dry-run
```

---

## 6. Profile B: `aws` — agent on AgentCore, orchestrator on a schedule

Same knowledge pipeline and workflow commands as above — the only differences are
where state lives (S3 instead of `.state/`) and where the agent/orchestrator run.

### 6.1 Switch profile and deploy the agent infra

```yaml
# config.yaml
profile: aws
```

```bash
uv run sdlc aws-deploy --dry-run
uv run sdlc aws-deploy --no-dry-run
```

Because `profile: aws` is now set, this also builds/pushes the analyst agent's
container via the `agentcore` CLI toolkit (needs Docker running locally), creates the
AgentCore Runtime, and deploys the scheduled-orchestrator Lambda + its EventBridge
rule via CloudFormation (`infra/aws/templates/agent.yaml`). It writes
`agent_runtime_arn` back into `config.yaml`.

#### What `aws-deploy` actually does under the hood (profile: aws)

All of this is driven by `src/sdlc/aws/agent_deploy.py`, run right after the
storage/KB deploy:

1. **Package the orchestrator Lambda.** Zips a deliberately minimal subset of the
   codebase — `config`, `sync_once`, `wiring`, the Jira/S3/AgentCore adapters,
   the workflow registry/orchestrator — explicitly *excluding* Strands, bs4,
   WeasyPrint and Graphviz, since the Lambda never needs them. Uploads the zip to
   `s3://<bucket>/state/lambda/orchestrator.zip`.
2. **Deploy the CloudFormation stack** (`infra/aws/templates/agent.yaml`), which
   creates, in one stack:
   - `AgentExecutionRole` — the IAM role the AgentCore *container* assumes at
     runtime (ECR image pull, CloudWatch logs, X-Ray, `bedrock:InvokeModel`,
     `bedrock-agent-runtime:Retrieve` on the KB). Created first, specifically so
     the `agentcore` CLI step below can be handed a pre-existing least-privilege
     role instead of letting it auto-create a broader one.
   - `OrchestratorLambda` — the scheduled orchestrator function itself (handler
     `sdlc.aws.orchestrator_lambda.handler`), with env vars for the profile and
     Atlassian credentials. Its `AGENT_RUNTIME_ARN` env var starts out **blank**
     here — the Runtime doesn't exist yet.
   - `SchedulerRule` — an EventBridge rule (`rate(5 minutes)` by default,
     configurable) that invokes the Lambda, plus the matching
     `AWS::Lambda::Permission` letting `events.amazonaws.com` call it.
3. **Build and launch the container via the `agentcore` CLI** — this is the part
   CloudFormation can't do (it can't build/push Docker images):
   ```bash
   agentcore configure -e src/sdlc/agents/agentcore_app.py -er <AgentExecutionRoleArn>
   agentcore launch
   ```
   This builds the container from the `agentcore_app.py` entrypoint, pushes it to
   ECR (via a CodeBuild project `agentcore launch` creates behind the scenes), and
   creates the actual AgentCore Runtime resource.
4. **Close the loop.** The new Runtime's ARN is read back from the generated
   `.bedrock_agentcore.yaml`, then: written into `config.yaml` as
   `agent_runtime_arn`, *and* patched into the already-deployed Lambda's env vars
   via `update_function_configuration` — so the Lambda now knows which Runtime to
   call.

**Runtime flow once deployed:** `SchedulerRule` (EventBridge) fires on schedule →
`OrchestratorLambda` runs `sync_once()` → when a step becomes ready, it calls
`AgentCoreAgentRuntime.invoke_agent_runtime()` → that invokes the deployed container
(`agents/agentcore_app.py`), which runs the actual Strands analyst agent (Bedrock
Claude + `search_knowledge` against the KB) and returns the result back to the
Lambda.

### 6.2 Run the same workflow commands

`seed`, `sync`, `aws-sync`, `search`, `workflow preview`/`start`,
`analyst-plan`/`analyst-apply` all work identically to the local walkthrough above —
copy sections 5.1–5.4 and 5.7 as-is.

### 6.3 Skip `sync-progress`/`watch-progress`

The deployed Lambda (`aws/orchestrator_lambda.py`, a thin wrapper around
`sync_once()`) polls Jira on its own EventBridge schedule — default every 5 minutes,
configurable via `ScheduleExpression` in `infra/aws/templates/agent.yaml`. You don't
need to run `sync-progress`/`watch-progress` yourself; approvals (step 5.6) still
happen by adding the `sdlc-approved`/`sdlc-rejected` label in Jira, and the next
scheduled Lambda tick picks it up.

### 6.4 Tear down

```bash
uv run sdlc aws-destroy --dry-run
uv run sdlc aws-destroy --no-dry-run
```

Deletes exactly what `aws-deploy` recorded in `infra/aws/.ledger.json`, in reverse
order (storage stack, KB, agent stack, AgentCore runtime). Never run `agentcore
destroy` directly — it has a known bug that deletes externally-created IAM roles,
which would pull the CloudFormation-managed execution role out from under
CloudFormation.

**Known cleanup gap**: `aws-destroy` doesn't remove the ECR image/repo or CodeBuild
project that `agentcore launch` creates. Clean those up by hand:

```bash
aws ecr delete-repository --force --repository-name <name>
```

---

## 7. Optional: the Jira downloader (separate pipeline)

A second, fully independent knowledge pipeline that mirrors a Jira epic's own tree
(Epic → Stories → Sub-tasks) into Markdown for its own Knowledge Base — useful for
asking questions over delivered Jira history. It is **never** triggered by
`workflow start`, `sync-progress`/`watch-progress`, `aws-deploy`/`aws-sync`, or any
orchestrator path, and it is not wired into the analyst agent's `search_knowledge`
tool. Run it explicitly:

```bash
# one-time: create its own Managed Knowledge Base + data source
uv run sdlc jira-kb-create --dry-run
uv run sdlc jira-kb-create --no-dry-run

# download a Jira epic's tree into .data/corpus_jira/
uv run sdlc jira-sync --epic-key DEMO-1 --dry-run
uv run sdlc jira-sync --epic-key DEMO-1 --no-dry-run

# upload to S3 and ingest into the Jira KB
uv run sdlc jira-aws-sync --dry-run
uv run sdlc jira-aws-sync --no-dry-run

# search it
uv run sdlc jira-search "shipment delay root cause"
```

---

## 8. Running against your own use case (not the demo)

No code changes needed — add a sibling folder:

```
usecases/<your_use_case>/
  usecase.yaml        # use-case level config
  terminology.yaml     # your Jira issue type / status names, domain terms
  page_roles.yaml      # role -> Confluence page_id mapping (fill in by hand)
  templates/           # (optional) seed page templates, only needed if using `seed`
```

Set `use_case: <your_use_case>` in `config.yaml` (or pass `--use-case` per command),
point `page_roles.yaml` at your real DDS/TDS and other role pages' `page_id`s, and
re-run the same commands from section 5 or 6. `usecases/enterprise*/` is gitignored —
use that prefix for any real, non-public use case.

## 9. Tests

```bash
uv run pytest
```

Runs entirely against in-memory fakes (`src/sdlc/adapters/fake.py`) and mocked
Confluence/Jira/S3 calls — no real network or AWS account required.

## 10. Make targets

All commands above also have `make` shortcuts (still dry-run by default — pass
variables, not flags):

```bash
make setup
make seed
make sync ROOT_PAGE_ID=123456
make search QUERY="on-time delivery"
make workflow-preview USE_CASE=demo_order_fulfilment
make workflow-start USE_CASE=demo_order_fulfilment
make analyst-plan
make analyst-apply
make sync-progress
make watch-progress
make aws-deploy
make aws-sync
make aws-destroy
make jira-sync EPIC_KEY=DEMO-1
make jira-kb-create
make jira-aws-sync
make jira-search QUERY="..."
make test
```

## 11. Quick reference: command → artifact produced

| Command | Artifact / effect |
|---|---|
| `seed` | Demo Confluence page tree + diagrams |
| `sync` | Local corpus (`.data/corpus/`: `page.html`, `_attachments/`, `meta.json`) |
| `aws-sync` | PDFs in S3 + Managed KB ingestion job |
| `search` | Retrieved KB chunks (console output) |
| `workflow start` | Jira Epic + Story tickets per selected step |
| `sync-progress` / `watch-progress` | Status rollups, readiness checks, automated-step runs, approval checks |
| `analyst-plan` | `workflow/<use_case>/plan.json` + `.md`, published Confluence page |
| `analyst-apply` | Story + Engineering/Testing Sub-task tickets from a plan |
| `aws-deploy` | S3 bucket, KB, (if `profile: aws`) AgentCore Runtime + Lambda + EventBridge |
| `aws-destroy` | Reverses everything `aws-deploy` recorded |
| `jira-sync` | `.data/corpus_jira/` Markdown mirror of a Jira epic tree |
| `jira-aws-sync` | Jira-pipeline S3 objects + its own KB ingestion job |
