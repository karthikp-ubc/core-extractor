#!/usr/bin/env python3
"""Shared configuration loader — paths, rounds in scope, contact email.

Every stage in this pipeline is local-file-based except stage3_verify.py
(OpenAlex). See config.yaml's header comment for why, and for what
`contact_email` is and isn't used for.
"""
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class Paths(BaseModel):
    inputs_dir: str = "inputs"
    data_dir: str = "data"
    cache_dir: str = "cache"
    images_dir: str = "images"
    pdfs_dir: str = "pdfs"
    extracted_dir: str = "extracted"


class AnthropicConfig(BaseModel):
    model: str = "claude-sonnet-5"


class OpenAlexConfig(BaseModel):
    base_url: str = "https://api.openalex.org"


class Config(BaseModel):
    contact_email: str = "you@example.com"
    in_scope_rounds: list[str] = Field(default_factory=list)
    paths: Paths = Field(default_factory=Paths)
    rate_limit_seconds: float = 1.0
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    openalex: OpenAlexConfig = Field(default_factory=OpenAlexConfig)

    def resolve(self, name: str) -> Path:
        """Absolute path for one of the configured directories, created if
        missing."""
        rel = getattr(self.paths, name)
        p = (REPO_ROOT / rel).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def contact_email_is_placeholder(self) -> bool:
        return self.contact_email.strip() in ("", "you@example.com")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not Path(path).exists():
        return Config()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Config(**raw)
