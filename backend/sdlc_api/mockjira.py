"""The mock Jira's HTTP API (/api/mockjira), mounted by create_app only when jira.mode is
"mock". A thin layer over MockJiraClient: rule violations (ValueError, TransitionNotAvailable)
become 400, a missing issue/sprint (KeyError) 404. Every write calls `changed`, which drops
the cached progress snapshots and wakes the poller, so e.g. dragging a step to Done on the
board starts the next step now rather than at the next interval.
"""
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from sdlc.ports import TransitionNotAvailable


class IssueCreate(BaseModel):
    issue_type: str
    summary: str
    description: str = ""
    parent: str | None = None
    labels: list[str] = Field(default_factory=list)
    assignee: str | None = None
    priority: str | None = None
    story_points: float | None = None
    sprint_id: int | None = None


class ChecklistItem(BaseModel):
    text: str
    done: bool = False


class IssueEdit(BaseModel):  # only the fields sent are changed
    brief_description: str | None = None
    acceptance_criteria: list[ChecklistItem] | None = None  # the whole list: items, order and ticks
    definition_of_done: list[ChecklistItem] | None = None
    test_coverage: list[ChecklistItem] | None = None
    summary: str | None = None
    description: str | None = None
    assignee: str | None = None
    priority: str | None = None
    story_points: float | None = None
    components: list[str] | None = None
    parent: str | None = None


class Transition(BaseModel):
    status: str


class Label(BaseModel):
    label: str


class Comment(BaseModel):
    body: str


class WebLink(BaseModel):
    url: str
    title: str = ""


class IssueLink(BaseModel):
    issue: str
    relation: str = "depends on"  # "depends on" | "is depended by": this issue, relative to `issue`


class SprintCreate(BaseModel):
    name: str | None = None
    goal: str = ""


class SprintEdit(BaseModel):
    name: str | None = None
    goal: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class SprintStart(BaseModel):
    start_date: str | None = None
    end_date: str | None = None


class SprintComplete(BaseModel):
    move_to: int | None = None  # a future sprint; None = back to the backlog


class Rank(BaseModel):
    keys: list[str]
    sprint_id: int | None = None
    before_key: str | None = None


