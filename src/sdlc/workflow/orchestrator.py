"""The workflow orchestrator: `workflow_start` (creates the use-case epic + step
tickets), one `handle_status_change` handler (rollup, then step readiness + automation
trigger), and `check_approvals` (the sdlc-approved/-rejected label check — polled
separately, since a label add isn't a status change; see CLAUDE.md "Workflow").

Agent execution is faked in Phase 5 (AgentRuntime.run just logs a call, and apply_plan
is a stub); Phase 6 wires in the real Strands-based analyst agent and real requirement
ticket creation.

ASSUMPTION (flagged for the user, see adapters/jira.py docstring): rollup queries
children via JQL `parent = <key>`, which only works uniformly for Epic/Story/Sub-task
in a team-managed Jira project. A company-managed project needs a different clause for
Story -> Epic (the "Epic Link" field) — tell me if that's your project type.
"""
import re
from pathlib import Path

import yaml

from sdlc.ports import AgentRuntime, Issue, ObjectStore, StatusEvent, TicketSystem, TransitionNotAvailable
from sdlc.workflow.registry import Step, WorkflowRegistry

_STEP_LABEL_RE = re.compile(r"^step:([^:]+)$")
_UC_LABEL_RE = re.compile(r"^uc:(.+)$")


def load_terminology(use_case: str) -> dict:
    return yaml.safe_load((Path("usecases") / use_case / "terminology.yaml").read_text())


def _step_id_of(issue: Issue) -> str | None:
    for label in issue.labels:
        if m := _STEP_LABEL_RE.match(label):
            return m.group(1)
    return None


def _use_case_of(issue: Issue) -> str | None:
    for label in issue.labels:
        if m := _UC_LABEL_RE.match(label):
            return m.group(1)
    return None


def _steps_map(store: ObjectStore, use_case: str) -> dict:
    return store.get_json(f"workflow/{use_case}/steps.json") or {}


def _save_steps_map(store: ObjectStore, use_case: str, mapping: dict, dry_run: bool) -> None:
    store.put_json(f"workflow/{use_case}/steps.json", mapping, dry_run=dry_run)


def _run_state(store: ObjectStore, use_case: str) -> dict:
    return store.get_json(f"workflow/{use_case}/run_state.json") or {}


def _save_run_state(store: ObjectStore, use_case: str, state: dict, dry_run: bool) -> None:
    store.put_json(f"workflow/{use_case}/run_state.json", state, dry_run=dry_run)


# --------------------------------------------------------------------- rollup --
def _rollup(issue_key: str, *, tickets: TicketSystem, project_key: str, statuses: dict,
            reopen_on_child_reopen: bool, dry_run: bool) -> None:
    """Sub-task -> Story -> Epic, recursively. Any child In Progress/Done (not all
    Done) moves the parent to In Progress; all children Done moves it to Done;
    optionally a child leaving Done reopens a Done parent, with a comment — or just a
    comment if the workflow blocks that transition."""
    issue = tickets.get_issue(issue_key)
    if not issue.parent_key:
        return
    parent = tickets.get_issue(issue.parent_key)
    children = tickets.search(f'project = "{project_key}" AND parent = "{parent.key}"')
    if not children:
        return

    child_statuses = {c.status for c in children}
    all_done = child_statuses == {statuses["done"]}
    any_active = bool(child_statuses & {statuses["in_progress"], statuses["done"]})

    def _recurse():
        _rollup(parent.key, tickets=tickets, project_key=project_key, statuses=statuses,
                reopen_on_child_reopen=reopen_on_child_reopen, dry_run=dry_run)

    if all_done and parent.status != statuses["done"]:
        tickets.transition_issue(parent.key, statuses["done"], dry_run=dry_run)
        _recurse()
    elif not all_done and any_active and parent.status == statuses["todo"]:
        tickets.transition_issue(parent.key, statuses["in_progress"], dry_run=dry_run)
        _recurse()
    elif not all_done and any_active and parent.status == statuses["done"]:
        if not reopen_on_child_reopen:
            return
        reason = "a child moved out of Done"
        try:
            tickets.transition_issue(parent.key, statuses["in_progress"], dry_run=dry_run)
            tickets.add_comment(parent.key, f"Reopened: {reason}.", dry_run=dry_run)
            _recurse()
        except TransitionNotAvailable:
            tickets.add_comment(
                parent.key,
                f"Would reopen ({reason}) but the workflow doesn't allow "
                f"{statuses['done']} -> {statuses['in_progress']} from here.",
                dry_run=dry_run,
            )


# ----------------------------------------------------------------- readiness --
def _inputs_satisfied(step: Step, steps_map: dict, tickets: TicketSystem, statuses: dict) -> bool:
    for input_id in step.inputs:
        key = steps_map.get(input_id)
        if not key:
            return False
        issue = tickets.get_issue(key)
        if issue.status != statuses["done"] or not tickets.has_artifact(key):
            return False
    return True


