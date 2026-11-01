"""读取提示词模板：中文在 prompts/，英文在 prompts/en/。每个文件用一行 ---USER--- 分开 system 和 user，变量写成 $name。"""
from __future__ import annotations

from importlib import resources
from string import Template

SEPARATOR = "---USER---"
NAMES = ("plan_queries", "screen", "read_paper", "mine_gaps", "seeds", "write_proposal", "meta_review", "chair")


def raw(name: str, lang: str = "zh") -> str:
    folder = resources.files("idea_review").joinpath("prompts")
    if lang != "zh":
        folder = folder.joinpath(lang)
    return folder.joinpath(f"{name}.md").read_text(encoding="utf-8")


def render(name: str, lang: str = "zh", **values) -> tuple[str, str]:
    text = raw(name, lang)
    if SEPARATOR not in text:
        raise ValueError(f"提示词 {name}.md 里缺少 {SEPARATOR}")
    system, user = text.split(SEPARATOR, 1)
    values = {k: str(v) for k, v in values.items()}
    return Template(system.strip()).safe_substitute(values), Template(user.strip()).safe_substitute(values)
