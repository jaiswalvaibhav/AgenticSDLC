"""The workflow orchestrator: `workflow_start` (creates the use-case epic + step
tickets), one `handle_status_change` handler (rollup, then step readiness + automation
trigger), and `check_approvals` (the sdlc-approved/-rejected label check — polled
separately, since a label add isn't a status change; see CLAUDE.md "Workflow").

`_start_step` dispatches automated steps through AgentRuntime (LocalAgentRuntime for
profile=local, AgentCore for profile=aws — see wiring.py); `apply_plan` turns an
approved plan.json into real Story + Engineering/Testing Sub-task tickets.

Confirmed by the user: the "AgenticSDLC" Jira project is team-managed, so rollup's
JQL `parent = <key>` is correct as-is for Epic/Story/Sub-task (see adapters/jira.py's
docstring for the Jira Cloud shapes this relies on).
"""
import re
from pathlib import Path

import yaml

from sdlc.agents.analyst.ticket_anchors import AnchorMissing, confluence_links_in_text, pick_anchor_source
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
        # has_artifact = an attachment or Web link; a Confluence page pasted into the ticket's
        # description/comments counts too (the same rule the analyst's anchor lookup uses)
        if issue.status != statuses["done"] or not (
                tickets.has_artifact(key) or confluence_links_in_text(key, tickets)):
            return False
    return True


def _run_agent(step: Step, key: str, *, tickets: TicketSystem, use_case: str, agent: AgentRuntime,
               dry_run: bool) -> str | None:
    """Runs the step's automated task and returns its status ("planned" when it produced a
    plan, "blocked" when it already commented why not), or "failed" if it raised — reported
    on the ticket, since otherwise it would only crash the poll cycle with nobody told."""
    try:
        result = agent.run(step.automation, {"use_case": use_case, "step_id": step.id,
                                               "issue_key": key, "dry_run": dry_run})
    except Exception as exc:
        tickets.add_comment(key, f"Agent run failed: {type(exc).__name__}: {exc}", dry_run=dry_run)
        return "failed"
    return result.get("status")


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
        # Only move to awaiting_approval when the task actually produced a plan —
        # e.g. engine.run_solution_requirements returns status "blocked" (and posts
        # its own explanatory comment) when an anchor isn't configured yet, in which
        # case the step ticket should stay as-is, not get a misleading "plan ready"
        # comment on top of the real one.
        if _run_agent(step, key, tickets=tickets, use_case=use_case, agent=agent, dry_run=dry_run) == "planned":
            run_state = _run_state(store, use_case)
            run_state[step.id] = "awaiting_approval"
            _save_run_state(store, use_case, run_state, dry_run)


