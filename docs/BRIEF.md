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
One codebase that runs in two modes, selected by config (profile: local | aws):
1. LOCAL mode: works on my laptop with no AWS account or credentials. It still talks to my personal Atlassian Cloud site for Confluence and Jira.
2. AWS mode: the same logic, but storage, search and agent runtime use AWS (S3, Bedrock Knowledge Base, AgentCore).
AWS SDK imports must be lazy, so local mode runs even if boto3 is not installed.

## Broader context (design for extensibility, implement only the Phase-1 task)
Autonomous Data SDLC. Business raises a Frontdoor Request. The Data Analyst agent does feasibility analysis (can the data platform consume this use case for ETL), then presents results to business in a sanitised format in a BI tool (for example Tableau). Analyst work: collect stakeholder requirements, analyse source data, mature scope, create the conceptual data model, work with the team on solution architecture, and write the Data Design Solution. Engineering produces the Technical Design Solution. The analyst then creates solution requirements (THIS PHASE), and later a data contract from the design and requirements. Engineers build; testers write test cases from requirements, test, then engineers deploy.
- Implement ONLY: Data Design Solution + Technical Design Solution -> solution requirements -> Jira Epics/Stories/Sub-tasks.
- Structure the analyst agent as a task registry: each task declares its input page roles, retrieval scope, outputs and upstream/downstream links, so a future task (for example, create a data contract) can be added without changing the core.
- Store traceability links (requirement -> design section -> stakeholder requirement) so the agent can later back-track to the start.
- Later analyst tasks and the Engineering and Tester agents are out of scope; leave documented stubs only.

## Architecture: ports and adapters
Define interfaces, each with a local and an AWS implementation where relevant:
- DocumentSource (Confluence)
- TicketSystem (Jira)
- StatusSource (ticket progress events; polling implementation now)
- ObjectStore (local folder | S3)
- KnowledgeIndex (local vector index | Bedrock Knowledge Base)
- VisionDescriber (describes architecture diagrams; local-mode model to be decided with me)
- AgentRuntime (local runner | AgentCore)
Configuration: config.yaml with environment-variable overrides, plus a profile switch. Every AWS parameter (region, bucket names, KB ID, model IDs, prefixes, role ARNs) must be configurable, with no hardcoded values.

## Demo use case and generic terminology
- Ship a generic demo use case: "Order Fulfilment Performance" for a fictional retailer. Sources: Orders, Shipments, Customers, Product catalogue. Target: platform layers (raw -> curated -> presentation) and a BI dashboard for on-time delivery %, average fulfilment time and backorder rate. Dummy data only. If another generic use case shows the flow better, propose it in your questions.
- `usecases/demo_order_fulfilment/` holds seed content, config and templates. My enterprise use case will later be a sibling folder `usecases/<name>/` with no code changes. Add `usecases/enterprise*/` to .gitignore (this repo is public).
- A seed script (supports --dry-run) creates the Confluence page tree in my personal site with attachments. Generate diagrams programmatically (Graphviz or Mermaid to PNG). Pages: Frontdoor Request; Stakeholder Requirements; Feasibility Analysis; Source Data Analysis; Conceptual Data Model (ER diagram); Solution Architecture (diagram); Data Design Solution (source-to-target mappings, layers, data quality rules, refresh schedule); Technical Design Solution (pipeline design, orchestration, environments, deployment, architecture diagram); Data Contract (placeholder for a later task); Test Strategy (placeholder).
- `terminology.yaml` per use case maps generic terms to organisation terms (Frontdoor Request, Data Design Solution, Technical Design Solution, Solution Requirements, Data Contract, layer names, issue-type names, status names). `page_roles.yaml` says how each role is found (page ID, title pattern or label). Jira titles, descriptions and acceptance criteria come from Jinja templates in `templates/`, so enterprise wording never touches code.

## Components

