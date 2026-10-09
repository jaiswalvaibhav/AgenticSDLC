# CLAUDE.md

Autonomous Data SDLC: a Jira-driven orchestrator plus an analyst agent that turns Data Design Solution (DDS) and Technical Design Solution (TDS) pages into Jira work. The full brief is in `docs/BRIEF.md`.

**Status**: Phases 0–8 done (see `docs/BRIEF.md` "Phases" for the checklist).

## Ground rules
- Work in phases (see `docs/BRIEF.md`). Stop after each phase, summarise it in 2–3 sentences, and confirm before moving on.
- If anything is ambiguous, ask instead of guessing.
- Never invent AWS (Bedrock KB, AgentCore), Jira or Confluence API or CLI details. Check the current official docs first, and say what was verified.
- Write the minimal code that fully meets the requirement. No speculative abstractions.
- Reuse what is already in the project before writing anything new.
- When changing code, touch only the lines that need to change.
- This is a public repo. Keep it generic: no employer names, domains, space keys or real data. Use placeholders such as `your-domain.atlassian.net` and `SPACE_KEY`. `usecases/enterprise*/` is gitignored.
- Secrets come only from env vars or the gitignored `.env`. Never log or commit tokens.
- Every remote write (Confluence, Jira, AWS) supports `--dry-run`, and dry-run is the default (`--apply` to execute).

## Architecture
- **Ports** (`src/sdlc/ports.py`): DocumentSource, TicketSystem, StatusSource, ObjectStore, KnowledgeIndex, LLM and AgentRuntime. Each has a fake in `src/sdlc/adapters/fake.py` for tests.
- **Profiles** (`profile: local | aws`): both use Bedrock (Claude) and a Bedrock **Managed** Knowledge Base in `ap-southeast-2`. They differ as follows:
  - `local`: state lives in `.state/`, and the agent and orchestrator run on the laptop.
  - `aws`: state lives in `s3://<bucket>/state/` (`S3ObjectStore`), the agent runs on AgentCore (`AgentCoreAgentRuntime`, invoking it via `invoke_agent_runtime`), and the orchestrator runs on an EventBridge schedule (a Lambda — see "AWS infra" below). `src/sdlc/wiring.py` has the one `profile`-dispatch point for ObjectStore/AgentRuntime; both the CLI and the Lambda import from it.
- **Knowledge pipeline**:
  1. Confluence → `.data/corpus/` (`page.html`, `_attachments/`, `meta.json`; no `.md` files).
  2. WeasyPrint renders each page to `<pageId>.pdf`, written with `<pageId>.pdf.metadata.json` to S3.
  3. Ingestion into the Managed KB.
  4. Our own `search_knowledge` tool calls Retrieve with `managedSearchConfiguration` and a `use_case` filter.
- **Workflow**: `config/workflow.yaml` lists the lifecycle steps. `workflow start` creates one Jira epic per use case and one Story per selected step (idempotent via stable marker labels), linked with "Blocks". `orchestrator.handle_status_change()` is the one handler every status event goes through: rollup (Sub-task → Story → Epic), then — if the issue just reached Done and is a step — checks downstream steps' inputs (Done + has an artifact) and starts whichever became ready, running automated steps through `AgentRuntime` (`LocalAgentRuntime` for `profile: local`; AgentCore lands in Phase 7). `orchestrator.check_approvals()` is polled separately (a label add isn't a status change) for the `sdlc-approved`/`sdlc-rejected` labels, and calls `orchestrator.apply_plan()`, which turns a stored `plan.json` into real Story + Engineering/Testing Sub-task tickets.
- **Analyst agent** (`src/sdlc/agents/analyst/`): `anchors.py` resolves DDS/TDS via `page_roles.yaml` (page_id only — no silent title-match fallback, see Decisions) and reads them in full, section by section (split at h1–h3), with diagrams as image bytes; reads from the local corpus when synced, else live Confluence. `engine.py` runs a Strands `Agent` (Bedrock Claude) with our own `search_knowledge` tool, producing a Pydantic `SolutionRequirementsPlan` via `structured_output`, written to `workflow/<use_case>/plan.json`/`.md`. `tasks.py` is the task registry (`AnalystTask.run`); `agents/runtime.py`'s `LocalAgentRuntime` dispatches to it.
- **Use cases**: `usecases/<name>/` holds the config, terminology, page roles and templates for that use case. Adding a use case needs no code changes.
- **AWS infra** (`infra/aws/templates/`, `src/sdlc/aws/`): `storage.yaml` (Phase 4) is the S3 docs bucket + KB service role. `agent.yaml` (Phase 7) is the AgentCore execution role, the scheduled-orchestrator Lambda (`aws/orchestrator_lambda.py`, a thin wrapper around `sync_once()`) and its EventBridge schedule. `aws-deploy` runs `aws/deploy.py` then `aws/agent_deploy.py`: CloudFormation for everything CFN can do, then a CLI step for whatever it can't (the Managed KB; the AgentCore Runtime container+resource, via the `agentcore` CLI toolkit). Everything created is recorded in `infra/aws/.ledger.json`; `aws-destroy` reverses it.

