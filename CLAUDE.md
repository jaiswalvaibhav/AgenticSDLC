# CLAUDE.md

Autonomous Data SDLC: a Jira-driven orchestrator plus an analyst agent that turns Data Design Solution (DDS) and Technical Design Solution (TDS) pages into Jira work. The full brief is in `docs/BRIEF.md`.

**Status**: Phases 0–8 done (see `docs/BRIEF.md` "Phases" for the checklist). The full dated decision log — what was verified against official docs, what the user confirmed directly, what's still unverified — lives in `docs/DECISIONS.md`.

## Ground rules
- If anything is ambiguous, ask instead of guessing.
- Never invent AWS (Bedrock KB, AgentCore), Jira or Confluence API or CLI details. Check the current official docs first, and say what was verified.
- Write the minimal code that fully meets the requirement. No speculative abstractions.
- Reuse what is already in the project before writing anything new.
- When changing code, touch only the lines that need to change.
- This is a public repo. Keep it generic: no employer names, domains, space keys or real data. Use placeholders such as `your-domain.atlassian.net` and `SPACE_KEY`. `usecases/enterprise*/` is gitignored.
- Secrets come only from env vars or the gitignored `.env`. Never log or commit tokens.
- Every remote write (Confluence, Jira, AWS) supports dry-run, and dry-run is the default. Most commands use `--no-dry-run` to execute; `workflow start` uses `--apply` instead (see Commands below for which).

## Architecture
- **Ports** (`src/sdlc/ports.py`): DocumentSource, TicketSystem, StatusSource, ObjectStore, KnowledgeIndex, LLM and AgentRuntime. Each has a fake in `src/sdlc/adapters/fake.py` for tests.
- **Profiles** (`profile: local | aws`): both use Bedrock (Claude) and a Bedrock **Managed** Knowledge Base in `ap-southeast-2`. They differ as follows:
  - `local`: state lives in `.state/`, and the agent and orchestrator run on the laptop.
  - `aws`: state lives in `s3://<bucket>/state/` (`S3ObjectStore`), the agent runs on AgentCore (`AgentCoreAgentRuntime`, invoking it via `invoke_agent_runtime`), and the orchestrator runs on an EventBridge schedule (a Lambda — see "AWS infra" below). `src/sdlc/wiring.py` has the one `profile`-dispatch point for ObjectStore/AgentRuntime; both the CLI and the Lambda import from it.
- **Knowledge pipeline**:
  1. Confluence → `.data/corpus/` (`page.html`, `_attachments/`, `meta.json`; no `.md` files).
  2. WeasyPrint renders each page to `<pageId>.pdf`, uploaded to S3 (use_case-scoped by its S3 key path — see Gotchas).
  3. Ingestion into the Managed KB.
  4. Our own `search_knowledge` tool calls Retrieve with `managedSearchConfiguration` and a `use_case` filter.
  - The Jira downloader (below) is a second, separate knowledge pipeline — its own KB, its own S3 prefix, explicit-trigger only.
