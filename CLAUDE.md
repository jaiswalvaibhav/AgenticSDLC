# CLAUDE.md

Autonomous Data SDLC: a Jira-driven orchestrator plus an analyst agent that turns Data Design Solution (DDS) and Technical Design Solution (TDS) pages into Jira work. The full brief is in `docs/BRIEF.md`.

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
  - `aws`: state lives in `s3://<bucket>/state/`, the agent runs on AgentCore, and the orchestrator runs on an EventBridge schedule.
- **Knowledge pipeline**:
  1. Confluence → `.data/corpus/` (`page.html`, `_attachments/`, `meta.json`; no `.md` files).
  2. WeasyPrint renders each page to `<pageId>.pdf`, written with `<pageId>.pdf.metadata.json` to S3.
  3. Ingestion into the Managed KB.
  4. Our own `search_knowledge` tool calls Retrieve with `managedSearchConfiguration` and a `use_case` filter.
- **Workflow**: `config/workflow.yaml` lists the lifecycle steps. `workflow start` creates one Jira epic per use case and one Story per selected step (idempotent via stable marker labels), linked with "Blocks". `orchestrator.handle_status_change()` is the one handler every status event goes through: rollup (Sub-task → Story → Epic), then — if the issue just reached Done and is a step — checks downstream steps' inputs (Done + has an artifact) and starts whichever became ready, running automated steps through `AgentRuntime` (faked until Phase 6). `orchestrator.check_approvals()` is polled separately (a label add isn't a status change) for the `sdlc-approved`/`sdlc-rejected` labels.
- **Use cases**: `usecases/<name>/` holds the config, terminology, page roles and templates for that use case. Adding a use case needs no code changes.

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
- **Needs your confirmation**: rollup (`orchestrator._rollup`) queries children via JQL `parent = <key>` uniformly for Sub-task → Story → Epic. That's correct for a **team-managed** Jira project; a **company-managed** project uses a separate "Epic Link" field for Story → Epic instead, which would need a different JQL clause. Tell me which your "AgenticSDLC" project is.