## Commands
- `uv sync`: install dependencies.
- `uv run sdlc --help`: list all commands (`make <target>` wraps them).
- `uv run sdlc config show`: show the merged config, with secrets masked.
- `uv run sdlc workflow preview --use-case demo_order_fulfilment --from solution_requirements [--steps data_contract]`: preview the tickets that would be created.
- `uv run sdlc sync --root-page-id <id>`: download a Confluence tree into `.data/corpus/`.
- `uv run sdlc seed [--use-case demo_order_fulfilment]`: create the demo Confluence pages + diagrams (dry-run by default; `--no-dry-run` to actually create them — needs real Atlassian credentials in `.env` and a `dot` binary installed for the diagrams).
- `uv run sdlc aws-deploy`: deploy the storage stack (CloudFormation: S3 bucket + KB service role) and create the Managed Knowledge Base + data source (CLI step). Writes `knowledge_base_id`/`data_source_id`/`kb_role_arn` back into `config.yaml`.
- `uv run sdlc aws-sync`: render changed pages to PDF, sync them + `.metadata.json` to S3, run and poll a Knowledge Base ingestion job.
- `uv run sdlc search "<query>"`: Retrieve against the Managed KB, scoped to `use_case`.
- `uv run sdlc aws-destroy`: delete everything `aws-deploy` recorded in `infra/aws/.ledger.json`, in reverse order.
- `uv run sdlc workflow start --use-case demo_order_fulfilment [--from solution_requirements] [--apply]`: create the epic + step tickets.
- `uv run sdlc sync-progress` / `watch-progress`: one-shot / looping poll of Jira status changes → rollup + readiness + approval check.
- `uv run sdlc analyst-plan --use-case demo_order_fulfilment [--step solution_requirements]`: manually run the analyst task (needs `aws.llm_model_id` and a populated KB).
- `uv run sdlc analyst-apply --use-case demo_order_fulfilment [--step solution_requirements]`: manually apply a stored plan.json into Jira.
- `uv run pytest`: run the tests.

## Decisions (Phase 0)
- Python 3.13, uv, Typer CLI `sdlc`, Makefile.
- No local RAG and no vision model: the Managed KB parser reads the diagrams in PDFs. Anchor diagrams are sent to Claude as images.
- Managed KB with managed embedding. CloudFormation is used where it works; the KB and data source are created by a CLI step because of a reported CloudFormation schema issue. boto3 is allowed.
- No official Confluence Cloud PDF API exists, so PDFs are rendered with WeasyPrint.
- The Strands `retrieve` tool doesn't support Managed KBs, so we use our own tool.
- Jira:
  - issue types Epic/Story/Sub-task; statuses To Do/In Progress/Done
  - transitions looked up by name
  - components Engineering and Testing, with optional assignee IDs
