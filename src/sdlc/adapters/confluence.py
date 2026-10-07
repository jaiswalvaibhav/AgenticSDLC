"""Confluence Cloud DocumentSource, using REST API v2.

Verified against developer.atlassian.com/cloud/confluence/rest/v2 (Oct 2026):
- Auth: Basic base64(email:api_token) — still supported for scripts, not for distributed apps.
- GET  /wiki/api/v2/spaces?keys={key}                      -> resolve space key to numeric id
- GET  /wiki/api/v2/pages/{id}?body-format=export_view      -> id, title, spaceId, parentId,
                                                                version.number, body.export_view.value
- GET  /wiki/api/v2/pages/{id}/ancestors                    -> [{id, type}, ...] root-first, no title
- GET  /wiki/api/v2/pages/{id}/descendants?cursor&limit     -> [{id, title, parentId, depth}, ...]
- GET  /wiki/api/v2/pages?id={id1}&id={id2}&...             -> batched metadata (incl. version.number)
                                                                without fetching any body
- GET  /wiki/api/v2/pages/{id}/attachments?cursor&limit     -> [{id, title, downloadLink, mediaType,
                                                                fileSize}, ...]
- GET  /wiki/api/v2/pages?title={t}&space-id={id}            -> find an existing page by title
                                                                (used for idempotent re-seeding)
- POST /wiki/api/v2/pages                                    -> create (body.representation="storage")
- PUT  /wiki/api/v2/pages/{id}                                -> update (needs version.number + 1)
- POST /wiki/rest/api/content/{id}/child/attachment          -> upload an attachment (v1 API —
                                                                v2 has no attachment-upload endpoint;
                                                                multipart/form-data, needs the
                                                                X-Atlassian-Token: nocheck header)
`export_view` is a read-only output format; `storage` is the only representation used for writes.
"""
import base64
import re

import requests

from sdlc.ports import Attachment, Page

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    return _SLUG_RE.sub("-", title.lower()).strip("-") or "untitled"


