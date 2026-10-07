# Project: Autonomous Data SDLC, Analyst Agent (Phase 1)

## How I want you to work
- Read this whole brief, then ask me clarifying questions (share your recommended default listed first). Only after all the questions are answered, write a plan for my approval.
- Build in phases (listed at the end). Stop at the end of each phase, summarize in 2-3 sentences, and confirm before moving on.
- If anything becomes ambiguous later, ask me instead of guessing.
- Never invent API or CLI details for AWS (Bedrock Knowledge Bases, AgentCore), Jira or Confluence. Check the current official docs first and tell me what you verified.
- Write Minimal Code: Favor the absolute simplest, most concise implementation that fully satisfies the requirements. Avoid over-engineering, premature abstraction, or speculative generalization
- Reuse First: Always check existing project files, utilities, and libraries before writing new functions or components. Do not reinvent the wheel.
- Targeted Changes: When modifying existing code, touch only the lines necessary to achieve the goal. Avoid sweeping refactors or unsolicited stylistic changes to surrounding code.
- Create a CLAUDE.md early, and keep it updated with architecture, commands and decisions.

## Context and ground rules
- This is a personal-laptop project using MY PERSONAL accounts only: a personal Atlassian Cloud free site and a personal AWS account (free plan). Use dummy content only.
- It will later be brought into an enterprise repo from this public GitHub repo, so the code must be generic: no employer names, domains, space keys or real data anywhere (use placeholders or configurable details such as your-domain.atlassian.net and SPACE_KEY).
- All secrets come from environment variables or a gitignored .env file. Add .gitignore (with .env) BEFORE the first commit. Provide .env.example. Never log or commit tokens.
- All write operations (Confluence, Jira, AWS) must support --dry-run, and dry-run should be the default for anything that creates or changes remote resources.

## Goal
One codebase that runs in two profiles, selected by config (profile: local | aws). Both profiles use AWS for Claude (Bedrock) and search (a Bedrock Managed Knowledge Base, ap-southeast-2). There is no local RAG, local vision model, local embeddings or local vector store.
1. LOCAL profile: state (poll checkpoint, step run state, sync manifest, plans, traceability) lives in a gitignored `.state/` folder. The agent and orchestrator run on my laptop.
2. AWS profile: state lives in S3, the agent runs on AgentCore, and the orchestrator runs on an EventBridge schedule.
boto3 is a core dependency, because both profiles need AWS.

## Broader context (design for extensibility, implement only the Phase-1 task)
Autonomous Data SDLC. Business raises a Frontdoor Request. The Data Analyst agent does feasibility analysis (can the data platform consume this use case for ETL), then presents results to business in a sanitised format in a BI tool (for example Tableau). Analyst work: collect stakeholder requirements, analyse source data, mature scope, create the conceptual data model, work with the team on solution architecture, and write the Data Design Solution. Engineering produces the Technical Design Solution. The analyst then creates solution requirements (THIS PHASE), and later a data contract from the design and requirements. Engineers build; testers write test cases from requirements, test, then engineers deploy.
- Implement ONLY: Data Design Solution + Technical Design Solution -> solution requirements -> Jira Epics/Stories/Sub-tasks.
- Structure the analyst agent as a task registry: each task declares its input page roles, retrieval scope, outputs and upstream/downstream links, so a future task (for example, create a data contract) can be added without changing the core.
- Store traceability links (requirement -> design section -> stakeholder requirement) so the agent can later back-track to the start.
- Later analyst tasks and the Engineering and Tester agents are out of scope; leave documented stubs only.

### Workflow orchestrator (added in Phase 0)
- Lifecycle steps live in `config/workflow.yaml`, which a use case can override. Each step declares its owner, inputs, required artifact and automation (an agent task, or manual). The steps are:
  1. Stakeholder Requirements
  2. Scope
  3. Conceptual Data Model
  4. Solution Architecture
  5. Data Design Solution
  6. Technical Design Solution
  7. Solution Requirements (automated: analyst agent)
  8. Data Contract (optional; input: Data Design Solution)
  9. Test Case Specification
  10. Data Solution Development
  11. Test Automation
  12. Test Solution Report (TSR)
  13. Solution Deployment
- `sdlc workflow start --use-case <uc> [--from <step>] [--steps ...]` (dry-run by default):
  - creates ONE epic per use case at the start, plus one Story per selected step
  - `--from` selects that step and every non-optional step downstream of it; optional steps (e.g. Data Contract) are created only if they are explicitly chosen
  - each unselected direct input gets an "artifact-only" Story; I attach the artifact and mark it Done
  - all tickets, including generated requirement stories, sit under the same epic
