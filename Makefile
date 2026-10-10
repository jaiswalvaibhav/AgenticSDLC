# Every target is dry-run by default (see CLAUDE.md). Pass variables as needed, e.g.:
#   make sync ROOT_PAGE_ID=123456
#   make search QUERY="on-time delivery"
#   make workflow-start USE_CASE=demo_order_fulfilment
#   make jira-sync EPIC_KEY=DEMO-1
.PHONY: setup seed sync search workflow-preview workflow-start analyst-plan analyst-apply \
        sync-progress watch-progress aws-deploy aws-sync aws-destroy \
        jira-sync jira-kb-create jira-aws-sync jira-search test

setup:
	uv run sdlc setup

seed:
	uv run sdlc seed --dry-run

sync:
	uv run sdlc sync --root-page-id $(ROOT_PAGE_ID) --dry-run

search:
	uv run sdlc search "$(QUERY)"

workflow-preview:
	uv run sdlc workflow preview --use-case $(USE_CASE)

workflow-start:
	uv run sdlc workflow start --use-case $(USE_CASE)

analyst-plan:
	uv run sdlc analyst-plan

analyst-apply:
	uv run sdlc analyst-apply

sync-progress:
	uv run sdlc sync-progress --dry-run

watch-progress:
	uv run sdlc watch-progress

aws-deploy:
	uv run sdlc aws-deploy --dry-run

aws-sync:
	uv run sdlc aws-sync --dry-run

aws-destroy:
	uv run sdlc aws-destroy --dry-run

jira-sync:
	uv run sdlc jira-sync --epic-key $(EPIC_KEY) --dry-run

jira-kb-create:
	uv run sdlc jira-kb-create --dry-run

jira-aws-sync:
	uv run sdlc jira-aws-sync --dry-run

jira-search:
	uv run sdlc jira-search "$(QUERY)"

test:
	uv run pytest