- **Workflow**: `config/workflow.yaml` lists the lifecycle steps. `workflow start` creates one Jira epic per use case and one Story per selected step (idempotent via stable marker labels), linked with "Blocks". `orchestrator.handle_status_change()` is the one handler every status event goes through: rollup (Sub-task → Story → Epic), then — if the issue just reached Done and is a step — checks downstream steps' inputs (Done + has an artifact) and starts whichever became ready, running automated steps through `AgentRuntime` (`LocalAgentRuntime` for `profile: local`, AgentCore for `profile: aws` — see `wiring.agent_runtime`). `orchestrator.check_approvals()` is polled separately (a label add isn't a status change) for the `sdlc-approved`/`sdlc-rejected` labels, and calls `orchestrator.apply_plan()`, which turns a stored `plan.json` into real Story + Engineering/Testing Sub-task tickets.
- **Analyst agent** (`src/sdlc/agents/analyst/`): `anchors.py` resolves DDS/TDS via `page_roles.yaml` (page_id only — no silent title-match fallback, see Gotchas) and reads them in full, section by section (split at h1–h3), with diagrams as image bytes; reads from the local corpus when synced, else live Confluence. `engine.py` runs a Strands `Agent` (Bedrock Claude, or a local-only Anthropic/Gemini stopgap — see Gotchas) with our own `search_knowledge` tool, producing a Pydantic `SolutionRequirementsPlan` via `structured_output`, written to `workflow/<use_case>/plan.json`/`.md` and published as a Confluence page. `tasks.py` is the task registry (`AnalystTask.run`); `agents/runtime.py`'s `LocalAgentRuntime` dispatches to it.
- **Use cases**: `usecases/<name>/` holds the config, terminology, page roles and templates for that use case. Adding a use case needs no code changes.
- **AWS infra** (`infra/aws/templates/`, `src/sdlc/aws/`): `storage.yaml` is the S3 docs bucket + KB service role. `agent.yaml` is the AgentCore execution role, the scheduled-orchestrator Lambda (`aws/orchestrator_lambda.py`, a thin wrapper around `sync_once()`) and its EventBridge schedule. `aws-deploy` runs `aws/deploy.py` then `aws/agent_deploy.py`: CloudFormation for everything CFN can do, then a CLI step for whatever it can't (the Managed KB; the AgentCore Runtime container+resource, via the `agentcore` CLI toolkit). Everything created is recorded in `infra/aws/.ledger.json`; `aws-destroy` reverses it.
- **Jira downloader** (`jira_sync.py`, `jira_aws_sync.py`, `jira_citations.py`): a fully separate pipeline (own KB, own S3 prefix, own CLI commands) that mirrors a Jira epic's tree (`--epic-key` → Stories → Sub-tasks) into `.data/corpus_jira/` as Markdown, for its own Knowledge Base. Explicit-trigger only — never called by `workflow start`, `sync-progress`/`watch-progress`, `aws-deploy`/`aws-sync`, or any orchestrator path, and not wired into the analyst agent's `search_knowledge` tool. See `docs/DECISIONS.md` for the full design.

## Commands
- `uv sync`: install dependencies.
- `uv run sdlc --help`: list all commands (`make <target>` wraps the common ones).
- `uv run sdlc config show`: show the merged config, with secrets masked.
- `uv run sdlc workflow preview --use-case demo_order_fulfilment --from solution_requirements [--steps data_contract]`: preview the tickets that would be created.
- `uv run sdlc sync --root-page-id <id> [--no-dry-run]`: download a Confluence tree into `.data/corpus/`.
- `uv run sdlc seed [--use-case demo_order_fulfilment] [--no-dry-run]`: create the demo Confluence pages + diagrams — needs real Atlassian credentials in `.env` and a `dot` binary for the diagrams.
- `uv run sdlc aws-deploy [--no-dry-run]`: deploy the storage stack (CloudFormation: S3 bucket + KB service role), create the Managed Knowledge Base + data source, and (if `profile: aws`) the agent infra. Writes the resolved ids back into `config.yaml`.
- `uv run sdlc aws-sync [--no-dry-run]`: render changed pages to PDF, sync them to S3, run and poll a Knowledge Base ingestion job.
- `uv run sdlc search "<query>"`: Retrieve against the Managed KB, scoped to `use_case`.
- `uv run sdlc aws-destroy [--no-dry-run]`: delete everything `aws-deploy` recorded in `infra/aws/.ledger.json`, in reverse order.
- `uv run sdlc workflow start --use-case demo_order_fulfilment [--from solution_requirements] [--apply]`: create the epic + step tickets.
- `uv run sdlc sync-progress` / `watch-progress [--no-dry-run]`: one-shot / looping poll of Jira status changes → rollup + readiness + approval check.
- `uv run sdlc analyst-plan --use-case demo_order_fulfilment [--step solution_requirements] [--no-dry-run]`: manually run the analyst task (needs `aws.llm_model_id` and a populated KB).
- `uv run sdlc analyst-apply --use-case demo_order_fulfilment [--step solution_requirements] [--no-dry-run]`: manually apply a stored plan.json into Jira.
- `uv run sdlc jira-sync --epic-key <key> [--no-dry-run]`: download a Jira epic's tree into `.data/corpus_jira/` (separate pipeline — see Architecture).
- `uv run sdlc jira-kb-create [--no-dry-run]`: one-time setup of the Jira downloader's own Knowledge Base.
- `uv run sdlc jira-aws-sync [--no-dry-run]`: upload `.data/corpus_jira/` to S3 and run ingestion against the Jira KB.
- `uv run sdlc jira-search "<query>"`: Retrieve against the Jira KB.
- `uv run pytest`: run the tests.