def _check_anchor_artifact(step_id: str, issue_key: str, *, tickets: TicketSystem,
                            registry: WorkflowRegistry, dry_run: bool) -> None:
    """Done stays a human decision, but if the step feeds an automated step, its ticket must
    carry something the agent can read in full (see ticket_anchors) — else say so now, rather
    than only when the agent later blocks. Attaching after Done fires no status event, so the
    comment asks for a reopen."""
    if not any(s.automation and step_id in s.inputs for s in registry.all_steps()):
        return
    try:
        pick_anchor_source(issue_key, tickets)
    except AnchorMissing as exc:
        tickets.add_comment(
            issue_key, f"Marked Done but no usable artifact ({exc.reason}). Move it back to In "
                       "Progress, attach or link it, then mark Done again.", dry_run=dry_run)


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

    _check_anchor_artifact(step_id, event.issue_key, tickets=tickets, registry=registry, dry_run=dry_run)
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

    terminology = load_terminology(use_case)
    issue_types, statuses = terminology["issue_types"], terminology["statuses"]
    epic = tickets.find_issue_by_label(f"sdlc-epic:{use_case}")
    steps_map = _steps_map(store, use_case)
    dsd_key = steps_map.get("data_solution_development")
    traceability = store.get_json(f"workflow/{use_case}/traceability.json") or {"records": []}
    created_keys = []
    new_story_keys = []

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
            issue_types["story"], req["title"], parent_key=epic.key if epic else None, description=description,
            labels=[f"uc:{use_case}", f"ws:{req['workstream']}", marker], dry_run=dry_run,
        )
        tickets.create_issue(issue_types["subtask"], f"[Engineering] {req['title']}", parent_key=story.key,
                              component=cfg["jira"]["components"].get("engineer"),
                              assignee=cfg["jira"]["assignees"].get("engineer") or None, dry_run=dry_run)
        tickets.create_issue(issue_types["subtask"], f"[Testing] {req['title']}", parent_key=story.key,
                              component=cfg["jira"]["components"].get("tester"),
                              assignee=cfg["jira"]["assignees"].get("tester") or None, dry_run=dry_run)
        if dsd_key:
            tickets.link_issues(story.key, dsd_key, "Blocks", dry_run=dry_run)

        trace_record = {
            "requirement_id": req["requirement_id"], "jira_key": story.key,
            "source_doc": req["source_doc"], "source_section": req["source_section"],
            "stakeholder_requirement_ids": req["stakeholder_requirement_ids"],
        }
        traceability["records"].append(trace_record)
        # Also stamped directly on the issue (a Jira entity property), so the
        # back-trace is readable from Jira itself without our ObjectStore.
        tickets.set_property(story.key, "sdlc.trace", trace_record, dry_run=dry_run)
        created_keys.append(story.key)
        new_story_keys.append(story.key)

    store.put_json(f"workflow/{use_case}/traceability.json", traceability, dry_run=dry_run)

    # Same sprint placement as workflow_start's step Stories (not backlog, not the
    # active sprint) — see docs/DECISIONS.md "Decisions (sprint placement + status transitions)".
    if new_story_keys:
        sprint_id = tickets.get_or_create_future_sprint(dry_run=dry_run)
        tickets.add_issues_to_sprint(sprint_id, new_story_keys, dry_run=dry_run)

    tickets.add_comment(issue_key, f"Applied: {len(created_keys)} requirement stories "
                                     f"({', '.join(created_keys)}).", dry_run=dry_run)

    # Never jump straight To Do -> Done: pass through In Progress first, matching how
    # a step would have been transitioned had it gone through the normal orchestrator
    # flow (_start_step already does To Do -> In Progress before the agent runs).
    current_status = tickets.get_issue(issue_key).status
    try:
        if current_status == statuses["todo"]:
            tickets.transition_issue(issue_key, statuses["in_progress"], dry_run=dry_run)
        tickets.transition_issue(issue_key, done_status, dry_run=dry_run)
    except TransitionNotAvailable:
        tickets.add_comment(
            issue_key, f"Applied, but the workflow doesn't allow transitioning to "
                       f"{done_status} from here.", dry_run=dry_run)


def check_approvals(*, tickets: TicketSystem, store: ObjectStore, cfg: dict, use_case: str,
                     dry_run: bool = True, registry: WorkflowRegistry | None = None,
                     agent: AgentRuntime | None = None) -> list[str]:
    """Polled once per sync cycle (see cli.py sync-progress), separately from
    handle_status_change — a label add isn't a status change. Returns the step ids acted
    on this call. Approval applies the plan. Rejection removes the label and stops the step
    ("rejected") — the plan is NOT regenerated; the user fixes the issues and asks for a
    re-run (run state "rerun_requested"), which runs the agent again here."""
    statuses = load_terminology(use_case)["statuses"]
    run_state = _run_state(store, use_case)
    steps_map = _steps_map(store, use_case)
    acted = []

    for step_id, state in list(run_state.items()):
        key = steps_map.get(step_id)
        if not key:
            continue
        if state == "rerun_requested":
            step = registry[step_id] if registry and step_id in registry else None
            if agent is None or step is None or not step.automation:
                continue
            run_state[step_id] = "regenerating"  # visible while the agent runs (a UI shows no buttons)
            _save_run_state(store, use_case, run_state, dry_run)
            planned = _run_agent(step, key, tickets=tickets, use_case=use_case, agent=agent,
                                 dry_run=dry_run) == "planned"
            run_state[step_id] = "awaiting_approval" if planned else "rejected"
            acted.append(step_id)
            continue
        if state != "awaiting_approval":
            continue
        issue = tickets.get_issue(key)
        if cfg["jira"]["approval_label"] in issue.labels:
            apply_plan(step_id, key, tickets=tickets, store=store, use_case=use_case, cfg=cfg,
                       done_status=statuses["done"], dry_run=dry_run)
            run_state[step_id] = "applied"
            acted.append(step_id)
        elif cfg["jira"]["reject_label"] in issue.labels:
            tickets.remove_label(key, cfg["jira"]["reject_label"], dry_run=dry_run)
            tickets.add_comment(key, "Rejected — the plan was not regenerated. Fix the issues, "
                                     "then re-run this step.", dry_run=dry_run)
            run_state[step_id] = "rejected"
            acted.append(step_id)

    _save_run_state(store, use_case, run_state, dry_run)
    return acted


