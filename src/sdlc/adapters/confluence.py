"""Confluence Cloud DocumentSource, using REST API v2.

Verified against developer.atlassian.com/cloud/confluence/rest/v2 (Oct 2026):
- Auth: Basic base64(email:api_token) — still supported for scripts, not for distributed apps.
- GET  /wiki/api/v2/spaces?keys={key}                      -> resolve space key to numeric id
- GET  /wiki/api/v2/pages/{id}?body-format=export_view      -> id, title, spaceId, parentId,
                                                                version.number, body.export_view.value
- GET  /wiki/api/v2/pages/{id}/ancestors                    -> [{id, type}, ...] root-first, no title
- GET  /wiki/api/v2/pages/{id}/descendants?cursor&limit     -> [{id, title, parentId, depth}, ...]
- GET  /wiki/api/v2/pages/{id}/attachments?cursor&limit     -> [{id, title, downloadLink, mediaType,
                                                                fileSize}, ...]
- POST /wiki/api/v2/pages                                    -> create (body.representation="storage")
- PUT  /wiki/api/v2/pages/{id}                                -> update (needs version.number + 1)
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
        """Titles of every ancestor, root-first, joined with '/' (no page IDs)."""
        ancestors = self._get(f"/pages/{page_id}/ancestors").get("results", [])
        titles = [self._get(f"/pages/{a['id']}")["title"] for a in ancestors]
        return "/".join([self.space_key, *titles])

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
        """Full page tree under root_page_id (root included). One get_page() call per
        descendant — simple and correct; the demo corpus is small enough that this isn't
        a bottleneck, so we don't add a lighter metadata-only path.

        TODO before syncing a real enterprise space (~3000 pages): this is O(n) full-body
        fetches per sync. See "Known limitation" in CLAUDE.md for the fix (a cheap
        GET /pages?id=... metadata pass to diff versions, then full get_page() only for
        the pages that actually changed)."""
        descendants = self._get_all(f"/pages/{root_page_id}/descendants")
        return [self.get_page(root_page_id)] + [self.get_page(d["id"]) for d in descendants]

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

    def create_page(self, parent_id: str, title: str, body_html: str, dry_run: bool = True) -> Page:
        payload = {
            "spaceId": self._space_id(),
            "status": "current",
            "title": title,
            "parentId": parent_id,
            "body": {"representation": "storage", "value": body_html},
        }
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
