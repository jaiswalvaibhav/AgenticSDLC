# Every target is dry-run by default (see CLAUDE.md). Pass variables as needed, e.g.:
#   make sync ROOT_PAGE_ID=123456
#   make search QUERY="on-time delivery"
#   make workflow-start USE_CASE=demo_order_fulfilment
.PHONY: setup seed sync search workflow-preview workflow-start analyst-plan analyst-apply \
        sync-progress watch-progress aws-deploy aws-sync aws-destroy test

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

test:
	uv run pytest