def _start_step(step: Step, *, steps_map: dict, tickets: TicketSystem, store: ObjectStore,
                 use_case: str, cfg: dict, statuses: dict, agent: AgentRuntime, dry_run: bool) -> None:
    key = steps_map.get(step.id)
    if not key:
        return  # not created this run (e.g. an unselected optional step)
    issue = tickets.get_issue(key)
    if issue.status != statuses["todo"]:
        return  # already started — idempotent, in case this fires more than once

    inputs_desc = ", ".join(f"{i}: {steps_map[i]}" for i in step.inputs if i in steps_map)
    tickets.add_comment(key, f"Inputs ready ({inputs_desc}).", dry_run=dry_run)

    if cfg["orchestrator"].get("auto_start_manual_steps", True) or step.automation:
        try:
            tickets.transition_issue(key, statuses["in_progress"], dry_run=dry_run)
        except TransitionNotAvailable:
            tickets.add_comment(
                key, f"Inputs ready, but the workflow doesn't allow {statuses['todo']} -> "
                     f"{statuses['in_progress']} from here.", dry_run=dry_run)
            return

    if step.automation:
        agent.run(step.automation, {"use_case": use_case, "step_id": step.id,
                                     "issue_key": key, "dry_run": dry_run})
        run_state = _run_state(store, use_case)
        run_state[step.id] = "awaiting_approval"
        _save_run_state(store, use_case, run_state, dry_run)
        tickets.add_comment(
            key, "Agent plan generated (faked in Phase 5 — no real plan yet). Add the "
                 f"'{cfg['jira']['approval_label']}' label to apply it, or "
                 f"'{cfg['jira']['reject_label']}' to regenerate.", dry_run=dry_run)


def handle_status_change(event: StatusEvent, *, tickets: TicketSystem, store: ObjectStore,
                          cfg: dict, registry: WorkflowRegistry, agent: AgentRuntime,
                          dry_run: bool = True) -> None:
    """The one handler every StatusSource calls (polling now; a future WebhookStatusSource
    too). Does rollup unconditionally, then — only if the issue just reached Done and is
    a workflow step (has a `step:<id>` label) — checks every downstream step's inputs
    and starts whichever just became ready."""
    issue = tickets.get_issue(event.issue_key)
    use_case = _use_case_of(issue) or cfg["use_case"]
    statuses = load_terminology(use_case)["statuses"]

    _rollup(event.issue_key, tickets=tickets, project_key=cfg["jira"]["project_key"],
            statuses=statuses, reopen_on_child_reopen=cfg["orchestrator"].get(
                "reopen_parent_on_child_reopen", False), dry_run=dry_run)

    if event.to_status != statuses["done"]:
        return
    step_id = _step_id_of(issue)
    if not step_id or step_id not in registry:
        return

    steps_map = _steps_map(store, use_case)
    for downstream in registry.all_steps():
        if step_id not in downstream.inputs:
            continue
        if _inputs_satisfied(downstream, steps_map, tickets, statuses):
            _start_step(downstream, steps_map=steps_map, tickets=tickets, store=store,
                        use_case=use_case, cfg=cfg, statuses=statuses, agent=agent, dry_run=dry_run)


# ----------------------------------------------------------------- approval --
def apply_plan(step_id: str, issue_key: str, *, tickets: TicketSystem, store: ObjectStore,
                use_case: str, cfg: dict, done_status: str, dry_run: bool) -> None:
    """Creates one Story per requirement in the stored plan.json (under the use-case
    epic, labelled ws:<workstream>), each with Engineering + Testing Sub-tasks, linked
    to the data_solution_development step. Idempotent via a per-requirement marker
    label. Records traceability (requirement -> DDS/TDS section -> stakeholder
    requirement) in traceability.json and the Jira issue itself (description)."""
    plan = store.get_json(f"workflow/{use_case}/plan.json")
    if not plan:
        tickets.add_comment(issue_key, "No plan found to apply.", dry_run=dry_run)
        return

    epic = tickets.find_issue_by_label(f"sdlc-epic:{use_case}")
    steps_map = _steps_map(store, use_case)
    dsd_key = steps_map.get("data_solution_development")
    traceability = store.get_json(f"workflow/{use_case}/traceability.json") or {"records": []}
    created_keys = []

    for req in plan["requirements"]:
        marker = f"req-marker:{use_case}:{req['requirement_id']}"
        existing = tickets.find_issue_by_label(marker)
        if existing:
            created_keys.append(existing.key)
            continue

        ac_text = "\n".join(f"- {c}" for c in req["acceptance_criteria"])
        description = (f"{req['description']}\n\nAcceptance criteria:\n{ac_text}\n\n"
                        f"Source: {req['source_doc']} § {req['source_section']}")
        story = tickets.create_issue(
            "Story", req["title"], parent_key=epic.key if epic else None, description=description,
            labels=[f"uc:{use_case}", f"ws:{req['workstream']}", marker], dry_run=dry_run,
        )
        tickets.create_issue("Sub-task", f"[Engineering] {req['title']}", parent_key=story.key,
                              component=cfg["jira"]["components"].get("engineer"),
                              assignee=cfg["jira"]["assignees"].get("engineer") or None, dry_run=dry_run)
        tickets.create_issue("Sub-task", f"[Testing] {req['title']}", parent_key=story.key,
                              component=cfg["jira"]["components"].get("tester"),
                              assignee=cfg["jira"]["assignees"].get("tester") or None, dry_run=dry_run)
        if dsd_key:
            tickets.link_issues(story.key, dsd_key, "Blocks", dry_run=dry_run)

        traceability["records"].append({
            "requirement_id": req["requirement_id"], "jira_key": story.key,
            "source_doc": req["source_doc"], "source_section": req["source_section"],
            "stakeholder_requirement_ids": req["stakeholder_requirement_ids"],
        })
        created_keys.append(story.key)

    store.put_json(f"workflow/{use_case}/traceability.json", traceability, dry_run=dry_run)
    tickets.add_comment(issue_key, f"Applied: {len(created_keys)} requirement stories "
                                     f"({', '.join(created_keys)}).", dry_run=dry_run)
    tickets.transition_issue(issue_key, done_status, dry_run=dry_run)


