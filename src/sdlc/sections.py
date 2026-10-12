"""The ticket sections beyond the description: a brief description, and three checklists the team
ticks off (acceptance criteria, definition of done, test coverage). The analyst's plan fills them
(agents/analyst/plan_schema.py); the mock Jira stores them as real fields on Story/Task/Bug tickets.
Real Jira has no such fields, so JiraClient appends them to the description as text."""

CHECKLISTS = ("acceptance_criteria", "definition_of_done", "test_coverage")
SECTIONS = ("brief_description", *CHECKLISTS)
TITLES = {"brief_description": "Brief description", "acceptance_criteria": "Acceptance criteria",
          "definition_of_done": "Definition of done", "test_coverage": "Test coverage"}
SECTION_TYPES = {"story", "task", "bug"}  # lower-cased issue type names that carry the sections


def has_sections(issue_type: str) -> bool:
    return issue_type.lower() in SECTION_TYPES


def checklist(items) -> list[dict]:
    """[{"text", "done"}]; a plain string is an item that is not done yet, blank items are dropped."""
    result = []
    for item in items or []:
        if isinstance(item, str):
            item = {"text": item}
        elif not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError(f"a checklist item is a string or {{text, done}}, not {item!r}")
        if item["text"].strip():
            result.append({"text": item["text"].strip(), "done": bool(item.get("done"))})
    return result


def normalise(name: str, value):
    if name == "brief_description":
        if not isinstance(value, str):
            raise ValueError("brief_description must be text")
        return value.strip()
    return checklist(value)


def progress(items: list[dict]) -> str:
    return f"{len(items)} items, {sum(1 for i in items if i['done'])} done"


def render_sections(sections: dict) -> str:
    """The sections as plain text, for a tracker that has no fields for them."""
    parts = []
    brief = (sections.get("brief_description") or "").strip()
    if brief:
        parts.append(f"{TITLES['brief_description']}:\n{brief}")
    for name in CHECKLISTS:
        items = checklist(sections.get(name))
        if items:
            parts.append(f"{TITLES[name]}:\n" + "\n".join(f"- {i['text']}" for i in items))
    return "\n\n".join(parts)
