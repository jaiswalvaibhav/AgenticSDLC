"""ObjectStore backed by a local folder. Used when profile=local."""
import json
from pathlib import Path


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def get_json(self, key: str) -> dict | None:
        data = self.get_bytes(key)
        return json.loads(data) if data is not None else None

    def put_json(self, key: str, value: dict, dry_run: bool = True) -> None:
        self.put_bytes(key, json.dumps(value, indent=2).encode(), dry_run=dry_run)

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def put_bytes(self, key: str, data: bytes, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would write {self._path(key)} ({len(data)} bytes)")
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def delete(self, key: str, dry_run: bool = True) -> None:
        path = self._path(key)
        if dry_run:
            print(f"[dry-run] would delete {path}")
            return
        path.unlink(missing_ok=True)
