"""`mock-jira-rename`: copies a mock Jira board to a new project key (and name), rewriting every
reference to the old keys (issue keys, parents, links, ranking, history, default sprint names) and
the saved runs that point at them. The old board is left untouched, as the backup; delete
`mock_jira/<OLD>/` yourself once you're happy. Stop the UI backend first, so nothing writes to the
old board while it is copied, and restart it after changing `jira.mock.project_key` in config.yaml."""
import re

from sdlc.ports import ObjectStore


def _prefix(project_key: str) -> str:
    return f"mock_jira/{project_key}/"


def rename_project(store: ObjectStore, old: str, new: str, new_name: str, use_cases: list[str],
                   dry_run: bool = True) -> dict:
    board = store.get_json(f"{_prefix(old)}board.json")
    if board is None:
        raise ValueError(f"there is no mock Jira board for project {old}")
    if store.get_json(f"{_prefix(new)}board.json") is not None:
        raise ValueError(f"project {new} already has a board; refusing to overwrite it")

    key_pattern = re.compile(rf"^{re.escape(old)}-(\d+)$")

    def rekey(node):  # a string that is exactly an old issue key becomes the new one
        if isinstance(node, str):
            match = key_pattern.match(node)
            return f"{new}-{match.group(1)}" if match else node
        if isinstance(node, list):
            return [rekey(n) for n in node]
        if isinstance(node, dict):
            return {k: rekey(v) for k, v in node.items()}
        return node

    issues = {}
    for key in board["rank"]:
        doc = store.get_json(f"{_prefix(old)}issues/{key}.json")
        if doc is not None:  # a key with no file is the crash gap MockJiraClient tolerates
            issues[key] = doc
    attachment_ids = [a["id"] for doc in issues.values() for a in doc["attachments"]]

    new_board = rekey(board)
    new_board["project"] = {"key": new, "name": new_name}
    for sprint in new_board["sprints"]:
        if sprint["name"].startswith(f"{old} Sprint"):  # only the generated names
            sprint["name"] = sprint["name"].replace(f"{old} Sprint", f"{new} Sprint", 1)
    new_board["rank"] = [k for k in new_board["rank"] if k.replace(f"{new}-", f"{old}-", 1) in issues]

    runs = {}  # saved runs (workflow start/UI) that name the old keys
    for use_case in use_cases:
        steps = store.get_json(f"workflow/{use_case}/steps.json")
        selection = store.get_json(f"ui/{use_case}/selection.json")
        if steps:
            runs[f"workflow/{use_case}/steps.json"] = rekey(steps)
        if selection and selection.get("project_key") == old:
            runs[f"ui/{use_case}/selection.json"] = {**selection, "project_key": new}

    result = {"issues": len(issues), "attachments": len(attachment_ids), "sprints": len(board["sprints"]),
              "saved_runs": sorted(runs)}
    if dry_run:
        return result
    for att_id in attachment_ids:
        data = store.get_bytes(f"{_prefix(old)}attachments/{att_id}")
        if data is not None:
            store.put_bytes(f"{_prefix(new)}attachments/{att_id}", data, dry_run=False)
    for key, doc in issues.items():
        new_doc = rekey(doc)
        store.put_json(f"{_prefix(new)}issues/{new_doc['key']}.json", new_doc, dry_run=False)
    store.put_json(f"{_prefix(new)}board.json", new_board, dry_run=False)  # last: it is what makes the board visible
    for key, value in runs.items():
        store.put_json(key, value, dry_run=False)
    return result
