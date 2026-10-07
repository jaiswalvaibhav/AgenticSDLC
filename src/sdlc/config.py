"""Config: config.yaml, then .env / environment overrides (SDLC__SECTION__KEY)."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

SECRET_WORDS = ("token", "secret", "password")


def load_config(path: str | Path = "config.yaml") -> dict:
    load_dotenv()
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    for name, value in os.environ.items():
        if not name.startswith("SDLC__"):
            continue
        *parents, leaf = name[len("SDLC__"):].lower().split("__")
        node = cfg
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = yaml.safe_load(value) if value else value
    return cfg


def masked(cfg: dict) -> dict:
    return {
        k: masked(v) if isinstance(v, dict)
        else ("***" if v and any(w in k for w in SECRET_WORDS) else v)
        for k, v in cfg.items()
    }