## Gotchas / non-obvious constraints
- **`claude-sonnet-5-5` breaks plan generation**: it rejects forced `tool_choice`, which the installed `strands-agents`' `AnthropicModel.structured_output()` hardcodes. Use `claude-sonnet-5` (the current default) until Strands or Anthropic fixes this — see `docs/DECISIONS.md` for the live-verified detail.
- **Jira issue type names are per-instance**, not hardcoded — wired from `terminology.yaml`'s `issue_types` mapping. Check your own instance's real names (`GET /issue/createmeta`) before reusing this for a new use case; this instance's Sub-task type is named `"Subtask"` (no hyphen).
- **KB metadata sidecars (`.metadata.json`) don't get scanned** on this account/region — confirmed live, `numberOfMetadataDocumentsScanned` stays 0 under every connectorParameters shape tried. `use_case` scoping is done client-side by matching the S3 key path instead (see `bedrock_kb.py`, `aws_sync._pdf_key`).
- **Never run `agentcore destroy`**: it has a documented bug deleting externally-created IAM roles, which would delete our CloudFormation-managed AgentExecutionRole out from under CloudFormation ([toolkit#438](https://github.com/aws/bedrock-agentcore-starter-toolkit/issues/438)). `aws-destroy` calls `delete_agent_runtime` directly instead. Known gap: it doesn't clean up the ECR image/repo or CodeBuild project `agentcore launch` creates — clean those up manually.
- **`wiring.py`'s `LocalAgentRuntime` import is lazy** (inside `agent_runtime()`, not module scope) so the scheduled Lambda's import chain never pulls in Strands/bs4/WeasyPrint/Graphviz — guarded by `tests/test_orchestrator_lambda.py`.
- **Anchor resolution never auto-proceeds past an unconfigured `page_id`**: if `page_roles.yaml` has no `page_id` for a role, `anchors.AnchorNotConfirmed` is raised (surfaced as a Jira comment, step stays blocked) even when a title-match candidate is found — a human must set `page_id` to confirm it.
- **Local-only Anthropic/Gemini stopgap** (`profile: local` only, via `EXTERNAL_LLM_PROVIDER=anthropic|gemini`): lets a local agent (today: the analyst agent; the same switch will cover the engineer/tester agents once they exist) call those APIs directly instead of Bedrock while Bedrock Claude model access is pending. Billed to your own account, confirmed per call. Delete once Bedrock access lands — see `.env.example` and `docs/DECISIONS.md`.
- **Fields not independently doc-verified against a real response this session** (pinned by mocked tests, but check against your own instance): the Jira changelog shape (`values`/`items`/`fromString`/`toString`) and the Jira attachment shape (`fields.attachment[]`) in `adapters/jira.py`.
- **`agent_deploy.py`'s `agentcore configure`/`launch` calls haven't been run against real AWS**: written per the verified CLI/API shapes, but `_read_agent_runtime_arn`'s exact `.bedrock_agentcore.yaml` key is a best read of the docs, not confirmed against a real file. Check it the first time this actually runs.

See `docs/DECISIONS.md` for the full dated reasoning behind every decision above, plus everything else decided along the way (sprint placement, traceability, the Jira downloader's design, etc.).
