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


def save_aws_values(updates: dict, path: str | Path = "config.yaml") -> None:
    """Writes only the given aws.* keys (e.g. knowledge_base_id, agent_runtime_arn)
    back into config.yaml, re-reading the raw file fresh rather than re-dumping the
    env-merged load_config() result — which can carry secrets from .env overrides
    (SDLC__ATLASSIAN__API_TOKEN etc.) that must never land in the tracked
    config.yaml. Used by aws/deploy.py, aws/jira_kb_deploy.py and aws/agent_deploy.py
    after they create or discover ids later commands need."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    raw.setdefault("aws", {}).update(updates)
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