def _do(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0]))
    except (ValueError, TransitionNotAvailable) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def build_router(client_for, changed) -> APIRouter:
    """client_for(profile) -> a MockJiraClient acting as the UI's user; changed() after writes."""
    router = APIRouter(prefix="/api/mockjira")

    @router.get("/board")
    def board(profile: str | None = None) -> dict:
        return client_for(profile).board_view()

    @router.get("/issues/{key}")
    def issue(key: str, profile: str | None = None) -> dict:
        view = _do(client_for(profile).issue_view, key)
        view["attachments"] = [{**a, "url": f"/api/mockjira/issues/{key}/attachments/{a['id']}"}
                               for a in view["attachments"]]
        return view

    @router.post("/issues")
    def create_issue(body: IssueCreate, profile: str | None = None) -> dict:
        jira = client_for(profile)
        created = _do(jira.create_issue, body.issue_type, body.summary, parent_key=body.parent,
                      description=body.description, labels=body.labels, assignee=body.assignee,
                      dry_run=False)
        extra = {f: v for f, v in (("priority", body.priority), ("story_points", body.story_points))
                 if v is not None}
        if extra:
            _do(jira.update_fields, created.key, **extra)
        if body.sprint_id is not None:
            _do(jira.rank, [created.key], body.sprint_id)
        changed()
        return {"key": created.key}

    @router.patch("/issues/{key}")
    def edit_issue(key: str, body: IssueEdit, profile: str | None = None) -> dict:
        _do(client_for(profile).update_fields, key, **body.model_dump(exclude_unset=True))
        changed()
        return {"key": key}

    @router.delete("/issues/{key}")
    def delete_issue(key: str, profile: str | None = None) -> dict:
        jira = client_for(profile)
        _do(jira.get_issue, key)  # 404 for a key that doesn't exist
        jira.delete_issue(key, dry_run=False)
        changed()
        return {"deleted": key}

    @router.post("/issues/{key}/transitions")
    def transition(key: str, body: Transition, profile: str | None = None) -> dict:
        _do(client_for(profile).transition_issue, key, body.status, dry_run=False)
        changed()
        return {"key": key, "status": body.status}

    @router.post("/issues/{key}/labels")
    def add_label(key: str, body: Label, profile: str | None = None) -> dict:
        _do(client_for(profile).add_label, key, body.label, dry_run=False)
        changed()
        return {"key": key}

    @router.delete("/issues/{key}/labels/{label}")
    def remove_label(key: str, label: str, profile: str | None = None) -> dict:
        _do(client_for(profile).remove_label, key, label, dry_run=False)
        changed()
        return {"key": key}

    @router.post("/issues/{key}/comments")
    def add_comment(key: str, body: Comment, profile: str | None = None) -> dict:
        _do(client_for(profile).add_comment, key, body.body, dry_run=False)
        changed()
        return {"key": key}

    @router.patch("/issues/{key}/comments/{comment_id}")
    def edit_comment(key: str, comment_id: str, body: Comment, profile: str | None = None) -> dict:
        _do(client_for(profile).update_comment, key, comment_id, body.body)
        changed()
        return {"key": key}

    @router.delete("/issues/{key}/comments/{comment_id}")
    def delete_comment(key: str, comment_id: str, profile: str | None = None) -> dict:
        _do(client_for(profile).delete_comment, key, comment_id)
        changed()
        return {"key": key}

    @router.post("/issues/{key}/attachments")
    async def add_attachment(key: str, file: UploadFile, profile: str | None = None) -> dict:
        data = await file.read()
        att = _do(client_for(profile).add_attachment, key, file.filename or "file", data,
                  file.content_type or "application/octet-stream", dry_run=False)
        changed()
        return {"id": att.attachment_id, "filename": att.title}

    @router.get("/issues/{key}/attachments/{attachment_id}")
    def download_attachment(key: str, attachment_id: str, profile: str | None = None) -> Response:
        jira = client_for(profile)
        att = next((a for a in _do(jira.get_attachments, key) if a.attachment_id == attachment_id), None)
        if att is None:
            raise HTTPException(status_code=404, detail=f"attachment {attachment_id} not found")
        media_type = att.media_type or "application/octet-stream"
        # images display in place (comments embed them); everything else downloads. Sandboxed and
        # un-sniffed, so an uploaded SVG or HTML file can't run script on this origin.
        disposition = "inline" if media_type.startswith("image/") else "attachment"
        return Response(content=_do(jira.download_attachment, att), media_type=media_type,
                        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(att.title)}",
                                 "Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})

    @router.delete("/issues/{key}/attachments/{attachment_id}")
    def delete_attachment(key: str, attachment_id: str, profile: str | None = None) -> dict:
        _do(client_for(profile).delete_attachment, key, attachment_id)
        changed()
        return {"key": key}

    @router.post("/issues/{key}/weblinks")
    def add_weblink(key: str, body: WebLink, profile: str | None = None) -> dict:
        _do(client_for(profile).add_remote_link, key, body.url, body.title or body.url, dry_run=False)
        changed()
        return {"key": key}

    @router.delete("/issues/{key}/weblinks/{link_id}")
    def delete_weblink(key: str, link_id: str, profile: str | None = None) -> dict:
        _do(client_for(profile).delete_remote_link, key, link_id)
        changed()
        return {"key": key}

    @router.post("/issues/{key}/links")
    def link(key: str, body: IssueLink, profile: str | None = None) -> dict:
        if body.relation not in ("depends on", "is depended by"):
            raise HTTPException(status_code=400, detail="relation must be 'depends on' or 'is depended by'")
        jira = client_for(profile)
        _do(jira.get_issue, body.issue)
        # stored as Jira's "Blocks" link (the orchestrator and real Jira use that name): the issue
        # that is depended on is the blocker, and `link_issues` takes the blocker first
        blocker, dependent = (body.issue, key) if body.relation == "depends on" else (key, body.issue)
        _do(jira.link_issues, blocker, dependent, "Blocks", dry_run=False)
        changed()
        return {"key": key}

    @router.delete("/issues/{key}/links/{other_key}")
    def unlink(key: str, other_key: str, profile: str | None = None) -> dict:
        _do(client_for(profile).unlink_issues, key, other_key)
        changed()
        return {"key": key}

    @router.post("/sprints")
    def create_sprint(body: SprintCreate, profile: str | None = None) -> dict:
        sprint = _do(client_for(profile).create_sprint, body.name, body.goal)
        changed()
        return sprint

    @router.patch("/sprints/{sprint_id}")
    def edit_sprint(sprint_id: int, body: SprintEdit, profile: str | None = None) -> dict:
        sprint = _do(client_for(profile).update_sprint, sprint_id, **body.model_dump(exclude_unset=True))
        changed()
        return sprint

    @router.delete("/sprints/{sprint_id}")
    def delete_sprint(sprint_id: int, profile: str | None = None) -> dict:
        _do(client_for(profile).delete_sprint, sprint_id)
        changed()
        return {"deleted": sprint_id}

    @router.post("/sprints/{sprint_id}/start")
    def start_sprint(sprint_id: int, body: SprintStart | None = None, profile: str | None = None) -> dict:
        body = body or SprintStart()
        sprint = _do(client_for(profile).start_sprint, sprint_id, body.start_date, body.end_date)
        changed()
        return sprint

    @router.post("/sprints/{sprint_id}/complete")
    def complete_sprint(sprint_id: int, body: SprintComplete | None = None,
                        profile: str | None = None) -> dict:
        result = _do(client_for(profile).complete_sprint, sprint_id, (body or SprintComplete()).move_to)
        changed()
        return result

    @router.post("/rank")
    def rank(body: Rank, profile: str | None = None) -> dict:
        _do(client_for(profile).rank, body.keys, body.sprint_id, body.before_key)
        changed()
        return {"ranked": body.keys}

    return router