- Rollup:
  - if any child is In Progress or Done, the parent moves to In Progress
  - if all children are Done, the parent moves to Done
  - optionally, a child reopening moves a Done parent back to In Progress, with a comment; if the workflow blocks that, it only comments
- Design pages are resolved by page ID first, then label, then title pattern. A Confluence link on a step ticket overrides all of these.
- Demo use case: "Order Fulfilment Performance". Graphviz is a dev-only dependency (lazily imported in `diagrams.py`, so it's never required outside `seed`).
- Confluence attachment upload has no v2 endpoint; `ConfluenceClient.add_attachment` uses the v1 `POST /wiki/rest/api/content/{id}/child/attachment` (multipart, `X-Atlassian-Token: nocheck`).
- Confluence sync scales to the enterprise space (~3000 docs) by design: `get_descendants` is metadata-only and `sync_tree` fetches full bodies only for new/changed pages, concurrently. Still open: verify the account's actual Confluence Cloud rate limits against the batch/worker sizes — see the docstrings in `src/sdlc/adapters/confluence.py` (`get_descendants`, `_PAGE_ID_BATCH`) and `src/sdlc/confluence_sync.py`.
- WeasyPrint is a core dependency (not dev-only): both profiles need `aws-sync` for Knowledge Base ingestion. It turned out to need no system libraries on this machine (pure-Python rendering worked without `brew install pango`) — if that's not true elsewhere, document the system requirement where it's actually needed.
- `aws_sync.py` gates PDF re-rendering on the Confluence page `version` already tracked by `confluence_sync` (not by re-rendering every page's PDF to hash-compare) — same scaling principle as the Confluence sync fix above.
- IAM for the KB service role is defined directly in the CloudFormation template (`infra/aws/templates/storage.yaml`), not as separate `infra/aws/policies/*.json` files — CloudFormation is the single source of truth for it, since BRIEF.md's original CLI-only IAM approach was superseded by the Phase 0 decision to prefer CloudFormation.
- `aws-deploy`/`aws-sync`/`aws-destroy` were tested dry-run against a real AWS account (list/describe calls only — no resources were created). boto3's SSO/"login" credential provider needed the `botocore[crt]` extra in this environment; added as a dependency since this is a real, not-invented, AWS SDK requirement.
- Jira search moved to `/rest/api/3/search/jql` (the old `/rest/api/3/search` is fully removed); it can't `expand=changelog`, so `JiraClient.status_changes_since` uses the dedicated `GET /issue/{key}/changelog` endpoint instead, called only for issues a cheap `updated >=` search already flagged as changed.
- **Not fully doc-verified this session** (the Atlassian docs pages kept truncating on fetch): the exact changelog response field names (`values`/`items`/`fromString`/`toString`) in `adapters/jira.py`. Used the long-standing, widely-documented Jira Cloud shape, flagged in the code — check it against a real response from your instance before relying on it.
- **Confirmed by user**: the "AgenticSDLC" Jira project is team-managed, so `orchestrator._rollup`'s uniform `parent = <key>` JQL is correct as-is.
- Strands Agents SDK (`strands-agents` on PyPI) for the real agent: `Agent(model=BedrockModel(...), tools=[...], system_prompt=...)`, our own `search_knowledge` `@tool` (not the built-in `retrieve` tool), and `agent.structured_output(PydanticModel, content_blocks)` for the plan — verified against strandsagents.com and the SDK's own search/PR history (the docs site 404'd on direct page fetches this session).
- `engine.run_solution_requirements` takes an injectable `agent` param (anything with `.structured_output(Model, content) -> Model`) so tests exercise anchor-reading/prompt-building/plan-writing without a real Bedrock call; when omitted it builds the real Strands `Agent`.
- `config.yaml`'s `aws.llm_model_id` is set to `apac.anthropic.claude-3-5-sonnet-20240620-v1:0` — the most capable of the two Claude cross-region inference profiles the user confirmed enabled on this account via `aws bedrock list-inference-profiles --region ap-southeast-2` (the other being `apac.anthropic.claude-3-sonnet-20240229-v1:0`). `ap-southeast-2` needs a cross-region profile, not a plain foundation-model id. Swap this for a newer `au.anthropic.*` Sonnet 4.5/Haiku 4.5 profile if one is enabled and preferred.
- Traceability is now in all three places `orchestrator.apply_plan` was meant to write it: `plan.json`, `workflow/<use_case>/traceability.json`, and the Jira issue property `sdlc.trace` (`TicketSystem.set_property`, `PUT /rest/api/3/issue/{key}/properties/{propertyKey}`) — stamped directly on each created Story so the back-trace is readable from Jira itself without the ObjectStore.
- Anchor resolution never auto-proceeds past an unconfigured `page_id`: if `page_roles.yaml` has no `page_id` for a role, `anchors.AnchorNotConfirmed` is raised (surfaced as a Jira comment, step stays blocked) even when a title-match candidate is found — per BRIEF.md, a human must set `page_id` to confirm it, not have the agent silently pick a page.

## Decisions (Phase 7 — AgentCore, scheduled orchestrator, destroy, Jira property)
User confirmed these before any code was written, per BRIEF.md's own instruction to check AgentCore resources/steps first:
- **AgentCore deploy**: the `agentcore` CLI toolkit (`agentcore configure -e <entrypoint> -er <role_arn>` + `agentcore launch`), not raw boto3 + manual ECR push. It builds+pushes the container and creates the Runtime; CloudFormation can't do either.
- **Secrets**: Atlassian credentials are **plaintext environment variables** on the AgentCore Runtime / Lambda (`create_agent_runtime`'s `environmentVariables`), not Secrets Manager. Simpler, at the cost of being visible via the control-plane API — the user's explicit choice.
- **Scheduler compute**: a Lambda on an EventBridge schedule, not always-on ECS/Fargate/EC2 — no standing cost, matches BRIEF.md's "same one-shot command on a schedule."
- **IaC split**: CloudFormation (`infra/aws/templates/agent.yaml`) owns the AgentExecutionRole, the Lambda, and the EventBridge rule+target — same CFN-owns-IAM pattern as Phase 4's `storage.yaml`. The AgentCore Runtime resource itself is created by the `agentcore` CLI step (`src/sdlc/aws/agent_deploy.py`), which is handed the CFN-created role ARN via `-er` rather than letting the toolkit auto-create its own role.
- **Lambda dependency footprint**: `wiring.py`'s `LocalAgentRuntime` import is lazy (inside `agent_runtime()`, not module scope) specifically so the Lambda's import chain never pulls in Strands/bs4/WeasyPrint/Graphviz — guarded by `tests/test_orchestrator_lambda.py`, which actually imports it in a subprocess and asserts none of those modules loaded.
- **`aws-destroy` does not call `agentcore destroy`**: that CLI command has a documented bug ([aws/bedrock-agentcore-starter-toolkit#438](https://github.com/aws/bedrock-agentcore-starter-toolkit/issues/438)) deleting externally-created IAM roles — which would delete our CFN-managed AgentExecutionRole out from under CloudFormation. Instead `destroy.py` calls `delete_agent_runtime(agentRuntimeId=...)` directly (boto3 `bedrock-agentcore-control`). **Known gap**: this doesn't clean up the ECR images/repo or CodeBuild project `agentcore launch` creates — those currently need manual cleanup (`aws ecr delete-repository --force ...`) until that's wired in.
- **Not run against real AWS this session** (would build+push a real container image and create billable resources without you present): `agent_deploy.py`'s `agentcore configure`/`launch` calls and `_read_agent_runtime_arn`'s parsing of `.bedrock_agentcore.yaml` — written per the verified CLI/API shapes, but the exact YAML key for the runtime ARN is my best read of the docs, not confirmed against a real file. Check it the first time this actually runs.
- `aws-deploy` now runs `aws/deploy.py` (storage + KB) then `aws/agent_deploy.py` (agent infra), reloading config between them so the agent stack sees the KB id just written.

## Decisions (Phase 8 — docs, mocked-call tests, cleanup)
- README.md now has both quick starts (local and aws), since Phase 7 made `aws` fully wired end-to-end (S3ObjectStore, AgentCoreAgentRuntime, the Lambda).
- Added mocked-call unit tests for the three adapters that talk to real services over HTTP/boto3 and had none yet: `JiraClient`, `ConfluenceClient` (`tests/_mock_http.py`'s `FakeSession`, no extra test dependency), and `S3ObjectStore` (`unittest.mock.patch("boto3.client")`). These pin the request/response shapes the docstrings document, including the ones flagged as not independently doc-verified.
- Writing `ConfluenceClient`'s mocked tests surfaced two real bugs, now fixed: `create_page` and `append_to_page` both did a read (`_space_id()` / fetch-current-page) *before* checking `dry_run`, even though dry-run's own message never used the result — so dry-run calls to those two methods made a real network call. Both now check `dry_run` first.
- Fixed a bug in the test helper itself while writing these: `FakeSession`'s first cut matched responses by substring-in-url, which is ambiguous when adapter URLs share long prefixes (e.g. `.../wiki/api/v2/pages` vs `.../wiki/api/v2/pages/1`); switched to suffix matching (`url.endswith(...)`), with longest-match as a tiebreaker.
- Removed two genuinely unused imports (`Path` in `engine.py`, `field` in `tests/_mock_http.py`), found via a small one-off AST script rather than adding a linter dependency for a one-time pass.
- Not added: a linter/formatter dependency (ruff etc.) — out of scope for "minimal code," and nothing in the brief asked for one.

## Decisions (local-only Anthropic API stopgap)
- While Bedrock Claude model access and AgentCore access are both still pending on the AWS
  account, `engine._build_real_agent` can call the Anthropic API directly instead of
  Bedrock: if `cfg["profile"] == "local"` and `ANTHROPIC_API_KEY` is set, it builds a Strands
  `AnthropicModel(client_args={"api_key": ...}, model_id=...)` (verified against the installed
  `strands-agents[anthropic]` extra's `strands/models/anthropic.py`) instead of `BedrockModel`.
  The `aws` profile (Lambda/AgentCore) never takes this branch regardless of the env var.
- `ANTHROPIC_MODEL_ID` env var selects the model, defaulting to `claude-sonnet-5` — chosen
  over Haiku because this task (multi-section DDS/TDS synthesis with section/requirement-id
  citations, feeding real Jira tickets) needs Sonnet-tier reasoning quality, matching the
  Sonnet-class model already used on the Bedrock path.
- **Not `claude-sonnet-5-5`**, despite it being the newer/cheaper Sonnet: verified live (a
  real `messages.create` call, user-approved) that it rejects forced `tool_choice`
  (`"tool_choice: type \"tool\" and \"any\" are not supported for this model."`), and the
  installed `strands-agents` 1.58.1's `AnthropicModel.structured_output()` hardcodes
  `tool_choice={"any": {}}` — so `claude-sonnet-5-5` breaks plan generation outright.
  `claude-sonnet-5` has no such restriction (also verified live). Revisit the default once
  Strands supports the newer structured-outputs API for this Claude generation, or ships an
  `AnthropicModel` fix.
- This is billed to the user's personal Anthropic API key, not AWS, so `_build_real_agent`
  calls `typer.confirm(..., abort=True)` immediately before constructing the `AnthropicModel`
  every time this path is taken — the user confirmed they want to approve each call, not just
  once per session.
- KB retrieval (`search_knowledge`) is unaffected: it's a separate Bedrock `Retrieve` call
  against the existing Managed KB/S3 bucket, which only needs AWS credentials with Bedrock
  Agent Runtime + S3 permissions — not Claude model access.
- Treat this as a temporary workaround to delete once Bedrock Claude model access lands, not
  a permanent second LLM provider path.