def check_approvals(*, tickets: TicketSystem, store: ObjectStore, cfg: dict, use_case: str,
                     dry_run: bool = True) -> list[str]:
    """Polled once per sync cycle (see cli.py sync-progress), separately from
    handle_status_change — a label add isn't a status change. Returns the step ids that
    were applied or sent back for regeneration this call."""
    statuses = load_terminology(use_case)["statuses"]
    run_state = _run_state(store, use_case)
    steps_map = _steps_map(store, use_case)
    acted = []

    for step_id, state in list(run_state.items()):
        if state != "awaiting_approval":
            continue
        key = steps_map.get(step_id)
        if not key:
            continue
        issue = tickets.get_issue(key)
        if cfg["jira"]["approval_label"] in issue.labels:
            apply_plan(step_id, key, tickets=tickets, store=store, use_case=use_case, cfg=cfg,
                       done_status=statuses["done"], dry_run=dry_run)
            run_state[step_id] = "applied"
            acted.append(step_id)
        elif cfg["jira"]["reject_label"] in issue.labels:
            tickets.add_comment(key, "Rejected — plan will be regenerated.", dry_run=dry_run)
            run_state[step_id] = "ready"
            acted.append(step_id)

    _save_run_state(store, use_case, run_state, dry_run)
    return acted


# ------------------------------------------------------------- workflow start --
def workflow_start(*, tickets: TicketSystem, store: ObjectStore, registry: WorkflowRegistry,
                    cfg: dict, use_case: str, from_step: str | None = None,
                    steps: list[str] | None = None, dry_run: bool = True) -> dict:
    """Creates one epic per use case (if it doesn't already exist) plus one Story per
    selected/artifact-only step, links dependencies with 'Blocks', and persists the
    step_id -> issue_key map. Idempotent: every issue is found by a stable label before
    being created, so re-running never duplicates."""
    usecase_yaml = yaml.safe_load((Path("usecases") / use_case / "usecase.yaml").read_text())
    selection = registry.resolve_selection(from_step=from_step, steps=steps)

    epic_marker = f"sdlc-epic:{use_case}"
    epic = tickets.find_issue_by_label(epic_marker)
    if not epic:
        epic = tickets.create_issue("Epic", f"[{usecase_yaml['display_name']}] Delivery",
                                      labels=[f"uc:{use_case}", epic_marker], dry_run=dry_run)

    steps_map = _steps_map(store, use_case)
    for step in [*selection.artifact_only, *selection.selected]:
        if step.id in steps_map:
            continue
        marker = f"step-marker:{use_case}:{step.id}"
        existing = tickets.find_issue_by_label(marker)
        if existing:
            steps_map[step.id] = existing.key
            continue
        issue = tickets.create_issue(
            "Story", step.artifact, parent_key=epic.key,
            labels=[f"uc:{use_case}", f"step:{step.id}", marker],
            assignee=cfg["jira"]["assignees"].get(step.owner) or None,
            component=cfg["jira"]["components"].get(step.owner), dry_run=dry_run,
        )
        steps_map[step.id] = issue.key
    _save_steps_map(store, use_case, steps_map, dry_run)

    for step in selection.selected:
        for dep_id in step.inputs:
            if dep_id in steps_map and step.id in steps_map:
                tickets.link_issues(steps_map[dep_id], steps_map[step.id], "Blocks", dry_run=dry_run)

    return steps_map
