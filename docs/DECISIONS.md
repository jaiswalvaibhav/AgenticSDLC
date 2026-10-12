# Decisions

Dated decision log for the Autonomous Data SDLC project, moved out of CLAUDE.md to keep that file short — CLAUDE.md links here. Each phase/topic's decisions are recorded as they were made, including what was verified against official docs vs. what the user confirmed directly vs. what's still flagged as unverified.

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
  account, `engine._build_real_agent` can call the Anthropic or Gemini APIs directly instead
  of Bedrock, for `profile: local` only: set `ANALYST_LLM_PROVIDER=anthropic` or `=gemini`
  (unset, or any value when `profile: aws`, keeps the Bedrock path). The `aws` profile
  (Lambda/AgentCore) never takes either branch regardless of the env var.
  - `anthropic`: builds a Strands `AnthropicModel(client_args={"api_key": ...}, model_id=...)`
    from `ANTHROPIC_API_KEY` (verified against the installed `strands-agents[anthropic]`
    extra's `strands/models/anthropic.py`) instead of `BedrockModel`.
  - `gemini`: builds a Strands `GeminiModel(client_args={"vertexai": True, "project": ...,
    "location": ...}, model_id=...)` (verified against the installed `strands-agents[gemini]`
    extra's `strands/models/gemini.py`), authenticating via Application Default Credentials —
    run `gcloud auth application-default login` once in the terminal, then set
    `GOOGLE_CLOUD_PROJECT` (required) and optionally `GOOGLE_CLOUD_LOCATION` (default
    `us-central1`). No API key is used. `GEMINI_MODEL_ID` default `gemini-3.8-flash` is the
    user's own pick, not independently doc-verified against ai.google.dev this session —
    override it if that id is wrong/unavailable on your project.
  - Both are billed to the user's own account (Anthropic key / GCP project), not AWS, so
    `_build_local_llm_model` calls `typer.confirm(..., abort=True)` before constructing either
    model, every time — the user confirmed they want to approve each call, not just once per
    session.
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
- KB retrieval (`search_knowledge`) is unaffected: it's a separate Bedrock `Retrieve` call
  against the existing Managed KB/S3 bucket, which only needs AWS credentials with Bedrock
  Agent Runtime + S3 permissions — not Claude model access.
- Treat this as a temporary workaround to delete once Bedrock Claude model access lands, not
  a permanent second LLM provider path.

## Decisions (env var rename: `ANALYST_LLM_PROVIDER` → `EXTERNAL_LLM_PROVIDER`)
- Renamed per the user's request: the switch was never analyst-specific in behaviour — it
  only happened to live in `engine.py` because the analyst agent is the only one implemented
  so far. The engineer/tester agents (`agents/engineering/`, `agents/tester/` — stubs only)
  are expected to reuse the same local-only Anthropic/Gemini stopgap once built, so the name
  shouldn't imply it's scoped to one agent. Updated the env var itself (`engine.py`,
  `.env.example`), and the wording in `CLAUDE.md`/`README.md`/`docs/DEPLOYMENT_GUIDE.md`
  that said "the analyst agent" where it meant "whichever local agent is calling an LLM."
  Left the rest of this file's earlier entries as-written (dated history), rather than
  rewriting past references to the old name.

## Decisions (sprint placement + status transitions)
- **Confluence publishing of the solution requirements plan** (`engine._publish_plan_page`):
  besides `plan.json`/`plan.md` in the `ObjectStore` (`.state/` for `local`, S3 for `aws`), the
  full plan (not just the summary that goes in the Jira comment) is published as a Confluence
  page titled `"<N>. Solution Requirements"` under the use case root — same numbering
  convention as the other anchor pages (`N = len(non-root page_roles) + 1`) — and the step
  ticket gets a `TicketSystem.add_remote_link` to it. `DocumentSource` is Confluence in both
  profiles, so no profile-specific wiring was needed.
- **Jira issue type names are per-instance, not hardcoded**: `orchestrator.py` used to pass
  literal `"Epic"`/`"Story"`/`"Sub-task"` to `create_issue`, ignoring the `issue_types` mapping
  already defined in `terminology.yaml` for exactly this. On this Jira Cloud site the real
  Sub-task issue type is named `"Subtask"` (no hyphen) — confirmed via
  `GET /issue/createmeta?projectKeys=SCRUM` — so the literal `"Sub-task"` 400'd on every
  `create_issue` call. Fixed by wiring `issue_types` from `terminology.yaml` into all three
  `create_issue` call sites (`workflow_start`'s Epic/Story, `apply_plan`'s Story/Sub-task), and
  correcting this use case's `terminology.yaml` value. Check your own instance's real issue
  type names the same way before reusing this for a new use case.
- **New Stories go into the next unstarted sprint, not the backlog and not the active
  sprint** — the user's explicit choice. `TicketSystem.get_or_create_future_sprint` /
  `add_issues_to_sprint` (new port methods) use the separate Jira Software "Agile" REST root
  (`/rest/agile/1.0`, not `/rest/api/3`) — verified live against a real Jira Cloud site (Oct
  2026): `GET /board?projectKeyOrId=...` for the board, `GET /board/{id}/sprint?state=future`
  for a not-yet-started sprint (created via `POST /sprint {name, originBoardId}` if none
  exists), `POST /sprint/{id}/issue {"issues": [...]}` to move issues out of the backlog. Wired
  into both `workflow_start` (step Stories) and `apply_plan` (requirement Stories) — only the
  newly-created keys each call, not ones found via the idempotency marker.
- **`apply_plan` never jumps straight To Do -> Done** — the user's explicit choice, since the
  normal orchestrator-driven flow already passes through In Progress (`_start_step` does that
  before the agent runs) and a manually-triggered `apply_plan`/`analyst-apply` should look the
  same in the issue's history. It now checks the issue's current status and inserts a
  To Do -> In Progress transition first if needed, wrapped in the same `TransitionNotAvailable`
  fallback-to-comment pattern `_start_step`/`_rollup` already use.

## Decisions (Jira downloader — separate pipeline, explicit-trigger only)
- **Fully separate from the existing workflow**, per the user's explicit requirement: its own
  Knowledge Base (`aws.jira_knowledge_base_id`/`jira_data_source_id`, created by the new
  standalone `jira-kb-create` command, reusing the storage stack's bucket + KB service role —
  no new CloudFormation), its own S3 prefix (`aws.jira_docs_prefix`), and its own CLI commands
  (`jira-sync`, `jira-aws-sync`, `jira-search`). None of these are called by `workflow start`,
  `sync-progress`/`watch-progress`, `aws-deploy`, `aws-sync`, or any `orchestrator.py` code
  path — confirmed by grep, no references exist outside `cli.py` and the new modules
  themselves.
- **Markdown, not PDF**, for `issue.md`: Bedrock's S3 data source ingests `.md` directly
  (verified live, Oct 2026), and unlike Confluence pages, Jira issues have no embedded-diagram
  requirement forcing an HTML->PDF render step — attachments are uploaded as their own
  separate S3 objects regardless. No WeasyPrint dependency on this path.
- **Local corpus mirrors Jira's real hierarchy**, per the user's requirement: scope is
  `--epic-key` (required, no auto-derivation, no interactive prompt) -> its Stories (`parent =
  <epicKey>`) -> each Story's Sub-tasks (`parent = <storyKey>`), using the real `parent` links
  `workflow_start`/`apply_plan` already set. Layout:
  `.data/corpus_jira/<epicKey>/[<storyKey>/[<subtaskKey>/]]` with `issue.md` and a per-ticket
  `_attachments/<file>` subfolder (same convention as Confluence's corpus) in each ticket's
  own directory.
- **`meta.json` is one file per epic tree, not one per ticket** — the user's explicit
  correction after the first pass wrote one per ticket. It lives only at the epic root
  (`.data/corpus_jira/<epicKey>/meta.json`) as `{"epicKey": ..., "issues": {issueKey: {
  issueType, status, updated, summary, attachments, path}}}`, where `path` is the ticket's
  directory relative to the epic root (`"."` for the epic itself, `"<story>"`, `"<story>/
  <subtask>"`). `jira_sync.sync_issues` rebuilds it every run by merging the previous file
  (kept for tickets that were unchanged or still failing) with this run's freshly-fetched
  tickets, so a partial run never drops an entry it didn't need to touch. `jira_aws_sync.py`
  walks each epic's single `meta.json` to find every ticket's directory via its `path`, rather
  than globbing for per-ticket `meta.json` files.
- **Attachment handling**: Bedrock's S3 data source natively ingests `.txt/.md/.html/
  .doc/.docx/.csv/.xls/.xlsx/.pdf` and (via multimodal parsing) `.jpeg/.jpg/.png` — verified
  live, Oct 2026 — with size limits of 3.75MB for images and 50MB for everything else. An
  attachment outside that extension set or over its size limit, or one that errors on
  download, is never written to disk; instead it's logged (`print("[error] ...")`) and noted
  in `issue.md`'s own text (`> Attachment not downloaded: <file> (<reason>)`) plus `meta.json`
  (`status: "skipped_unsupported_type" | "skipped_too_large" | "failed_download"`) — so its
  existence is still discoverable even though it's never ingested. The user's explicit choice
  over building a format-conversion pipeline (e.g. LibreOffice for pptx->pdf).
- **Download trace + retry, for both Jira and Confluence** — the user's explicit requirement.
  `jira_sync.py` keeps `jira_trace.json` (ObjectStore, keyed by issue key:
  `status/error/last_attempt/last_success_updated`); an issue is re-fetched when new, its
  `updated` changed since the last successful fetch, or its last recorded status was
  `"failed"`. The same gap existed in `confluence_sync.py` — it had no success/failure record
  at all, and `list(pool.map(...))` meant the *first* page's exception aborted the entire
  batch with no partial progress — so the identical trace/retry pattern (`confluence_trace.json`,
  per-page try/except instead of letting `pool.map` propagate) was added there too.
- **`jira-aws-sync` has its own upload trace** (`jira_upload_trace.json`), separate from
  `jira_sync.py`'s download trace: an S3 upload can fail for reasons unrelated to the Jira
  download (e.g. a transient S3 error), so it needs independent retry bookkeeping, gated on
  whether the last successful upload's `uploaded_updated` matches the download trace's current
  `last_success_updated` for that issue.
- **Traceability back to Jira**: `Chunk.s3_uri`/`location.s3Location.uri` is reliable (unlike
  the `.metadata.json` sidecar, confirmed non-functional in this account per `bedrock_kb.py`'s
  own docstring). The issue key is encoded directly in the S3 key path
  (`.../jira/<epicKey>/[.../<storyKey>/[.../<subtaskKey>/]]...`), and `jira_citations.
  jira_url_from_s3_uri` turns a hit's `s3_uri` back into a `<base_url>/browse/<key>` link —
  used by the new `jira-search` CLI command. Per-attachment upload (rather than embedding into
  one PDF) is a deliberate precision gain: a KB hit on an attachment resolves to exactly that
  file and its issue, not just "somewhere in this issue."
- **Not wired into the analyst agent's `search_knowledge` tool** this round — `jira-search` is
  a separate, manual CLI command for proving the Jira KB out first. Wiring a second
  `KnowledgeIndex` into the analyst's tool set is a deliberate follow-up, not done here.
- **jira.py's attachment shape (`fields.attachment[]`: id/filename/mimeType/size/content) is
  not independently doc-verified this session** — same caveat as the existing changelog shape
  — it's the long-standing documented Jira Cloud shape, pinned by mocked tests, but check it
  against a real response from your instance before relying on it.


## Decisions (anchors read from Jira step tickets, any supported format — 2026-10-11)
- **Why**: in the real workflow the DDS may be an Excel file and the TDS a Confluence page, attached per ticket — not fixed once per use case in `page_roles.yaml`. User-confirmed design.
- **Resolution**: `ticket_anchors.pick_anchor_source` reads the step ticket (found through `workflow/<uc>/steps.json`) of each `AnalystTask.anchor_roles` entry. Exactly one Confluence link (preferred) or exactly one supported attachment; anything else raises `AnchorMissing` → comment on the triggering ticket, step stays blocked. No `page_roles.yaml` fallback (user-confirmed). `page_roles.yaml` still seeds the demo tree, numbers the plan page and gives `usecase_root`.
- **Generic over tickets and formats** (user chose "Option 2"): the engine loops over `anchor_roles`, labelled from `terminology.yaml`; a future task only declares its anchor steps.
- **Done stays human.** An upload isn't sign-off and fires no status event. When a step that feeds an automated step is marked Done, `orchestrator._check_anchor_artifact` comments on it if it has no usable artifact (asks to reopen, attach, re-mark Done). Driven by `workflow.yaml`'s `automation`/`inputs`, so it covers future automated steps.
- **Formats**: Confluence → existing reader. xlsx/xlsm → openpyxl `read_only`, one CSV section per sheet (sheet name = `source_section`). docx → python-docx, split at Heading 1–3, tables as ` | ` rows, images kept. csv → text. pdf → native Converse document block (works on Bedrock and the Anthropic stopgap). xlsx/docx are converted rather than sent natively because the installed Strands `AnthropicModel` raises TypeError on them (only pdf/csv/html/md/txt are accepted; `strands/models/anthropic.py:70`). `.xls`/`.doc` unsupported.
- **Not truncating**: above `analyst.max_anchor_chars` (default 400000) or `max_pdf_bytes` (default 4500000 — placeholder, set to Bedrock's documented per-document limit) the step blocks with a per-section size breakdown. Split-and-merge (one LLM call per sheet group) is deferred until a real DDS exceeds the budget.
- **Still search-free**: anchors are read in full, never via `search_knowledge` — traceability (`source_doc`/`source_section`) needs the complete, ordered source.
- **Unverified**: the Jira remote-link list response shape (Atlassian docs page truncated when fetched) — pinned by a mocked test only. Tracked as an open item in `docs/DEPLOYMENT_GUIDE.md` §5.5 (with the check to run and what to update once confirmed).


## Decisions (workflow.yaml renames — 2026-10-11)
- **Step id vs task id are now different things.** The step is `develop_solution_requirements`; the analyst *task* stays `solution_requirements` (`AnalystTask.id`, the `terminology.yaml` term and the published plan page title are the output concept, unchanged). `tasks.resolve_task` accepts either the task id or a workflow `automation` reference (`analyst.<task id>`); the local runtime, the AgentCore entrypoint and the CLI all use it. Before this, `LocalAgentRuntime.run("analyst.solution_requirements")` — what the orchestrator passes — raised "no runnable analyst task" (hidden by fake runtimes in tests).
- **CLI**: `analyst-plan`/`analyst-apply --step` now takes a *workflow step id* (default `develop_solution_requirements`); the task is found through that step's `automation`.
- **workflow.yaml**: `test_case_specification` and `data_solution_development` listed `solution_requirements`, which no longer exists as a step; both now list `develop_solution_requirements`. `technical_design_solution` now also needs `data_design_solution`, so the DDS is Done before the TDS starts.
- **`data_contract` is a required step** (user-confirmed; `optional: true` removed), an input of `test_solution_report`. It is selected by default and by `--from develop_solution_requirements`, so the tail of the chain is no longer dropped. The registry's `optional` flag is kept but unused today.
- **terminology.yaml**: added `develop_solution_requirements: Develop Solution Requirements`; kept `solution_requirements`, which titles the published plan page and is the analyst task's output. Step Story titles come from `workflow.yaml`'s `artifact`, not terminology.


## Decisions (workflow UI: FastAPI backend + React UI — 2026-10-12)
User-confirmed choices:
- **Selection**: ticked steps plus their *direct* inputs as artifact-only ("provide"); no downstream expansion (`registry.resolve_targets`, `workflow_start(targets=…)`). Different from `--from`, which also pulls in every reachable downstream step.
- **Uploads and approval stay in Jira**; the UI is read-only for them and links to each ticket.
- **Stack/layout**: FastAPI backend in `backend/sdlc_api/`, React + Vite + TypeScript in `ui/`, separate modules; the backend imports `sdlc` and adds no business logic. `fastapi`/`uvicorn` are in a new `ui` uv group (also in `dev`), never in the Lambda/AgentCore dependencies.
- **Writes**: preview first (`/api/preview`, no Jira calls), then a confirmed live trigger (`dry_run=False`); the dialog says so.
- **External LLM stopgap**: consent is collected once at trigger (`cfg["external_llm_confirmed"]`) because `typer.confirm` can't prompt in a background poller; the CLI is unchanged.

Implementation choices:
- **Poller** (`poller.py`): one daemon thread running `sync_once(dry_run=False)` then a snapshot rebuild per cycle, one lock so cycles never overlap, errors recorded not fatal. Local profile only. One use case at a time. Interval `ui.poll_interval_seconds` (30), deliberately faster than the CLI's `jira.poll_interval_seconds` (300).
- **Resume**: `ui/poller.json` (in the local store) records what was started; server shutdown halts the thread but leaves it active, the user's Stop marks it inactive. It includes the external-LLM consent (see CLAUDE.md gotcha).
- **Progress snapshot** is cached in memory and rebuilt each poller cycle or on demand — it costs ~2 Jira calls per step, too much to do on every 5 s UI refresh. `ui/<use_case>/selection.json` records which steps were ticked vs added as inputs (not derivable from `steps.json`).
- **First-poll baseline**: `PollingStatusSource` only records a baseline on its first run; the UI surfaces that as a note when no checkpoint exists.
- **Unverified**: the UI has been type-checked and built (`npm run build`) and its Vite proxy smoke-tested against the real backend, but not exercised in a browser or against live Jira at the time of writing; the Jira remote-link response shape it relies on is still an open item (DEPLOYMENT_GUIDE §5.5).

- **Saved run vs. Jira project (user-confirmed, 2026-10-12)**: the Progress table is built from the saved `workflow/<uc>/steps.json`, so after changing `jira.project_key` it kept showing the old project's tickets, and `workflow_start` would have skipped creating any step already in that file. Chosen: the UI keeps showing the saved run, **labelled** (project, ticket count, start time; a run saved before projects were recorded has its project inferred from its ticket keys), with an amber warning when it isn't in the configured project, and a separate **Start fresh** button (`POST /api/runs/{use_case}/reset`, confirmation in the UI). Reset deletes the run's saved state (steps map, run state, plan, traceability, selection) and stops the poller if it is polling that use case; it never touches Jira or the Jira status checkpoint. Not chosen: resetting on every trigger (would drop an in-flight run, e.g. a plan awaiting approval) and silently ignoring other-project state.

## Decisions (poller replayed old status events every cycle — 2026-10-12)
- **Bug**: `JiraClient.status_changes_since` compared the changelog's `created` to the checkpoint as *strings*. Jira writes `created` in the site's UTC offset (`…T10:35:37.421+0530`); the checkpoint is UTC (`…T05:09:00+00:00`). With a positive offset every old entry looked newer, so every past status change was replayed on every poll for hours. Each replayed Done also re-posted the "Marked Done but no usable artifact" comment, which bumped the ticket's `updated` time, which put it back in the next poll's window — a self-feeding loop (a comment every ~30 s). Any other replayed event (rollup, step starts) was affected the same way. Found with the live changelog of a real ticket; the old mocked test used the same offset on both sides, so it never saw it.
- **Fix**: parse both into timezone-aware datetimes before comparing (`datetime.fromisoformat` handles `+0530` and `Z`); entries with no `created` are skipped. Tests use mixed offsets. Verified against the live ticket: no replay with the current checkpoint.
- **Changelog shape now verified live** (`values[].created` / `items[].field|fromString|toString`, `isLast`).
- **Known, not fixed**: `PollingStatusSource` formats the JQL `updated >=` literal in UTC, but JQL reads it in the *Jira user's* timezone. East of UTC that only over-fetches (harmless); west of UTC the window would start too late and could miss changes. Fix by converting to the user's timezone (`GET /myself` → `timeZone`) if this is ever run from such an account.

- **Anchor links pasted into comments (user-requested, 2026-10-12)**: the user attached the TDS as a Confluence URL typed into a comment on the step ticket and the app said "no usable artifact" — it only looked at Web links (remote links) and attachments. Now `ticket_anchors.pick_anchor_source` has a third, lowest-priority tier: with no Web link and no readable attachment, exactly one distinct Confluence page URL (`/wiki/.../pages/<id>`, deduplicated by page id) in the description or comments is used; several distinct pages → `AnchorMissing` naming them and asking for a Web link; non-Confluence URLs are ignored. The orchestrator's readiness check (`_inputs_satisfied`) and the "marked Done but no usable artifact" check use the same rule, so a ticket with only a comment link both passes the check and starts the next step. `TicketSystem.get_text_links` (new port method) walks the ADF of the description and comments (`adf_urls`: `inlineCard`/`blockCard`/`embedCard`, link marks, plain-text URLs); `adf_to_text` drops the first two, so it can't be reused. Confirmed live on a real ticket: `inlineCard`, link marks and plain-text URLs. The UI shows where a link was found ("link in comment/description").

- **Billed external-LLM calls on by default; per-step LLM call count (user-requested, 2026-10-12)**: after a restart the UI's poller resumed without the one-time consent, so the stopgap's `typer.confirm` blocked a poll cycle on a prompt only visible in the backend terminal. The user asked for the confirmation to be always on by default instead of answering it. Implemented as one config flag, `analyst.auto_confirm_external_llm` (config.yaml, `true`), read by `engine._build_real_agent`, so it applies to the CLI as well as the UI; the UI hides its consent checkbox and the poller-start prompt when it is on, and the backend stops requiring `confirm_external_llm`. Set it to `false` (or `SDLC__ANALYST__AUTO_CONFIRM_EXTERNAL_LLM=false`) to restore the previous per-call/per-trigger confirmation — a deliberate default for this instance, worth flipping before sharing a clone. Separately, each analyst run adds its model-call count to `workflow/<use_case>/llm_usage.json` (`{step_id: {calls, runs}}`), taken from Strands' `AgentResult.metrics.cycle_count` (one event-loop cycle = one model call; field names checked against the installed `strands`), and the UI shows it under the step's ticket. Not counted: throttling retries inside a cycle and runs that raise before returning a result; dry runs record nothing.

- **Approve/reject from the UI; reject now regenerates (user-requested, 2026-10-12)**: this reverses the earlier "approving plans stays in Jira" choice. The UI adds the same label a person would (`POST /api/runs/{uc}/steps/{step}/approve|reject`; only for a step in `awaiting_approval`, and refused with 409 if a label is already pending), then wakes the poller. While wiring it up it turned out that `sdlc-rejected` never regenerated anything — `check_approvals` only set the run state to `ready` and commented "plan will be regenerated", and nothing consumed `ready`; the label also stayed on the ticket. `check_approvals` now (given the registry and agent, which `sync_once` passes) removes the label (`TicketSystem.remove_label`, Jira `update.labels.remove`), sets `regenerating`, re-runs the agent and returns to `awaiting_approval` (or `ready` if it produced no plan). Agent exceptions in `_start_step` and in regeneration are now posted to the ticket as "Agent run failed: …" instead of propagating out of the poll cycle after the event had already been consumed. Still open: there is no way to re-run a step whose run failed (state is left as-is) other than the CLI `analyst-plan` or moving the ticket back to To Do and re-completing an input; a "Run agent now" action would close that. Also in the UI: the latest comment in the Progress table is clamped to ~3 lines / 160 characters with a Show more toggle.

## Decisions (AWS packaging check for the mock Jira — 2026-10-12)
- **What was checked**: how the AgentCore Runtime and the orchestrator Lambda get their code and config, read from the installed `bedrock-agentcore-starter-toolkit` source and by building the Lambda zip and importing its handler. Nothing was deployed.
- **AgentCore Runtime is a `direct_code_deploy`** (`.bedrock_agentcore.yaml`: PYTHON_3_11, arm64), not a container, so the toolkit zips the project folder with its own fixed ignore list: `config.yaml` goes in, `.env` does not. The project's own `.dockerignore` is not read, so `.data/` and `.state/` are uploaded too (small, but local corpus). Consequence: credentials and `profile` never reached the runtime; `config.yaml` says `profile: local`, so under the mock the agent would have kept the board on its own disk instead of S3.
- **Fix**: `agent_deploy._runtime_env` passes `SDLC__PROFILE=aws`, the Jira mode/keys and the Atlassian credentials to `agentcore launch --env KEY=VALUE` (repeatable option, checked in the toolkit's CLI source). Plaintext, like the Lambda. Secret values are masked in anything printed or raised (`_redacted`).
- **Lambda zip**: it is a file whitelist. Added `adapters/mock_jira.py` and the light `agents/analyst/ticket_anchors.py` (the orchestrator imports it). `wiring.py` imported `bedrock_kb` and `confluence` at module scope although the zip leaves them out, so the handler could not import at all (found by importing it from the built zip); both are now lazy, and a test imports the handler from the unpacked zip.
- **Still open (not fixed)**: the Lambda zip carries no third-party packages. The chain needs PyYAML, python-dotenv and requests (+ certifi, idna, charset_normalizer); the Lambda runtime supplies only boto3/botocore and their dependencies. They need vendoring into the zip (or a layer) before `aws-deploy` can work.
- **Still unverified (needs a real launch)**: the runtime runs `src/sdlc/agents/agentcore_app.py` as a script, and the toolkit's `dependencies.zip` does not contain the `sdlc` package, so `import sdlc` depends on the runtime's working directory/sys.path. If it fails, add a root-level entry shim that puts `src` on `sys.path`.

## Decisions (Start fresh and Confluence pages — user-requested, 2026-10-12)
- **Start fresh, mock Jira**: deletes the use case's mock tickets (`cleanup_run`, children first; a failed delete answers 409 and leaves the saved run alone). **Real Jira**: unchanged — never touches tickets (the earlier confirmed decision stands; deleting real tickets from one button was judged too risky). Confluence pages are only deleted when the user ticks "also delete the Confluence pages" (`delete_pages_too`), in either mode.
- **Which pages**: every page the workflow created, not just the plan page. Each agent that publishes a page calls `workflow.pages.record_page` (`workflow/<uc>/pages.json`: page id -> title/url/issue key); cleanup deletes the recorded ids, plus (CLI only, for runs from before the record existed) a plan page linked from a ticket by title. Links a person added as step inputs (DDS/TDS anchors) are never recorded, so never deleted. Confluence moves deleted pages to the space trash. The record survives a Start fresh that keeps the pages, so a later one can still delete them; a page that fails to delete is reported (`page_errors`), stays recorded for a retry, and doesn't stop the saved run being cleared.
- **Pages kept, plan re-run**: the plan page is now **replaced**, not appended to (`DocumentSource.replace_page_body`, the same update-page request `PUT /pages/{id}` as `move_page`; the old body stays in the page history). `append_to_page` had no other caller and was removed. This also changes the reject-then-regenerate flow: the regenerated plan replaces the rejected one on the page.

## Decisions (Mock Jira — user-requested, 2026-10-12)
- **Why**: all ticket traffic went to a real Jira project; the user wanted a local Jira-like Scrum board (backlog, sprints, board, create/edit/delete) that the same workflow UI links to, chosen by one config switch, with delete that works.
- **User-confirmed**: Confluence stays real (only Jira is mocked); the mock works for **both** profiles (so its files live in the ObjectStore, S3 for `aws`); ticket links open a **new tab per click**; "Start fresh" deletes the tickets (see the Start fresh entry for the narrowing to mock mode and the Confluence checkbox).
- **Design**: `jira.mode: mock | real` (a config without `mode` is real). One adapter, `MockJiraClient`, implements the whole `TicketSystem` port plus board operations the UI needs (not on the port). Storage is plain JSON files per ticket so the data is readable and diffable; the ObjectStore has no listing, so `board.json` holds the issue ranking and doubles as the index. Several processes write, so every write is a read-modify-write with `put_bytes_if` (local: content hash + file lock; S3: `IfMatch`/`IfNoneMatch`, 412/409 -> `VersionConflict`, checked against the installed botocore's own docs). Not verified against real S3.
- **Fidelity choices**: company-managed Scrum rules — one active sprint at a time, only future sprints are deletable, completing a sprint moves unfinished issues to a future sprint or the backlog, a Subtask follows its parent into a sprint, deleting an issue deletes its Subtasks (an epic's stories are detached, as Jira does), any-to-any transitions over `jira.mock.statuses`. Not built: workflows/transition rules, permissions, custom fields, notifications, a Kanban project, reports.
- **AWS**: see "AWS packaging check" above for the AgentCore/Lambda changes the mock needed (`--env`, the Lambda zip whitelist).
- **Incident while building it (2026-10-12)**: a smoke test of step 7 hit the user's own `make api` on :8000 (the test server failed to bind) and its `reset` call deleted that run's saved local state (`workflow/demo_order_fulfilment/*`, `ui/demo_order_fulfilment/selection.json`) and `jira_checkpoint.json`; the tickets in real Jira and Confluence were not touched. Test data written to `MOCK-1` was removed again. Re-triggering the workflow re-adopts same-project real tickets by their marker labels; the plan and run state have to be regenerated.

## Decisions (mock Jira: dependency wording — user-requested, 2026-10-13)
- The issue page words the "Blocks" link as **depends on** (this issue is blocked by the other) and **is depended by** (this issue blocks the other). The stored link type stays "Blocks", because the orchestrator and real Jira use that name; only the display and the `/api/mockjira` relation values changed.
- `TicketSystem.link_issues(inward_key, outward_key, "Blocks")` is read the way the orchestrator uses it: `inward_key` blocks `outward_key` (a step's input is the blocker, so the step depends on it). The mock had stored the two sides the other way round for that call (the UI hid it, since it always called with its own argument order); fixed, and a test pins that workflow-created links read "depends on" on the step.
- **Unverified against real Jira**: that the REST `inwardIssue` of a "Blocks" link is the blocker, which is what the orchestrator's call and its dry-run message ("A Blocks B") assume. The mock follows that reading; `JiraClient.link_issues` is unchanged. Check one real link's direction before relying on it.

## Decisions (mock Jira: no "mock" in the UI — user-requested, 2026-10-13)
- The UI no longer shows the word "mock": the logo and tab title say "Jira", the workflow page links "Open Jira board", and the dialogs talk about "the Jira board". Code identifiers (`jira.mode: mock`, `MockJiraApp`) keep the name; they are never displayed.
- The default project key/name are now `SDLC` / "SDLC Delivery" (ticket keys `SDLC-n`, sprints "SDLC Sprint n"). They are data, so an existing board keeps its old key: `sdlc mock-jira-rename` (user-chosen over starting an empty board) copies a board to the new key, rewriting issue keys wherever they are exact values (parents, links, ranking, history, saved runs' `steps.json`/`selection.json`) and the generated sprint names. Free text (comments, descriptions) is not rewritten. The old board is left untouched as the backup, a target that already exists is refused, and it is dry-run by default.

## Decisions (ticket sections: brief description + 3 checklists — user-requested, 2026-10-13)
- **What**: Story, Task and Bug tickets (user-chosen; not Epics or Subtasks) get a **Brief Description** (text) and three **checklists** — Acceptance Criteria, Definition of Done, Test Coverage — each item tickable, with a done count (user-chosen: checklists + text over plain text boxes). Shown and edited on the issue page; the shared definition is `src/sdlc/sections.py`.
- **Who fills them (user-requested)**: the analyst's LLM decides the content per requirement. `Requirement` in the plan schema gained `brief_description`, `definition_of_done` and `test_coverage` (alongside `acceptance_criteria`), all **required** so the model can't skip them, and both `SYSTEM_PROMPT` and the field descriptions tell it that these are ticket sections, which three are checklists the team ticks off, and to write short, independently verifiable items. Not defaults from config. The plan page and plan.md show all of them, so the reviewer approves what the tickets will contain.
- **Tickets**: `create_issue(..., sections=...)` is a port parameter. The mock stores them as fields (checklist items are `{text, done}`; history shows "3 items, 1 done"); real Jira has no such fields, so `JiraClient` appends them to the description as text (the same effect as before for acceptance criteria). A story's description no longer repeats the acceptance criteria in the mock.
- **Compatibility**: a plan saved before this change (no new fields) still applies — missing sections are empty; tickets saved before open with empty sections. Existing runs' prompts/plans are not regenerated.
- **Not verified**: how well a real model fills the new fields — no live LLM call was made; the schema and prompt are pinned by tests only. Check the first generated plan.

## Decisions (agent visibility + optional Knowledge Base — user-requested, 2026-10-13)
- **Problem**: after the analyst step started, the UI showed nothing (the progress snapshot is cached and only rebuilt after the poller cycle, which is blocked inside the agent), and there was no way to see what the model was sent, what it answered, or what it asked the Knowledge Base. Steps also couldn't run without a KB.
- **Live status**: `agents/activity.py` `RunRecorder` overwrites one small doc per step in the ObjectStore at each phase; `/progress` merges it in on every poll (store reads only). Works for both profiles, since the AgentCore agent writes to the S3 store the UI reads. Nothing is written on a dry run or without a step id.
- **Trace** (user-chosen: shown in a UI drawer; full input text, images/PDFs as stubs): per run, the system prompt, the anchor text in full, each model response (text and tool calls), each KB query with the chunks it returned, token totals and the plan. Newest 10 runs per step are kept (an ObjectStore can't list, so an `index.json` lists them). Anchor text can be up to `analyst.max_anchor_chars` per run, and the trace is rewritten at each event.
- **Optional KB** (user-chosen: config plus automatic): `analyst.use_knowledge_base` (default true) and a non-empty `aws.knowledge_base_id` both have to hold (`wiring.kb_enabled`). Off means no `search_knowledge` tool and a prompt that says no KB exists and not to invent stakeholder ids. A KB error mid-run becomes a tool result for the model and an error on the trace's search entry. The `search` CLI says so when no KB id is set.
- **Verified against the installed `strands-agents` 1.58.1** (`strands/hooks/events.py`): `Agent(hooks=[provider])` with `BeforeModelCallEvent` / `AfterModelCallEvent`; the latter carries `stop_response.message` and `stop_reason` or `exception`, but no per-call token usage, so tokens are recorded once at the end from `result.metrics.accumulated_usage`.
- **Not verified**: a real Bedrock/Anthropic run end to end — the hooks, the trace shape for a real model response, and the drawer against live data are pinned by fakes and a type check only (and the UI hasn't been viewed in a browser). Check the first real run's log.