- Artifacts are attached to the step ticket. A Confluence page link is preferred, and it overrides `page_roles.yaml`. A file attachment is the fallback.
- When all of a step's inputs are Done and have artifacts, the orchestrator comments, assigns the step and moves it to In Progress. If the step is automated, it also runs the agent.
- The agent attaches its plan to the step ticket. Adding the label `sdlc-approved` makes the next poll apply the plan; `sdlc-rejected` plus a comment regenerates it.

## Architecture: ports and adapters
Define interfaces, each with a local and an AWS implementation where relevant:
- DocumentSource (Confluence)
- TicketSystem (Jira)
- StatusSource (ticket progress events; polling implementation now)
- ObjectStore (local folder | S3; used for state)
- KnowledgeIndex (Bedrock Managed Knowledge Base; a fake that returns canned chunks for tests)
- LLM (Claude on Bedrock; a fake for tests)
- AgentRuntime (local runner | AgentCore)
(VisionDescriber is removed: the KB parser reads diagrams in PDFs, and anchor diagrams are sent to Claude as images.)
Configuration: config.yaml with environment-variable overrides, plus a profile switch. Every AWS parameter (region, bucket names, KB ID, model IDs, prefixes, role ARNs) must be configurable, with no hardcoded values.

## Demo use case and generic terminology
- Ship a generic demo use case: "Order Fulfilment Performance" for a fictional retailer. Sources: Orders, Shipments, Customers, Product catalogue. Target: platform layers (raw -> curated -> presentation) and a BI dashboard for on-time delivery %, average fulfilment time and backorder rate. Dummy data only. If another generic use case shows the flow better, propose it in your questions.
- `usecases/demo_order_fulfilment/` holds seed content, config and templates. My enterprise use case will later be a sibling folder `usecases/<name>/` with no code changes. Add `usecases/enterprise*/` to .gitignore (this repo is public).
- A seed script (supports --dry-run) creates the Confluence page tree in my personal site with attachments. Generate diagrams programmatically (Graphviz or Mermaid to PNG). Pages: Frontdoor Request; Stakeholder Requirements; Scope; Feasibility Analysis; Source Data Analysis; Conceptual Data Model (ER diagram); Solution Architecture (diagram); Data Design Solution (source-to-target mappings, layers, data quality rules, refresh schedule); Technical Design Solution (pipeline design, orchestration, environments, deployment, architecture diagram); Data Contract (placeholder for a later task); Test Strategy (placeholder).
- `terminology.yaml` per use case maps generic terms to organisation terms (Frontdoor Request, Data Design Solution, Technical Design Solution, Solution Requirements, Data Contract, layer names, issue-type names, status names). `page_roles.yaml` says how each role is found (page ID, title pattern or label). Jira titles, descriptions and acceptance criteria come from Jinja templates in `templates/`, so enterprise wording never touches code.

## Components

### 1. Confluence download and write-back
- Auth with Atlassian Cloud email + API token; base URL configurable.
- Download a space or page tree, preserving the hierarchy. Get each page's ancestors via the API and mirror them as nested folders: Space/Parent/Child/Child__<pageId>. Include the page ID in filenames so renames and moves do not create duplicates.
- Canonical copy = `page.html` per page (export view), plus attachments/images in an `_attachments` folder at the same level, plus a metadata sidecar. There is no Markdown copy. `page.html` is the PDF source and the full-text anchor the agent reads. The canonical copy drives hierarchy, incremental sync and write-back.
- One self-contained PDF per page, with its diagrams inline, is what the Knowledge Base ingests.
- Write a metadata sidecar per page (pageId, spaceKey, title, parentPath, url, version, lastModified).
- Keep a manifest for incremental sync, mapping pageId to last synced version, path, S3 key and PDF hash.
  - The S3 key is `confluence/<space>/<pageId>.pdf`, so a rename or move changes only the metadata, never the key.
  - A changed version means: regenerate the PDF, upload it, and ingest.
  - A deleted page means: delete the S3 object (the next ingestion removes its vectors) and keep the local copy in `_deleted/`.
- Also support creating pages and appending to pages, so the analyst agent can publish outputs back to Confluence (behind --dry-run).

