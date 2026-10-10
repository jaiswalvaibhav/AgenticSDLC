"""Resource ledger: records what `aws-deploy` created, in creation order, so
`aws-destroy` can delete exactly those resources in reverse order. Gitignored
(infra/aws/.ledger*) — this is per-account state, not source.
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PATH = Path("infra/aws/.ledger.json")


@dataclass(frozen=True)
class Resource:
    kind: str  # "cfn-stack" | "knowledge-base" | "data-source" | "agentcore-runtime"
    id: str    # stack name, knowledge base id, or data source id
    extra: dict | None = None  # e.g. {"knowledge_base_id": "..."} for a data source


class Ledger:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.resources: list[Resource] = self._load()

    def _load(self) -> list[Resource]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [Resource(**r) for r in raw.get("resources", [])]

    def append(self, resource: Resource, *, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would record {resource.kind} {resource.id} in {self.path}")
            return
        self.resources.append(resource)
        self._save()

    def remove(self, kind: str, id: str, *, dry_run: bool = True) -> None:
        if dry_run:
            return
        self.resources = [r for r in self.resources if not (r.kind == kind and r.id == id)]
        self._save()

    def find(self, kind: str) -> list[Resource]:
        return [r for r in self.resources if r.kind == kind]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"resources": [asdict(r) for r in self.resources]}, indent=2))
