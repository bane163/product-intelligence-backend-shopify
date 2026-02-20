from __future__ import annotations

from functools import lru_cache
from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=128)
def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **kwargs: object) -> str:
    template = _load_prompt(name)
    if not kwargs:
        return template
    return template.format(**kwargs)
