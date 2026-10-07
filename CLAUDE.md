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
- **Workflow**: `config/workflow.yaml` lists the lifecycle steps. There is one Jira epic per use case and one Story per selected step. `handle_status_change()` performs rollup, starts steps whose inputs are ready, and runs automated steps through the agent. Agent plans need the `sdlc-approved` label before they are applied.
- **Use cases**: `usecases/<name>/` holds the config, terminology, page roles and templates for that use case. Adding a use case needs no code changes.

## Commands
- `uv sync`: install dependencies.
- `uv run sdlc --help`: list all commands (`make <target>` wraps them).
- `uv run sdlc config show`: show the merged config, with secrets masked.
- `uv run sdlc workflow preview --use-case demo_order_fulfilment --from solution_requirements [--steps data_contract]`: preview the tickets that would be created.
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
- Demo use case: "Order Fulfilment Performance". Graphviz is a dev-only dependency.
- Confluence sync scales to the enterprise space (~3000 docs) by design: `get_descendants` is metadata-only and `sync_tree` fetches full bodies only for new/changed pages, concurrently. Still open: verify the account's actual Confluence Cloud rate limits against the batch/worker sizes — see the docstrings in `src/sdlc/adapters/confluence.py` (`get_descendants`, `_PAGE_ID_BATCH`) and `src/sdlc/confluence_sync.py`.