### 1. Confluence download and write-back
- Auth with Atlassian Cloud email + API token; base URL configurable.
- Download a space or page tree, preserving the hierarchy. Get each page's ancestors via the API and mirror them as nested folders: Space/Parent/Child/Child__<pageId>. Include the page ID in filenames so renames and moves do not create duplicates.
- Canonical copy = Markdown per page + attachments/images in an _attachments folder at the same level (image links resolved relative to it) + metadata sidecar. Always produced in both profiles. It drives hierarchy, incremental sync, write-back and LOCAL search.
- For the AWS Knowledge Base, consider which file format is ideal for image and text extraction and search. My default assumption is one self-contained PDF per page with diagrams inline (Bedrock parsers read PDFs and images, and do not follow image links from Markdown). Verify this against the current Bedrock docs and tell me if a better format exists.
- Write a metadata sidecar per page (pageId, spaceKey, title, parentPath, url, version, lastModified) and keep a manifest (pageId to last synced version and path) for incremental sync, including handling of moved, renamed and deleted pages. (Consider best practices that would be easy to manage. You might suggest your approach if this doesn't sound fit.)
- Also support creating pages and appending to pages, so the analyst agent can publish outputs back to Confluence (behind --dry-run).

### 2. PDF rendering (AWS profile only)
- PDFs are a derived artifact, generated ONLY in the aws profile as a step of `aws-sync`. Never generate PDFs in local mode.
- Render each page to a self-contained PDF with diagrams embedded inline where they are referenced in the text (WeasyPrint or Pandoc; propose one and ask me, or suggest something better). The PDF is the unit that gets ingested into the Knowledge Base.

### 3. Local search, including architecture images
- Local search needs no PDFs. Work from the Markdown + _attachments. Describe each image with a vision model using a prompt tuned for architecture diagrams (components, connections, data flows, labels, technologies), and insert the description into the text at the image's position.
- Chunk so a diagram's description stays next to its surrounding paragraph. Embed, and store in a local vector store (propose one, for example Chroma, LanceDB or FAISS, and ask me).
- CLI: `search "<query>"` returns text snippets, the matching image file path, page title, hierarchy path and Confluence URL.

### 4. Jira
- Create and read Epics, Stories and Sub-tasks (using the parent field for hierarchy), with assignee, labels, components, description and acceptance criteria. Look up transitions by name from the API, never hardcode transition IDs.
- Idempotency: tag created issues (label or issue property) with a stable key derived from the source design page and requirement, so re-running never creates duplicates.
- Progress monitoring:
  - Implement StatusSource as a PollingStatusSource: JQL `project = X AND updated >= <checkpoint>`, read each issue's changelog for status transitions, persist the checkpoint (local state file; S3 object in the aws profile). Poll interval configurable.
  - One handler, handle_status_change(issue_key, from_status, to_status), applies the rollup rules: sub-task statuses drive the story, story statuses drive the epic (for example, any child In Progress moves the parent to In Progress; all children Done moves the parent to Done). Rules are configurable. Confirm my exact intended rules with me before implementing. Any future event source must call this same handler.
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
- Flow: generate a plan file (JSON + readable markdown) first. Create the Jira issues only after I approve it, with a separate `apply` step.
- Run it through the AgentRuntime interface. Locally it is a plain Python runner; on AWS it is deployed to AgentCore. Propose the agent framework and ask me (for example Strands Agents SDK, or plain Python with Bedrock/Anthropic).
- The Engineering agent is out of scope. Only leave a documented empty interface and an agents/ folder layout so it can be added later.

### 6. AWS mode: provisioning with AWS CLI only
- No Terraform, CDK or SAM (my enterprise does not have them). Provision with AWS CLI commands wrapped in idempotent scripts under infra/aws/. Ask me whether `aws cloudformation deploy` is allowed; if it is, it may be an option.
- Each script reads the same config, checks whether a resource exists before creating it, supports --dry-run (print commands only), tags everything, and appends created IDs/ARNs to a gitignored resource ledger so `aws-destroy` deletes exactly what was created, in reverse order. One-command destroy matters because this is a time-boxed free-plan account.
- IAM: least-privilege policy JSON files in infra/aws/policies/, created via the CLI.
- S3 bucket(s): source PDFs keyed by the Confluence hierarchy, plus a multimodal storage destination for extracted images.
- Bedrock Knowledge Base that understands architecture images in PDFs: use the Managed KB type with the Managed parser (it describes figures, charts and images). Verify current docs. Parsing and chunking strategy cannot be changed after a data source is created, so create a NEW data source with the Managed parser rather than editing an existing one.
- Avoid vector stores with standing minimum costs (for example OpenSearch Serverless). Prefer the managed vector store or S3 Vectors, and tell me the cost implications of anything you choose.
- AWS Budgets alert, consistent tagging, and an EventBridge schedule for progress sync.
- AgentCore: create the AgentCore harness/runtime to deploy the analyst agent. Look up the current AgentCore docs and confirm the exact resources and steps with me before implementing.
- Do not assume CLI coverage. Check the installed CLI (`aws bedrock-agent help`, the AgentCore control-plane commands) and the official docs. If Managed KB or an AgentCore step cannot be done with the AWS CLI, tell me and propose options.
- Runtime app code may use boto3 to query or invoke. Confirm with me that this is allowed in my enterprise.
- Sync command (`aws-sync`): render PDFs, upload PDFs and metadata sidecars to S3, start the KB ingestion job via CLI, and poll until it finishes.

## Testing and docs
- (Unit tests can be skipped for now): Unit tests with mocked Atlassian and AWS calls (no real network needed). The demo seed script above is NOT skipped.
- A Makefile or task runner with: setup, seed, sync, index, search, analyst-plan, analyst-apply, sync-progress, watch-progress, aws-deploy, aws-sync, aws-destroy.
- README with local quick start and AWS quick start.

## Phases (confirm with me after each)
0. Questions, then plan, then CLAUDE.md
1. Project skeleton, config/profile system, interfaces, .gitignore and .env.example
2. Confluence download, hierarchy, manifest (no PDFs yet)
3. Local index and search with diagram descriptions
3b. Demo use case: seed content, terminology.yaml, page_roles.yaml, templates
4. Jira client, hierarchy creation, transitions, progress polling and rollup
5. Analyst agent locally: plan, approve, apply
6. AWS CLI scripts: PDF render + S3, Knowledge Base, AgentCore, schedule, destroy
7. Docs, tests, cleanup

## Questions to ask me first (seed list, add your own)
Python version/tooling; PDF renderer; local vision and embedding models (Anthropic API vs local Ollama vs Bedrock); local vector store; agent framework; whether `aws cloudformation deploy` is allowed; whether boto3 is allowed at runtime in my enterprise; AWS region and which Bedrock models are enabled; whether the demo use case is acceptable; Jira project setup (issue types, workflow status names, how to identify engineering vs tester assignees); the exact progress-rollup rules; how design pages are identified in Confluence (titles, labels or page IDs).