### 2. PDF rendering (both profiles, step of `aws-sync`)
- Confluence Cloud has no official PDF export API, so each page is rendered from `page.html` and `_attachments` with WeasyPrint into a self-contained PDF, with diagrams inline where they are referenced.
- Each PDF is uploaded together with `<pageId>.pdf.metadata.json`, which holds use_case, space, pageId, title, path, url and version.

### 3. Knowledge Base search, including architecture images
- Bedrock Managed Knowledge Base (ap-southeast-2):
  - managed parser, with image extraction enabled so diagrams in PDFs are described
  - managed embedding and reranking
  - hybrid search
- CLI: `search "<query>"` calls Retrieve with `managedSearchConfiguration` and a `use_case` filter. It returns text snippets, page title, hierarchy path, Confluence URL and S3 URI.

### 4. Jira
- Create and read Epics, Stories and Sub-tasks (using the parent field for hierarchy), with assignee, labels, components, description and acceptance criteria. Look up transitions by name from the API, never hardcode transition IDs.
- Idempotency: tag created issues (label or issue property) with a stable key derived from the source design page and requirement, so re-running never creates duplicates.
- Progress monitoring:
  - Implement StatusSource as a PollingStatusSource: JQL `project = X AND updated >= <checkpoint>`, read each issue's changelog for status transitions, persist the checkpoint (local state file; S3 object in the aws profile). Poll interval configurable.
  - One handler, handle_status_change(issue_key, from_status, to_status), applies the rollup rules: sub-task statuses drive the story, story statuses drive the epic (for example, any child In Progress moves the parent to In Progress; all children Done moves the parent to Done). Rules are configurable. Confirmed rules:
      - If any child is In Progress or Done (and not all are Done), the parent moves to In Progress.
      - If all children are Done, the parent moves to Done.
      - With `reopen_parent_on_child_reopen: true`, a Done parent moves back to In Progress when a child reopens or is added, and a comment explains why. If the workflow blocks that transition, the orchestrator only comments.
      - Generated requirement stories roll up to the Data Solution Development step through issue links.
    - The same handler also drives the workflow orchestrator (step readiness, automated steps, approval). Any future event source must call this same handler.
  - Local: `sync-progress` (one-shot) and `watch-progress` (loop). AWS: the same one-shot command on a schedule (EventBridge rule created with AWS CLI).
  - Do NOT build a webhook receiver now. Leave a documented WebhookStatusSource stub. Even with webhooks later, keep polling as reconciliation.
  - Idempotent, supports --dry-run.

### 5. Analyst agent (the only agent in scope now)
- Inputs (hybrid): anchor pages are given explicitly; the knowledge index supplies supporting detail on demand.
  - Anchors: the Data Design Solution and Technical Design Solution, including their diagrams, are resolved through page_roles.yaml (page ID preferred) and read IN FULL from the downloaded corpus (or Confluence), section by section if too long. Never read them through top-k retrieval.
  - Retrieval: a `search_knowledge(query)` tool through the KnowledgeIndex, scoped by metadata filters (space / hierarchy path of the use case) so other use cases in the same KB don't leak in. Use it for stakeholder requirements, source data analysis, conceptual model, feasibility, glossary.
  - If an anchor isn't configured, the agent may find candidates via search but must show me the resolved pages and wait for confirmation. Record anchor page IDs and versions in the plan file.
  - Each registered task declares its own anchors and retrieval scope.
- Output: solution requirements turned into a Jira structure, meaning Epics, then Stories, then Sub-tasks, with acceptance criteria. Assign engineering work to a configurable "engineering" assignee or component, and test work to a configurable "tester" assignee or component. Each issue links back to its source Confluence page and section, and mentions the relevant diagram.
- Flow: generate a plan file (JSON + readable markdown) first. Create the Jira issues only after I approve it, with a separate `apply` step. Approval is given with the label `sdlc-approved` on the Solution Requirements step ticket.
- Generated Stories (labelled `ws:<workstream>`) sit under the use-case epic, each with Engineering and Testing Sub-tasks.
- Run it through the AgentRuntime interface. Locally it is a plain Python runner; on AWS it is deployed to AgentCore.
- Framework: Strands Agents SDK with a Bedrock Claude model.
  - The Strands `retrieve` tool only supports `vectorSearchConfiguration`, so `search_knowledge` is our own tool. It sits behind the KnowledgeIndex port, with the use-case filter enforced in code.
  - The KnowledgeIndex and LLM ports have fakes, so tests need no AWS.
- The Engineering agent is out of scope. Only leave a documented empty interface and an agents/ folder layout so it can be added later.