class ConfluenceClient:
    # Conservative batch size for the GET /pages?id=... metadata pass. The API doesn't
    # document a hard max for this array param; re-check Confluence Cloud rate limits
    # for the account before a large first sync and tune if needed.
    _PAGE_ID_BATCH = 100

    def __init__(self, base_url: str, email: str, api_token: str, space_key: str):
        self.base_url = base_url.rstrip("/")
        self.space_key = space_key
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Basic {token}"
        self._space_id_cache: str | None = None

    # -- low-level -----------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{self.base_url}/wiki/api/v2{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Follows cursor pagination (the `next` link in `_links`)."""
        results: list[dict] = []
        url = f"{self.base_url}/wiki/api/v2{path}"
        while url:
            resp = self._session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            url = data.get("_links", {}).get("next")
            params = None  # the next link already carries the cursor
            if url and url.startswith("/"):
                url = f"{self.base_url}{url}"
        return results

    def _space_id(self) -> str:
        if self._space_id_cache is None:
            spaces = self._get("/spaces", params={"keys": self.space_key}).get("results", [])
            if not spaces:
                raise ValueError(f"no Confluence space found for key {self.space_key!r}")
            self._space_id_cache = spaces[0]["id"]
        return self._space_id_cache

    def _ancestor_path(self, page_id: str) -> str:
        """Titles of every ancestor, root-first, joined with '/' (no page IDs). Live,
        one-off lookup for a single arbitrary page (e.g. resolving an anchor page in
        Phase 6) — NOT used by get_descendants, which builds paths in-memory instead
        to avoid an API call per page. See CLAUDE.md "Enterprise scale: Confluence sync"."""
        ancestors = self._get(f"/pages/{page_id}/ancestors").get("results", [])
        titles = [self._get(f"/pages/{a['id']}")["title"] for a in ancestors]
        return "/".join([self.space_key, *titles])

    def _fetch_versions(self, page_ids: list[str]) -> dict[str, int]:
        """version.number for many pages via GET /pages?id=..., batched, with no body
        fetched at all — the cheap half of the descendants metadata pass."""
        versions: dict[str, int] = {}
        for i in range(0, len(page_ids), self._PAGE_ID_BATCH):
            chunk = page_ids[i:i + self._PAGE_ID_BATCH]
            for item in self._get_all("/pages", params={"id": chunk}):
                versions[item["id"]] = item["version"]["number"]
        return versions

    # -- DocumentSource --------------------------------------------------
    def get_page(self, page_id: str) -> Page:
        data = self._get(f"/pages/{page_id}", params={"body-format": "export_view"})
        return Page(
            page_id=data["id"],
            space_key=self.space_key,
            title=data["title"],
            url=f"{self.base_url}/wiki{data['_links']['webui']}",
            version=data["version"]["number"],
            parent_path=self._ancestor_path(page_id),
            html=data.get("body", {}).get("export_view", {}).get("value", ""),
        )

    def get_descendants(self, root_page_id: str) -> list[Page]:
        """Metadata-only pass over the tree under root_page_id (root included): one
        paginated /descendants call (which already returns id/title/parentId for every
        page) plus one batched /pages?id=... call for versions. No per-page body or
        ancestor-API call, so this scales to a ~3000-page space (O(pages/limit) requests,
        not O(n)). Returned pages have html="" — callers fetch the full body via
        get_page() only for pages that are actually new/changed (see confluence_sync.py
        and CLAUDE.md "Enterprise scale: Confluence sync").

        Note: root_page_id is treated as the top of the mirrored folder tree — its real
        Confluence ancestors above it (if any) are intentionally not included, so a
        use-case sync stays scoped to its own sub-tree regardless of where it sits in
        the wider space."""
        descendants = self._get_all(f"/pages/{root_page_id}/descendants")
        root = self._get(f"/pages/{root_page_id}")
        nodes = {root_page_id: {"title": root["title"], "parentId": None}}
        for d in descendants:
            nodes[d["id"]] = {"title": d["title"], "parentId": d.get("parentId")}

        versions = self._fetch_versions(list(nodes))
        return [
            Page(
                page_id=page_id,
                space_key=self.space_key,
                title=node["title"],
                url="",
                version=versions.get(page_id, 1),
                parent_path=self._path_from_nodes(nodes, page_id),
            )
            for page_id, node in nodes.items()
        ]

    def _path_from_nodes(self, nodes: dict, page_id: str) -> str:
        """Walks the parentId chain already in `nodes` (built from one /descendants
        call) — no extra API calls, unlike `_ancestor_path`."""
        titles: list[str] = []
        node_id = nodes[page_id]["parentId"]
        while node_id:
            titles.insert(0, nodes[node_id]["title"])
            node_id = nodes[node_id]["parentId"]
        return "/".join([self.space_key, *titles])

    def find_page_by_title(self, title: str) -> Page | None:
        results = self._get("/pages", params={"title": title, "space-id": self._space_id()}
                             ).get("results", [])
        return self.get_page(results[0]["id"]) if results else None

    def get_attachments(self, page_id: str) -> list[Attachment]:
        items = self._get_all(f"/pages/{page_id}/attachments")
        return [
            Attachment(
                attachment_id=item["id"],
                title=item["title"],
                media_type=item.get("mediaType", ""),
                download_url=f"{self.base_url}/wiki{item['downloadLink']}",
                file_size=item.get("fileSize", 0),
            )
            for item in items
        ]

    def download_attachment(self, attachment: Attachment) -> bytes:
        resp = self._session.get(attachment.download_url)
        resp.raise_for_status()
        return resp.content

    def add_attachment(self, page_id: str, filename: str, data: bytes,
                        media_type: str = "image/png", dry_run: bool = True) -> Attachment:
        if dry_run:
            print(f"[dry-run] would attach {filename} ({len(data)} bytes) to page {page_id}")
            return Attachment(attachment_id="(dry-run)", title=filename, media_type=media_type,
                               download_url="")
        resp = self._session.post(
            f"{self.base_url}/wiki/rest/api/content/{page_id}/child/attachment",
            headers={"X-Atlassian-Token": "nocheck"},
            files={"file": (filename, data, media_type)},
        )
        resp.raise_for_status()
        item = resp.json()["results"][0]
        return Attachment(attachment_id=item["id"], title=item["title"], media_type=media_type,
                           download_url=f"{self.base_url}/wiki{item['_links']['download']}")

    def create_page(self, parent_id: str | None, title: str, body_html: str,
                     dry_run: bool = True) -> Page:
        payload = {
            "spaceId": self._space_id(),
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": body_html},
        }
        if parent_id:
            payload["parentId"] = parent_id
        if dry_run:
            print(f"[dry-run] would create page {title!r} under parent {parent_id}")
            return Page(page_id="(dry-run)", space_key=self.space_key, title=title,
                        url="", version=1, parent_path="", html=body_html)
        resp = self._session.post(f"{self.base_url}/wiki/api/v2/pages", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return Page(page_id=data["id"], space_key=self.space_key, title=data["title"],
                     url=f"{self.base_url}/wiki{data['_links']['webui']}",
                     version=data["version"]["number"], parent_path=self._ancestor_path(data["id"]),
                     html=body_html)

    def append_to_page(self, page_id: str, body_html: str, dry_run: bool = True) -> Page:
        current = self._get(f"/pages/{page_id}", params={"body-format": "storage"})
        new_value = current["body"]["storage"]["value"] + body_html
        if dry_run:
            print(f"[dry-run] would append {len(body_html)} chars to page {page_id}")
            return self.get_page(page_id)
        payload = {
            "id": page_id,
            "status": "current",
            "title": current["title"],
            "spaceId": current["spaceId"],
            "body": {"representation": "storage", "value": new_value},
            "version": {"number": current["version"]["number"] + 1, "message": "sdlc append"},
        }
        resp = self._session.put(f"{self.base_url}/wiki/api/v2/pages/{page_id}", json=payload)
        resp.raise_for_status()
        return self.get_page(page_id)
