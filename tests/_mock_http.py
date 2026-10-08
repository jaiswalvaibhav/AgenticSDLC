"""Shared helper for mocking requests.Session calls in adapter unit tests (JiraClient,
ConfluenceClient) — no real network, no extra test dependency."""
from dataclasses import dataclass


@dataclass
class FakeResponse:
    json_data: dict | list | None = None
    status_code: int = 200
    content: bytes = b"{}"

    def json(self):
        return self.json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Records every call and returns responses from a queue keyed by (method, URL
    suffix) — good enough for these adapters' sequential, predictable call patterns."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []  # (method, url, json/params)
        self.responses: dict[str, list[FakeResponse]] = {}
        self.headers: dict = {}

    def queue(self, method: str, url_suffix: str, response: FakeResponse) -> None:
        self.responses.setdefault(f"{method}:{url_suffix}", []).append(response)

    def _respond(self, method: str, url: str) -> FakeResponse:
        # Match by suffix, not "contains": these adapters' URLs share long common
        # prefixes (e.g. .../wiki/api/v2/pages vs .../wiki/api/v2/pages/1), so "in"
        # matching is ambiguous. Longest match still wins as a tiebreaker.
        candidates = []
        for key, queue in self.responses.items():
            resp_method, suffix = key.split(":", 1)
            if resp_method == method and url.endswith(suffix) and queue:
                candidates.append((len(suffix), key, queue))
        if not candidates:
            raise AssertionError(f"no queued {method} response matching {url!r}")
        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][2].pop(0)

    def get(self, url, params=None, **kwargs):
        self.calls.append(("GET", url, params))
        return self._respond("GET", url)

    def post(self, url, json=None, **kwargs):
        self.calls.append(("POST", url, json))
        return self._respond("POST", url)

    def put(self, url, json=None, **kwargs):
        self.calls.append(("PUT", url, json))
        return self._respond("PUT", url)
