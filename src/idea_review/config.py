"""读取 config.toml：模型接口、各环节用哪个模型、检索和评审参数。"""
from __future__ import annotations

import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .texts import LANGS

PROVIDER_TYPES = ("anthropic", "openai_compatible", "claude_code", "codex", "gemini_cli", "codebuddy", "mock")
# 调用本机 code agent 命令行、走它登录的订阅的几种类型：默认命令名和默认 family
AGENT_CLIS = {
    "claude_code": ("claude", "anthropic"),
    "codex": ("codex", "openai"),
    "gemini_cli": ("gemini", "google"),
    "codebuddy": ("codebuddy", "tencent"),
}
# codebuddy（腾讯 CodeBuddy，和 WorkBuddy 同一套）不填 model 时用混元 hy3。它也能调别家的模型，所以 family 按模型名定
CODEBUDDY_DEFAULT_MODEL = "hy3"
_MODEL_FAMILIES = (("claude", "anthropic"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
                   ("gemini", "google"), ("deepseek", "deepseek"), ("qwen", "alibaba"), ("glm", "zhipu"),
                   ("kimi", "moonshot"), ("moonshot", "moonshot"), ("grok", "xai"), ("mistral", "mistral"),
                   ("llama", "meta"), ("minimax", "minimax"), ("doubao", "bytedance"), ("hunyuan", "tencent"))
ROLE_NAMES = ("planner", "screener", "reader", "miner", "writer", "chair")
# 检索数量的上下限：太少做不成调研，太多费用和时间会翻好几倍
COUNT_LIMITS = {
    "per_query": (1, 100, "每条检索式、每个来源取的篇数"),
    "max_candidates": (5, 300, "候选篇数"),
    "download": (1, 50, "精读篇数"),
}


class ConfigError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    type: str
    model: str
    family: str
    api_key_env: str | None = None
    base_url: str | None = None
    max_tokens: int = 32000
    # anthropic：思考深度（low / medium / high / xhigh / max）和拒答时的服务端兜底
    effort: str | None = None
    fallbacks: str = "default"
    # openai_compatible：是否要求 JSON 输出、用哪个参数名限制输出长度、额外请求字段
    json_mode: bool = True
    max_tokens_param: str = "max_tokens"
    extra_body: dict = field(default_factory=dict)
    concurrency: int = 4
    # claude_code / codex / gemini_cli / codebuddy：命令路径（不填就用默认命令名）和每次调用最多等多少秒
    command: str | None = None
    timeout: int = 900

    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None

    def executable(self) -> str:
        return self.command or AGENT_CLIS[self.type][0]

    def describe(self, lang: str = "zh") -> str:
        where = f" @ {self.base_url}" if self.base_url else ""
        model = self.model or ("default model" if lang == "en" else "默认模型")
        if lang == "en":
            return f"{self.name} ({self.type}, {model}{where})"
        return f"{self.name}（{self.type}，{model}{where}）"


@dataclass
class Roles:
    planner: str
    screener: str
    reader: str
    miner: str
    writer: str
    chair: str
    reviewers: list[str]


@dataclass
class SearchConfig:
    sources: list[str] = field(default_factory=lambda: ["semantic_scholar", "arxiv", "openalex"])
    per_query: int = 20
    max_candidates: int = 80
    download: int = 15
    year_from: int | None = None
    s2_api_key_env: str = "S2_API_KEY"
    openalex_mailto: str | None = None


@dataclass
class ReviewConfig:
    ideas: int = 4
    lang: str = "zh"                 # 提示词和报告用哪个版本：zh / en
    language: str | None = None      # 模型用什么语言写，不填就跟 lang 一致
    min_fulltexts: int = 3           # 读到的全文少于这个数，或者全是低质量，就不往下找空白、写 proposal
    max_fulltext_chars: int = 60000
    reviewer_fulltext_papers: int = 3
    reviewer_fulltext_chars: int = 15000
    screen_batch: int = 20


@dataclass
class Config:
    path: Path
    providers: dict[str, ProviderConfig]
    roles: Roles
    search: SearchConfig
    review: ReviewConfig

    def provider(self, role: str) -> ProviderConfig:
        return self.providers[getattr(self.roles, role)]

    def reviewer_providers(self) -> list[ProviderConfig]:
        return [self.providers[n] for n in self.roles.reviewers]

    def warnings(self, lang: str = "zh") -> list[str]:
        en = lang == "en"
        out = []
        writer = self.provider("writer")
        same = [p.name for p in self.reviewer_providers() if p.family == writer.family]
        if same:
            out.append(
                f"The proposal writer {writer.name} and reviewer(s) {', '.join(same)} come from the same company "
                f"(family = {writer.family}). Models rate their own writing too kindly; use reviewers from other companies."
                if en else
                f"写 proposal 的 {writer.name} 和评审里的 {'、'.join(same)} 是同一家（family = {writer.family}）。"
                "模型给自己写的东西打分会偏高，建议评审换成别家的模型。"
            )
        if len(self.roles.reviewers) < 2:
            out.append("Only one reviewer model is configured. Add more if you can: issues raised by several reviewers are more trustworthy."
                       if en else "只配了一个评审模型。有条件的话多配几家，几家都提到的问题才更可信。")
        for p in self.providers.values():
            if p.type in AGENT_CLIS:
                if not shutil.which(p.executable()):
                    out.append(f"Command {p.executable()} for {p.name} was not found; install it and log in first."
                               if en else f"{p.name} 要用的命令 {p.executable()} 没找到，先安装并登录。")
                continue
            if p.type != "mock" and p.api_key_env and not p.api_key():
                out.append(f"Environment variable {p.api_key_env} for {p.name} is not set; steps that use it will fail."
                           if en else f"{p.name} 的环境变量 {p.api_key_env} 没有设置，用到它的环节会失败。")
        return out


def model_family(model: str) -> str:
    """按模型名猜是哪家公司的模型，认不出来就是 other。"""
    m = model.lower()
    for key, family in _MODEL_FAMILIES:
        if key in m:
            return family
    if re.match(r"(.*/)?hy\d", m):   # 混元：hy3、hy4-preview
        return "tencent"
    return "other"


def _provider(name: str, raw: dict) -> ProviderConfig:
    ptype = raw.get("type")
    if ptype not in PROVIDER_TYPES:
        raise ConfigError(f"providers.{name}.type 必须是 {PROVIDER_TYPES} 之一，现在是 {ptype!r}")
    if not raw.get("model") and ptype not in AGENT_CLIS:
        raise ConfigError(f"providers.{name} 缺少 model")
    if ptype == "openai_compatible" and not raw.get("base_url"):
        raise ConfigError(f"providers.{name} 是 openai_compatible，需要填 base_url")
    known = set(ProviderConfig.__dataclass_fields__) - {"name"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"providers.{name} 里有不认识的字段：{sorted(unknown)}")
    data = dict(raw)
    if ptype in AGENT_CLIS:
        # 不填 model 就用命令行工具自己的默认模型；本机同时开太多个 agent 容易卡，也容易撞上订阅的频率限制
        data.setdefault("model", CODEBUDDY_DEFAULT_MODEL if ptype == "codebuddy" else "")
        data.setdefault("family", model_family(data["model"]) if ptype == "codebuddy" else AGENT_CLIS[ptype][1])
        data.setdefault("concurrency", 2)
    data.setdefault("family", name)
    for key in ("concurrency", "timeout"):
        value = data.get(key, 1)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"providers.{name}.{key} 要填正整数，现在是 {value!r}")
    return ProviderConfig(name=name, **data)