### 6. AWS provisioning (CloudFormation + AWS CLI)
- No Terraform, CDK or SAM. `aws cloudformation deploy` is allowed and preferred.
- AWS CLI steps are used only where CloudFormation can't do the job. A Managed KB with managed embedding is reportedly blocked by a CloudFormation schema issue, so the KB and data source are created by an idempotent CLI step.
- Region: ap-southeast-2 for both the personal and enterprise accounts.
- No budget alert (my personal account already has one).
- boto3 is allowed at runtime.
- Each script reads the same config, checks whether a resource exists before creating it, supports --dry-run (print commands only), tags everything, and appends created IDs/ARNs to a gitignored resource ledger so `aws-destroy` deletes exactly what was created, in reverse order. One-command destroy matters because this is a time-boxed free-plan account.
- IAM: least-privilege policy JSON files in infra/aws/policies/, created via the CLI.
- S3 bucket: source PDFs at `confluence/<space>/<pageId>.pdf` (hierarchy kept in the metadata sidecar), plus a `state/` prefix for the aws profile.
- Bedrock Knowledge Base that understands architecture images in PDFs: use the Managed KB type with the Managed parser (it describes figures, charts and images). Verify current docs. Parsing and chunking strategy cannot be changed after a data source is created, so create a NEW data source with the Managed parser rather than editing an existing one.
- Avoid vector stores with standing minimum costs (for example OpenSearch Serverless). Managed KB storage is managed by Bedrock (about $5/GB-month and $1 per 1,000 retrieves according to third-party sources; re-check the AWS pricing page).
- Consistent tagging, and an EventBridge schedule for progress sync (no Budgets alert).
- AgentCore: create the AgentCore harness/runtime to deploy the analyst agent. Look up the current AgentCore docs and confirm the exact resources and steps with me before implementing.
- Do not assume CLI coverage. Check the installed CLI (`aws bedrock-agent help`, the AgentCore control-plane commands) and the official docs. If Managed KB or an AgentCore step cannot be done with the AWS CLI, tell me and propose options.
- Runtime app code may use boto3 to query or invoke. Confirm with me that this is allowed in my enterprise.
- Sync command (`aws-sync`): render PDFs, upload PDFs and metadata sidecars to S3, start the KB ingestion job via CLI, and poll until it finishes.

## Testing and docs
- (Unit tests can be skipped for now): Unit tests with mocked Atlassian and AWS calls (no real network needed). The demo seed script above is NOT skipped.
- A Makefile or task runner with: setup, seed, sync, search, workflow-start, analyst-plan, analyst-apply, sync-progress, watch-progress, aws-deploy, aws-sync, aws-destroy.
- README with local quick start and AWS quick start.

## Phases (confirm with me after each; revised in Phase 0)
0. Questions, then plan, then CLAUDE.md
1. Project skeleton, config/profile system, interfaces + fakes, workflow step registry, .gitignore and .env.example
2. Confluence client: download (page.html, _attachments, metadata), hierarchy, manifest, create/append pages
3. Demo use case: seed content and diagrams, terminology.yaml, page_roles.yaml, templates
4. AWS knowledge: CloudFormation (S3, IAM), Managed KB, WeasyPrint PDFs + metadata, `aws-sync`, `search`
5. Jira client, workflow start, transitions, progress polling, rollup, step orchestration, label approval
6. Analyst agent: plan, approve, apply, traceability
7. AgentCore runtime, scheduled orchestrator, resource ledger, destroy
8. Docs, tests, cleanup

## Phase 0 answers (summary)
- Tooling:
  - Python 3.13 with uv, a Typer CLI `sdlc`, and a Makefile.
  - Graphviz as a dev-only dependency.
  - WeasyPrint for PDFs.
- No local models or vector store: Bedrock Managed KB plus Claude on Bedrock, using Strands.
- AWS:
  - CloudFormation is allowed; boto3 is allowed.
  - Region ap-southeast-2.
  - No budget alert.
- Jira:
  - Epic/Story/Sub-task; statuses To Do/In Progress/Done; transitions looked up by name.
  - Components Engineering/Testing, with optional assignee IDs (unassigned if none are set).
  - Epic titles are prefixed with the use case, and the same `use_case` value is used in Jira labels and KB metadata.
- Design pages are identified by page ID first, then label, then title pattern.
- Demo use case "Order Fulfilment Performance" is accepted.
- Traceability is stored in the plan JSON, a Jira issue property and `traceability.json`. Each record includes page version, S3 URI and the cited section heading, which is spot-checked.