# ------------------------------------------------------------- workflow start --
def workflow_start(*, tickets: TicketSystem, store: ObjectStore, registry: WorkflowRegistry,
                    cfg: dict, use_case: str, from_step: str | None = None,
                    steps: list[str] | None = None, targets: list[str] | None = None,
                    dry_run: bool = True) -> dict:
    """Creates one epic per use case (if it doesn't already exist) plus one Story per
    selected/artifact-only step, links dependencies with 'Blocks', and persists the
    step_id -> issue_key map. Idempotent: every issue is found by a stable label before
    being created, so re-running never duplicates. `targets` (the UI's mode) selects
    exactly those steps plus their direct inputs, instead of from_step/steps."""
    usecase_yaml = yaml.safe_load((Path("usecases") / use_case / "usecase.yaml").read_text())
    selection = (registry.resolve_targets(targets) if targets
                 else registry.resolve_selection(from_step=from_step, steps=steps))

    issue_types = load_terminology(use_case)["issue_types"]
    epic_marker = f"sdlc-epic:{use_case}"
    epic = tickets.find_issue_by_label(epic_marker)
    if not epic:
        epic = tickets.create_issue(issue_types["epic"], f"[{usecase_yaml['display_name']}] Delivery",
                                      labels=[f"uc:{use_case}", epic_marker], dry_run=dry_run)

    steps_map = _steps_map(store, use_case)
    new_story_keys = []
    for step in [*selection.artifact_only, *selection.selected]:
        if step.id in steps_map:
            continue
        marker = f"step-marker:{use_case}:{step.id}"
        existing = tickets.find_issue_by_label(marker)
        if existing:
            steps_map[step.id] = existing.key
            continue
        issue = tickets.create_issue(
            issue_types["story"], step.artifact, parent_key=epic.key,
            labels=[f"uc:{use_case}", f"step:{step.id}", marker],
            assignee=cfg["jira"]["assignees"].get(step.owner) or None,
            component=cfg["jira"]["components"].get(step.owner), dry_run=dry_run,
        )
        steps_map[step.id] = issue.key
        new_story_keys.append(issue.key)
    _save_steps_map(store, use_case, steps_map, dry_run)

    # Newly created step Stories go straight into the next (not-yet-started) sprint,
    # not the backlog and not the currently active sprint — per the user's explicit
    # choice, see docs/DECISIONS.md "Decisions (sprint placement + status transitions)".
    if new_story_keys:
        sprint_id = tickets.get_or_create_future_sprint(dry_run=dry_run)
        tickets.add_issues_to_sprint(sprint_id, new_story_keys, dry_run=dry_run)

    for step in selection.selected:
        for dep_id in step.inputs:
            if dep_id in steps_map and step.id in steps_map:
                tickets.link_issues(steps_map[dep_id], steps_map[step.id], "Blocks", dry_run=dry_run)

    return steps_map