def check_counts(search: SearchConfig, labels: dict[str, str] | None = None) -> None:
    """检查检索数量是不是整数、在不在范围内。labels 写明每个数是从哪来的，出错时告诉用户去哪改。"""
    labels = {key: f"search.{key}" for key in COUNT_LIMITS} | (labels or {})
    for key, (low, high, what) in COUNT_LIMITS.items():
        value = getattr(search, key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{labels[key]} 要填整数，现在是 {value!r}")
        if not low <= value <= high:
            raise ConfigError(f"{labels[key]} = {value} 超出范围：{what}要在 {low} 到 {high} 之间")
    if search.download > search.max_candidates:
        raise ConfigError(f"精读篇数（{labels['download']} = {search.download}）不能多于"
                          f"候选篇数（{labels['max_candidates']} = {search.max_candidates}）")


def _section(cls, name: str, raw: dict):
    unknown = set(raw) - {f.name for f in fields(cls)}
    if unknown:
        raise ConfigError(f"[{name}] 里有不认识的字段：{sorted(unknown)}")
    return cls(**raw)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"找不到配置文件 {path}。先运行 `idea-review init` 生成一份。")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    providers = {name: _provider(name, p) for name, p in raw.get("providers", {}).items()}
    if not providers:
        raise ConfigError("配置里没有 [providers.xxx]")

    roles_raw = raw.get("roles", {})
    missing = [r for r in (*ROLE_NAMES, "reviewers") if r not in roles_raw]
    if missing:
        raise ConfigError(f"[roles] 缺少：{missing}")
    reviewers = roles_raw["reviewers"]
    if isinstance(reviewers, str):
        reviewers = [reviewers]
    for role in ROLE_NAMES:
        if roles_raw[role] not in providers:
            raise ConfigError(f"roles.{role} = {roles_raw[role]!r}，但 [providers] 里没有这个名字")
    for r in reviewers:
        if r not in providers:
            raise ConfigError(f"roles.reviewers 里的 {r!r} 在 [providers] 里没有")
    if not reviewers:
        raise ConfigError("roles.reviewers 至少要有一个模型")
    roles = Roles(reviewers=list(reviewers), **{r: roles_raw[r] for r in ROLE_NAMES})

    search = _section(SearchConfig, "search", raw.get("search", {}))
    check_counts(search)
    review = _section(ReviewConfig, "review", raw.get("review", {}))
    if review.lang not in LANGS:
        raise ConfigError(f"review.lang 要填 {' 或 '.join(LANGS)}，现在是 {review.lang!r}")
    if isinstance(review.min_fulltexts, bool) or not isinstance(review.min_fulltexts, int) or not 1 <= review.min_fulltexts <= 50:
        raise ConfigError(f"review.min_fulltexts 要填 1 到 50 之间的整数，现在是 {review.min_fulltexts!r}")
    review.language = review.language or ("English" if review.lang == "en" else "中文")
    return Config(path=path, providers=providers, roles=roles, search=search, review=review)
