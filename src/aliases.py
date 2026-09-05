#!/usr/bin/env python3
"""Load and validate aliases.yaml — SPEC.md §7.

Shared by stage3_verify.py (needs openalex_source_ids) and stage4_join.py
(needs dblp_key / exclude_notes for the join). Kept as its own module so
both can import the same validation without duplicating it.
"""
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALIASES_PATH = REPO_ROOT / "aliases.yaml"


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are a hard error

    key: str
    core_id: Optional[str] = None
    dblp_key: Optional[str] = None
    openalex_source_ids: list[str] = []
    exclude_notes: Optional[str] = None


class AliasValidationError(ValueError):
    pass


def load_aliases(path: Path = DEFAULT_ALIASES_PATH) -> list[Alias]:
    if not Path(path).exists():
        return []
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise AliasValidationError(
            f"{path}: expected a YAML list of alias entries, got {type(raw).__name__}")

    aliases = []
    seen_keys = set()
    for i, entry in enumerate(raw):
        try:
            alias = Alias(**entry)
        except ValidationError as exc:
            raise AliasValidationError(
                f"{path}: entry {i} ({entry!r}) failed validation:\n{exc}") from exc
        if alias.key in seen_keys:
            raise AliasValidationError(
                f"{path}: duplicate key {alias.key!r}")
        seen_keys.add(alias.key)
        aliases.append(alias)
    return aliases


def check_openalex_ids_live(aliases: list[Alias], fetch_fn) -> list[str]:
    """Hard-error list of (alias key, source id) pairs that 404. `fetch_fn`
    takes an OpenAlex source id and returns True if it resolves, False on a
    404 — injected so this stays testable without a real network call."""
    errors = []
    for alias in aliases:
        for source_id in alias.openalex_source_ids:
            if not fetch_fn(source_id):
                errors.append(f"{alias.key}: OpenAlex source {source_id} "
                               f"does not resolve (404)")
    return errors
