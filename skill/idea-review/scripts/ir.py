#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf>=4"]
# ///
"""idea-review skill 的辅助脚本。读论文、找空白、写 proposal 的是 agent 自己，这个脚本只管：
检索文献、下载全文、检查进度和文件格式、请别家公司的模型做元评审。

  ir.py init RUN --idea "..." --writer anthropic   建运行目录
  ir.py search RUN                                 按 01_plan.json 里的检索式检索
  ir.py fetch RUN                                  按初筛结果下载全文、抽文字
  ir.py next RUN [--force]                         检查进度和格式，告诉你下一步做什么
  ir.py reviewers [--writer anthropic]             看本机能用哪些评审模型
  ir.py review RUN [--reviewer codex ...]          请别家模型做元评审

出报告用同目录下的 report.py。只用标准库；抽 PDF 文字用 pypdf（用 uv run 运行会自动装），
没装的话退回 pdftotext。
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from string import Template
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SKILL_DIR = Path(__file__).resolve().parent.parent
USER_AGENT = "idea-review-skill/0.1 (literature review helper)"
DEFAULTS = {"per_query": 20, "max_candidates": 40, "download": 8, "ideas": 3, "min_fulltexts": 3}
# 全文不够数时，候选最多自动扩到设定数量的几倍，总数不超过 300
EXPAND_FACTOR, MAX_POOL = 3, 300
# 空白个数的上下限：按读到的全文篇数定，读得少就少写
MIN_GAPS, MAX_GAPS = 3, 8
# 发给评审模型的全文节选：最多几篇、每篇多少字符
REVIEW_EXCERPT_PAPERS, REVIEW_EXCERPT_CHARS = 3, 15000
# 超过这个字符数的全文，提醒 agent 分段读（命令行版精读时也是截到这么长）
LONG_TEXT = 60000
MAX_PDF_BYTES = 40 * 1024 * 1024
NO_LINK = "没有开放获取的 PDF 链接"
LANGUAGES = {"zh": "中文", "en": "English"}
FAMILIES = ("anthropic", "openai", "google", "other")

S2_FIELDS = "title,abstract,year,venue,citationCount,externalIds,openAccessPdf,authors,url,publicationTypes,journal"
_STOP = {"a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with", "by", "via", "using", "from", "is", "are"}
_REFS = re.compile(r"\n\s*(?:\d+\.?\s*)?(References|REFERENCES|Bibliography|BIBLIOGRAPHY|参考文献)\s*\n")

# 评审用的命令行工具。调用时去掉 API key 环境变量，让它们走订阅
REVIEWERS = {
    "claude": {"family": "anthropic", "exe": "claude", "keys": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")},
    "codex": {"family": "openai", "exe": "codex", "keys": ("OPENAI_API_KEY", "CODEX_API_KEY")},
    "gemini": {"family": "google", "exe": "gemini", "keys": ("GEMINI_API_KEY", "GOOGLE_API_KEY")},
}
API_ENV = ("IDEA_REVIEW_API_BASE", "IDEA_REVIEW_API_KEY", "IDEA_REVIEW_API_MODEL")


class Stop(Exception):
    """出错或者需要先做别的事。消息直接打印给 agent 看。"""


# ---------- 运行目录 ----------

class Run:
    def __init__(self, path: str):
        self.dir = Path(path).expanduser().resolve()

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def load(self, name: str, default=None):
        if not self.has(name):
            return default
        try:
            return json.loads(self.path(name).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise Stop(f"{self.path(name)} 不是合法的 JSON（第 {e.lineno} 行第 {e.colno} 列：{e.msg}），改好再运行。") from None

    def save(self, name: str, data) -> None:
        self.path(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def meta(self) -> dict:
        meta = self.load("meta")
        if meta is None:
            raise Stop(f"{self.dir} 还没初始化，先运行：ir.py init {self.dir} --idea \"...\" --writer <你是哪家的模型>")
        return meta

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.dir))
        except ValueError:
            return str(path)

    def file(self, stored: str) -> Path:
        path = Path(stored)
        return path if path.is_absolute() else self.dir / path


# ---------- 拼给模型看的文字（和命令行版 texts.py 一致） ----------

TX = {
    "zh": {
        "published": {True: "已发表", False: "预印本", None: "发表情况不明"},
        "year_unknown": "年份不明", "venue_unknown": "出处不明", "cited": "被引 {n}", "bits_sep": "，",
        "paper_line": "[{id}] {title}（{bits}）", "names_sep": "、", "join": "；", "none": "无",
        "screen_item": "{line}\n摘要：{abstract}", "no_abstract": "无摘要",
        "paper_types": {"empirical": "方法或实证研究", "survey": "综述", "position": "立场或观点文章",
                        "resource": "数据集、基准或工具", "theory": "理论分析", "other": "其他"},
        "review_digest": ("{line} 类型：{kind}；质量档位：{tier}\n  实际做了什么：{does}\n  方法：{method}\n  实验设置：{setup}\n"
                          "  主要结果：{results}\n  局限：{limits}\n  未解决的问题：{open}\n  证据强度：{strength}；危险信号：{flags}"),
        "overclaims": "  摘要夸大或和正文矛盾：{items}", "scope_gaps": "  摘要没交代的适用范围：{items}",
        "no_fulltext_notes": "（没有读到全文）",
        "closest_item": "  - {id}：重合 {overlap}；区别 {difference}",
        "proposal_text": ("编号：{pid}\n题目：{title}\n一句话：{one_liner}\n要解决的问题：{problem}\n为什么现有方法解决不了：{why}\n"
                          "最接近的工作：\n{closest}\n方法：{method}\n最小实验：{setup}；数据：{data}；基线：{baselines}；指标：{metrics}；"
                          "用时：{duration}\n出现什么结果说明不成立：{falsification}\n风险：{risks}\n需要补查：{checking}"),
        "excerpt": "{line}\n<<<全文节选开始\n{text}\n全文节选结束>>>",
        "no_excerpts": "（proposal 引用的论文没有可用的全文）",
        "omitted": "\n\n[……中间省略……]\n\n",
        "json_instruction": "只输出一个 JSON 对象，不要输出其他文字。JSON 必须符合下面的 JSON Schema（{name}）：",
        "retry": "上一次的输出没有通过格式校验：{error}\n请重新输出完整的 JSON，只输出 JSON。",
    },
    "en": {
        "published": {True: "peer-reviewed", False: "preprint", None: "publication status unknown"},
        "year_unknown": "year unknown", "venue_unknown": "venue unknown", "cited": "cited {n} times", "bits_sep": ", ",
        "paper_line": "[{id}] {title} ({bits})", "names_sep": ", ", "join": "; ", "none": "none",
        "screen_item": "{line}\nAbstract: {abstract}", "no_abstract": "no abstract",
        "paper_types": {"empirical": "method or empirical study", "survey": "survey", "position": "position or opinion paper",
                        "resource": "dataset, benchmark or tool", "theory": "theoretical analysis", "other": "other"},
        "review_digest": ("{line} Type: {kind}; quality tier: {tier}\n  What it actually does: {does}\n  Method: {method}\n"
                          "  Setup: {setup}\n  Key results: {results}\n  Limitations: {limits}\n  Open questions: {open}\n"
                          "  Evidence strength: {strength}; red flags: {flags}"),
        "overclaims": "  Abstract overclaims or contradicts the body: {items}", "scope_gaps": "  Scope the abstract leaves out: {items}",
        "no_fulltext_notes": "(no full texts were read)",
        "closest_item": "  - {id}: overlap {overlap}; difference {difference}",
        "proposal_text": ("ID: {pid}\nTitle: {title}\nOne-liner: {one_liner}\nProblem: {problem}\nWhy existing methods fall short: {why}\n"
                          "Closest work:\n{closest}\nMethod: {method}\nMinimal experiment: {setup}; data: {data}; baselines: {baselines}; "
                          "metrics: {metrics}; duration: {duration}\nResult that would show the idea does not hold: {falsification}\n"
                          "Risks: {risks}\nNeeds checking: {checking}"),
        "excerpt": "{line}\n<<<FULL-TEXT EXCERPT START\n{text}\nFULL-TEXT EXCERPT END>>>",
        "no_excerpts": "(none of the papers cited by the proposal has a usable full text)",
        "omitted": "\n\n[... middle omitted ...]\n\n",
        "json_instruction": "Output a single JSON object and nothing else. The JSON must follow this JSON Schema ({name}):",
        "retry": "The previous output failed validation: {error}\nOutput the complete JSON again, and only the JSON.",
    },
}


def paper_line(p: dict, t: dict) -> str:
    bits = [str(p.get("year") or t["year_unknown"]), p.get("venue") or t["venue_unknown"], t["published"][p.get("peer_reviewed")]]
    if p.get("citation_count") is not None:
        bits.append(t["cited"].format(n=p["citation_count"]))
    return t["paper_line"].format(id=p["paper_id"], title=p["title"], bits=t["bits_sep"].join(bits))


def review_digest(p: dict, r: dict, t: dict) -> str:
    q, join, none = r["quality"], t["join"].join, t["none"]
    text = t["review_digest"].format(
        line=paper_line(p, t), kind=t["paper_types"].get(q.get("paper_type", "other"), q.get("paper_type")), tier=q["tier"],
        does=r["what_it_actually_does"], method=r["method"], setup=r["setup"], results=join(r["key_results"]),
        limits=join(r["limitations"]) or none, open=join(r["open_questions"]) or none,
        strength=q["evidence_strength"], flags=join(q["red_flags"]) or none,
    )
    for key in ("overclaims", "scope_gaps"):
        items = r["abstract_vs_body"].get(key) or []
        if items:
            text += "\n" + t[key].format(items=join(items))
    return text


def proposal_text(pid: str, prop: dict, t: dict) -> str:
    exp, join = prop["minimal_experiment"], t["join"].join
    closest = "\n".join(t["closest_item"].format(id=c["paper_id"], overlap=c["overlap"], difference=c["difference"])
                        for c in prop["closest_work"]) or f"  {t['none']}"
    return t["proposal_text"].format(
        pid=pid, title=prop["title"], one_liner=prop["one_liner"], problem=prop["problem"], why=prop["why_existing_fail"],
        closest=closest, method=prop["method"], setup=exp["setup"], data=exp["data"],
        baselines=t["names_sep"].join(exp["baselines"]), metrics=t["names_sep"].join(exp["metrics"]), duration=exp["duration"],
        falsification=prop["falsification"], risks=join(prop["risks"]), checking=join(prop["needs_checking"]) or t["none"],
    )


def clip(text: str, max_chars: int, t: dict) -> str:
    """太长的全文保留开头 80% 和结尾 20%，中间标明省略。"""
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.8)
    return text[:head] + t["omitted"] + text[-(max_chars - head):]


# ---------- 格式检查：按 assets/schemas.*.json 查 agent 写的文件 ----------

_SCHEMAS: dict = {}


def schemas(lang: str = "zh") -> dict:
    if lang not in _SCHEMAS:
        _SCHEMAS[lang] = json.loads((SKILL_DIR / "assets" / f"schemas.{lang}.json").read_text(encoding="utf-8"))
    return _SCHEMAS[lang]


def check(value, name: str) -> list[str]:
    """返回格式问题，最多 8 条；没问题返回空列表。多出来的字段不算错。"""
    root = schemas()[name]

    def walk(v, node, where, errors):
        if len(errors) >= 8:
            return
        if "$ref" in node:
            node = root["$defs"][node["$ref"].split("/")[-1]]
        if "anyOf" in node:
            for option in node["anyOf"]:
                sub: list[str] = []
                walk(v, option, where, sub)
                if not sub:
                    return
            errors.append(f"{where}：格式不对")
            return
        if "enum" in node and v not in node["enum"]:
            errors.append(f"{where}：只能是 {' / '.join(map(str, node['enum']))}，现在是 {v!r}")
            return
        kind = node.get("type")
        ok = {"object": isinstance(v, dict), "array": isinstance(v, list), "string": isinstance(v, str),
              "boolean": isinstance(v, bool), "integer": isinstance(v, int) and not isinstance(v, bool),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool), "null": v is None}.get(kind, True)
        if not ok:
            errors.append(f"{where}：应该是 {kind}，现在是 {type(v).__name__}")
            return
        if kind == "object":
            for key in node.get("required", []):
                if key not in v:
                    errors.append(f"{where}：缺少字段 {key}")
            for key, sub in node.get("properties", {}).items():
                if key in v:
                    walk(v[key], sub, f"{where}.{key}", errors)
        elif kind == "array":
            for i, item in enumerate(v):
                walk(item, node.get("items", {}), f"{where}[{i}]", errors)

    errors: list[str] = []
    walk(value, root, name, errors)
    return errors[:8]


# ---------- 联网 ----------

class FetchError(Exception):
    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


def http_get(url: str, params: dict | None = None, headers: dict | None = None, tries: int = 4,
             timeout: int = 30, limit: int | None = None) -> bytes:
    if params:
        url += "?" + urlencode(params)
    delay = 2.0
    for attempt in range(tries):
        req = Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read(limit + 1) if limit else resp.read()
            if limit and len(data) > limit:
                raise FetchError("文件超过 40MB")
            return data
        except HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                try:
                    wait = float(e.headers.get("retry-after") or delay)
                except ValueError:
                    wait = delay
                time.sleep(min(wait, 60))
                delay *= 2
                continue
            raise FetchError(f"HTTP {e.code}", e.code) from None
        except (URLError, TimeoutError, OSError) as e:
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise FetchError(f"{type(e).__name__} {str(e)[:100]}") from None
    raise FetchError("unreachable")


# ---------- 检索（和命令行版 search.py 一致） ----------

def new_paper(**fields) -> dict:
    p = {"paper_id": "", "title": "", "authors": [], "year": None, "venue": None, "peer_reviewed": None,
         "citation_count": None, "abstract": None, "doi": None, "arxiv_id": None, "s2_id": None, "openalex_id": None,
         "url": None, "pdf_urls": [], "sources": [], "query_hits": 0, "local_path": None}
    p.update(fields)
    return p


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def norm_arxiv(aid: str | None) -> str | None:
    if not aid:
        return None
    return re.sub(r"v\d+$", "", aid.strip().split("/abs/")[-1])


def search_semantic_scholar(query: str, limit: int, year_from: int | None) -> list[dict]:
    params = {"query": query, "limit": min(limit, 100), "fields": S2_FIELDS}
    if year_from:
        params["year"] = f"{year_from}-"
    key = os.environ.get("S2_API_KEY")
    data = json.loads(http_get("https://api.semanticscholar.org/graph/v1/paper/search", params,
                               {"x-api-key": key} if key else None))
    papers = []
    for item in data.get("data") or []:
        if not item.get("title"):
            continue
        ext = item.get("externalIds") or {}
        venue = item.get("venue") or (item.get("journal") or {}).get("name") or None
        types = item.get("publicationTypes") or []
        arxiv_only = not venue or "arxiv" in venue.lower()
        peer = True if (("JournalArticle" in types or "Conference" in types) and not arxiv_only) else (
            False if arxiv_only and ext.get("ArXiv") else None)
        pdfs = []
        if (item.get("openAccessPdf") or {}).get("url"):
            pdfs.append(item["openAccessPdf"]["url"])
        if ext.get("ACL"):
            # ACL Anthology 的论文都公开，Semantic Scholar 常常没挂 PDF 链接
            pdfs.append(f"https://aclanthology.org/{ext['ACL']}.pdf")
        papers.append(new_paper(
            title=item["title"].strip(), authors=[a.get("name", "") for a in item.get("authors") or []][:12],
            year=item.get("year"), venue=venue, peer_reviewed=peer, citation_count=item.get("citationCount"),
            abstract=item.get("abstract"), doi=ext.get("DOI") or None, arxiv_id=norm_arxiv(ext.get("ArXiv")),
            s2_id=item.get("paperId"), url=item.get("url"), pdf_urls=pdfs, sources=["semantic_scholar"],
        ))
    return papers


def arxiv_entries(data: bytes) -> list[dict]:
    """解析 arXiv 返回的 Atom。用正则而不用 xml 库：有些机器上 Python 自带的 XML 解析库是坏的。"""
    text = data.decode("utf-8", "replace")

    def field(block: str, tag: str) -> str | None:
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, re.S)
        return " ".join(html.unescape(m.group(1)).split()) if m else None

    return [{"id": field(b, "id"), "title": field(b, "title") or "", "summary": field(b, "summary") or "",
             "published": field(b, "published") or "", "journal_ref": field(b, "arxiv:journal_ref"), "doi": field(b, "arxiv:doi"),
             "authors": [" ".join(html.unescape(n).split()) for n in re.findall(r"<name>(.*?)</name>", b, re.S)]}
            for b in re.findall(r"<entry\b[^>]*>(.*?)</entry>", text, re.S)]


def search_arxiv(query: str, limit: int) -> list[dict]:
    words = [w for w in re.findall(r"[A-Za-z0-9\-]+", query) if w.lower() not in _STOP][:5]
    if not words:
        return []
    params = {"search_query": " AND ".join(f"all:{w}" for w in words), "max_results": limit, "sortBy": "relevance"}
    papers = []
    for e in arxiv_entries(http_get("https://export.arxiv.org/api/query", params)):
        if not e["title"]:
            continue
        aid = norm_arxiv(e["id"])
        papers.append(new_paper(
            title=e["title"], authors=e["authors"][:12], year=int(e["published"][:4]) if e["published"][:4].isdigit() else None,
            venue=e["journal_ref"] or "arXiv", peer_reviewed=bool(e["journal_ref"]), abstract=e["summary"], doi=e["doi"],
            arxiv_id=aid, url=f"https://arxiv.org/abs/{aid}" if aid else None,
            pdf_urls=[f"https://arxiv.org/pdf/{aid}"] if aid else [], sources=["arxiv"],
        ))
    return papers


def lookup_arxiv_id(title: str) -> str | None:
    """按标题去 arXiv 找同一篇论文的预印本。标题去掉标点和大小写后完全一样才算，宁可漏掉也不拿错论文。"""
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    if len(words) < 3:
        return None
    time.sleep(3)  # arXiv 要求两次请求间隔 3 秒
    try:
        entries = arxiv_entries(http_get("https://export.arxiv.org/api/query",
                                         {"search_query": f'ti:"{" ".join(words)}"', "max_results": 5}, tries=2))
    except FetchError:
        return None
    want = norm_title(title)
    return next((norm_arxiv(e["id"]) for e in entries if norm_title(e["title"]) == want), None)


def search_openalex(query: str, limit: int, year_from: int | None) -> list[dict]:
    params = {"search": query, "per-page": min(limit, 50)}
    if year_from:
        params["filter"] = f"from_publication_date:{year_from}-01-01"
    data = json.loads(http_get("https://api.openalex.org/works", params))
    papers = []
    for w in data.get("results") or []:
        title = w.get("title") or w.get("display_name")
        if not title:
            continue
        source = (w.get("primary_location") or {}).get("source") or {}
        venue = source.get("display_name")
        is_repo = source.get("type") == "repository" or (venue or "").lower().startswith("arxiv")
        peer = False if (w.get("type") == "preprint" or is_repo) else (True if source.get("type") in ("journal", "conference") else None)
        arxiv_id, pdfs = None, []
        for loc in w.get("locations") or []:
            if "arxiv.org/abs/" in (loc.get("landing_page_url") or ""):
                arxiv_id = norm_arxiv(loc["landing_page_url"])
            if loc.get("pdf_url"):
                pdfs.append(loc["pdf_url"])
        best = (w.get("best_oa_location") or {}).get("pdf_url")
        if best and best not in pdfs:
            pdfs.insert(0, best)
        inverted = w.get("abstract_inverted_index")
        abstract = " ".join(word for _, word in sorted((pos, word) for word, poss in inverted.items() for pos in poss)) if inverted else None
        papers.append(new_paper(
            title=" ".join(title.split()), authors=[(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []][:12],
            year=w.get("publication_year"), venue=venue, peer_reviewed=peer, citation_count=w.get("cited_by_count"),
            abstract=abstract, doi=(w.get("doi") or "").replace("https://doi.org/", "") or None, arxiv_id=arxiv_id,
            openalex_id=w.get("id"), url=w.get("doi") or w.get("id"), pdf_urls=pdfs, sources=["openalex"],
        ))
    return papers


def merge(papers: list[dict]) -> list[dict]:
    """按 DOI、arXiv 编号、标题合并同一篇论文，字段互相补全。"""
    merged: list[dict] = []
    index: dict[str, int] = {}
    for p in papers:
        keys = [k for k in (
            f"doi:{p['doi'].lower()}" if p["doi"] else None,
            f"arxiv:{p['arxiv_id']}" if p["arxiv_id"] else None,
            f"title:{norm_title(p['title'])}" if len(norm_title(p["title"])) > 15 else None,
        ) if k]
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            merged.append(dict(p, query_hits=max(p["query_hits"], 1)))
            hit = len(merged) - 1
        else:
            old = merged[hit]
            for key, value in p.items():
                if key in ("sources", "pdf_urls"):
                    old[key] = list(dict.fromkeys(old[key] + value))
                elif key == "citation_count":
                    old[key] = max(filter(None, [old[key], value]), default=None)
                elif key == "peer_reviewed":
                    old[key] = True if True in (old[key], value) else (old[key] if old[key] is not None else value)
                elif key == "abstract":
                    if value and len(value) > len(old[key] or ""):
                        old[key] = value
                elif key == "venue":
                    # 同一篇论文既有 arXiv 版又有正式发表版时，出处写正式发表的那个
                    if value and p["peer_reviewed"] and (not old[key] or "arxiv" in old[key].lower()):
                        old[key] = value
                elif key == "query_hits":
                    continue
                elif old.get(key) in (None, "", []):
                    old[key] = value
            old["query_hits"] += 1
        for k in keys:
            index[k] = hit
    return merged


def rank(papers: list[dict]) -> list[dict]:
    """粗排：被越多检索式、越多来源命中的越靠前，引用数只做轻微加权。"""
    def score(p):
        return p["query_hits"] * 2 + len(p["sources"]) + math.log1p(p["citation_count"] or 0) * 0.5 + (1 if p["abstract"] else 0)
    return sorted(papers, key=score, reverse=True)


def search_all(queries: list[str], per_query: int, year_from: int | None) -> list[dict]:
    found: list[dict] = []
    disabled: set[str] = set()
    for q in queries:
        for src in ("semantic_scholar", "arxiv", "openalex"):
            if src in disabled:
                continue
            try:
                if src == "semantic_scholar":
                    got = search_semantic_scholar(q, per_query, year_from)
                    time.sleep(0.2 if os.environ.get("S2_API_KEY") else 1.2)
                elif src == "arxiv":
                    got = search_arxiv(q, per_query)
                    time.sleep(3)  # arXiv 要求两次请求间隔 3 秒
                else:
                    got = search_openalex(q, per_query, year_from)
            except FetchError as e:
                print(f"  {src} 检索失败（{q}）：{e}", flush=True)
                if e.code == 429:
                    disabled.add(src)
                    hint = "，设置环境变量 S2_API_KEY 可以避免" if src == "semantic_scholar" else ""
                    print(f"  {src} 限流了，这次后面的检索跳过它{hint}", flush=True)
                continue
            except ValueError as e:
                print(f"  {src} 检索失败（{q}）：{type(e).__name__}", flush=True)
                continue
            print(f"  {src}：{q} → {len(got)} 篇", flush=True)
            found.extend(got)
    return rank(merge(found))


# ---------- 下载全文、抽文字（和命令行版 pdfs.py 一致） ----------

def candidate_urls(p: dict) -> list[str]:
    urls = list(p.get("pdf_urls") or [])
    if p.get("arxiv_id"):
        urls.append(f"https://arxiv.org/pdf/{p['arxiv_id']}")
    return list(dict.fromkeys(urls))


def save_pdf(url: str, dest: Path) -> str | None:
    """下载一个链接，确实是 PDF 才存下来。返回失败原因，成功返回 None。"""
    try:
        data = http_get(url, headers={"Accept": "application/pdf,*/*;q=0.8"}, tries=2, timeout=60, limit=MAX_PDF_BYTES)
    except FetchError as e:
        return f"{e} {url}"
    if not data.startswith(b"%PDF"):
        return f"不是 PDF（可能是登录页或网页）{url}"
    dest.write_bytes(data)
    return None


def download(p: dict, dest: Path) -> str | None:
    reasons = []
    for url in candidate_urls(p):
        reason = save_pdf(url, dest)
        if reason is None:
            return None
        reasons.append(reason)
        if "arxiv.org" in url:
            time.sleep(1)
    if not p.get("arxiv_id"):
        arxiv_id = lookup_arxiv_id(p["title"])
        if arxiv_id:
            reason = save_pdf(f"https://arxiv.org/pdf/{arxiv_id}", dest)
            if reason is None:
                return None
            reasons.append(reason)
    return "；".join(reasons) or NO_LINK


def pdf_pages(pdf: Path) -> list[str]:
    try:
        import logging
        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)
    except ImportError:
        PdfReader = None
    if PdfReader:
        pages = []
        for page in PdfReader(str(pdf)).pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # pypdf 遇到坏页会抛各种异常，跳过这一页
                pages.append("")
        return pages
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.decode("utf-8", "replace")[-200:])
        return out.stdout.decode("utf-8", "replace").split("\f")[:-1] or [""]
    raise Stop("抽 PDF 文字需要 pypdf：用 `uv run ir.py ...` 运行会自动装；或者 `python3 -m pip install pypdf`；"
               "或者装 poppler（里面有 pdftotext）。")


def extract_text(pdf: Path) -> tuple[str, int]:
    pages = pdf_pages(pdf)
    full = "\n\n".join(f"[p.{i}]\n{text.strip()}" for i, text in enumerate(pages, start=1))
    # 只在后半部分找"参考文献"标题，避免误切正文里的同名小节
    cut = None
    for m in _REFS.finditer(full):
        if m.start() > len(full) * 0.5:
            cut = m.start()
    if cut:
        full = full[:cut] + "\n\n[参考文献部分已省略]"
    return full, len(pages)


def pdf_title(pdf: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf))
        if reader.metadata and reader.metadata.title and len(reader.metadata.title.strip()) > 8:
            return reader.metadata.title.strip()
        lines = [line.strip() for line in (reader.pages[0].extract_text() or "").splitlines() if len(line.strip()) > 8]
        if lines:
            return lines[0][:200]
    except Exception:
        pass
    return pdf.stem


def read_pdf(run: Run, pid: str, pdf: Path) -> dict:
    """抽一篇 PDF 的文字，存进 text/。返回这篇的全文记录。"""
    record = {"paper_id": pid, "status": "failed", "pdf_path": run.rel(pdf), "text_path": None, "chars": 0, "pages": 0,
              "note": "", "manual": False, "checked_at": time.time()}
    try:
        text, pages = extract_text(pdf)
    except Stop:
        raise
    except Exception as e:
        return dict(record, note=f"PDF 解析失败：{str(e)[:150]}")
    if len(text) < 2000:
        return dict(record, pages=pages, note="抽出来的文字太少，可能是扫描版 PDF")
    (run.dir / "text").mkdir(exist_ok=True)
    text_path = run.dir / "text" / f"{pid}.txt"
    text_path.write_text(text, encoding="utf-8")
    return dict(record, status="ok", text_path=run.rel(text_path), chars=len(text), pages=pages)


# ---------- 命令 ----------

def cmd_init(args) -> None:
    run = Run(args.run)
    meta = run.load("meta")
    counts = {"per_query": args.per_query, "max_candidates": args.candidates, "download": args.refs}
    review = {"ideas": args.ideas, "min_fulltexts": args.min_fulltexts}
    if meta:
        # 已经建过：只改命令行明确给了的数量
        changed = []
        for group, values in (("search", counts), ("review", review)):
            for key, value in values.items():
                if value is not None and meta[group].get(key) != value:
                    changed.append(f"{key} {meta[group].get(key)} → {value}")
                    meta[group][key] = value
        if args.writer:
            meta["writer"] = {"family": args.writer, "model": args.writer_model or meta["writer"].get("model", "")}
        run.save("meta", meta)
        print(f"{run.dir} 已经建过了。" + (f"改了：{'，'.join(changed)}。" if changed else "没有改动。") + "运行 next 看下一步。")
        return
    if not args.idea:
        raise Stop("第一次建目录要用 --idea 给出研究 idea")
    if not args.writer:
        raise Stop("要用 --writer 说明你自己是哪家的模型（anthropic / openai / google / other），元评审要换别家的模型")
    run.dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "idea": args.idea.strip(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "lang": args.lang,
        "language": LANGUAGES[args.lang],
        "search": {k: v if v is not None else DEFAULTS[k] for k, v in counts.items()},
        "review": {k: v if v is not None else DEFAULTS[k] for k, v in review.items()},
        "year_from": args.year_from,
        "writer": {"family": args.writer, "model": args.writer_model or ""},
        "expanded": 0,
        "force": False,
        "tool": "skill",
    }
    run.save("meta", meta)
    s, r = meta["search"], meta["review"]
    print(f"建好了：{run.dir}\n数量：候选最多 {s['max_candidates']} 篇，精读 {s['download']} 篇，至少 {r['min_fulltexts']} 篇全文"
          f"才往下找空白，proposal 最多 {r['ideas']} 份。\n下一步：拆检索词，写 {run.path('01_plan')}（见 references/steps.md 第 1 节）。")


def write_screen_todo(run: Run, papers: list[dict], screened: set[str], lang: str) -> list[dict]:
    """把还没初筛的候选写进 screen_todo.md，给 agent 读。"""
    t = TX[lang]
    todo = [p for p in papers if not p["local_path"] and p["paper_id"] not in screened]
    path = run.dir / "screen_todo.md"
    if todo:
        body = "\n\n".join(t["screen_item"].format(line=paper_line(p, t), abstract=(p["abstract"] or t["no_abstract"])[:1500])
                           for p in todo)
        path.write_text(f"# 待初筛的候选（{len(todo)} 篇）\n\n{body}\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
    return todo


def cmd_search(args) -> None:
    run = Run(args.run)
    meta = run.meta()
    if run.has("02_candidates") and not args.again:
        raise Stop("已经检索过了。要换检索式重来，加 --again（会覆盖候选和之后的初筛、全文记录），或者开一个新的运行目录。")
    plan = run.load("01_plan")
    queries = args.query or (plan or {}).get("search_queries")
    if not queries:
        raise Stop(f"先写 {run.path('01_plan')}（要有 search_queries），或者用 --query 直接给检索式。")
    if args.again:
        for name in ("03_screen", "04_fulltext"):
            run.path(name).unlink(missing_ok=True)
        meta["expanded"] = 0
    s = meta["search"]
    print(f"检索 {len(queries)} 条检索式，每条每个来源取 {s['per_query']} 篇……", flush=True)
    found = search_all(queries, s["per_query"], meta.get("year_from"))
    run.save("02_search_results", found)
    candidates = [dict(p) for p in found[: s["max_candidates"]]]
    for i, p in enumerate(candidates, start=1):
        p["paper_id"] = f"P{i:02d}"
    local = []
    if args.papers_dir:
        for i, f in enumerate(sorted(Path(args.papers_dir).expanduser().glob("*.pdf")), start=1):
            local.append(new_paper(paper_id=f"L{i:02d}", title=pdf_title(f), sources=["local"], local_path=str(f.resolve())))
    papers = local + candidates
    if not papers:
        raise Stop("一篇论文都没检索到。换个说法改写检索式（01_plan.json），再加 --again 重新检索。")
    run.save("02_candidates", papers)
    meta["papers_dir"] = str(Path(args.papers_dir).expanduser().resolve()) if args.papers_dir else None
    run.save("meta", meta)
    write_screen_todo(run, papers, set(), meta["lang"])
    print(f"合并去重后 {len(found)} 篇，取前 {len(candidates)} 篇做候选" + (f"，另有本地 PDF {len(local)} 篇" if local else "") + "。")
    print(f"下一步：初筛。读 {run.dir / 'screen_todo.md'}，结果写进 {run.path('03_screen')}（见 references/steps.md 第 3 节）。")


def cmd_fetch(args) -> None:
    run = Run(args.run)
    meta = run.meta()
    papers = run.load("02_candidates")
    if papers is None:
        raise Stop("先运行 search。")
    screen = {s["paper_id"]: s for s in run.load("03_screen", [])}
    todo = write_screen_todo(run, papers, set(screen), meta["lang"])
    if todo:
        raise Stop(f"还有 {len(todo)} 篇候选没初筛（{todo[0]['paper_id']}–{todo[-1]['paper_id']}）。"
                   f"读 {run.dir / 'screen_todo.md'}，结果追加进 {run.path('03_screen')}，再运行 fetch。")
    result = run.load("04_fulltext", {})
    target = meta["search"]["download"]
    pdf_dir = run.dir / "papers"
    pdf_dir.mkdir(exist_ok=True)

    def got() -> int:
        return sum(1 for p in papers if not p["local_path"] and result.get(p["paper_id"], {}).get("status") == "ok")

    def keep(record: dict) -> None:
        result[record["paper_id"]] = record
        run.save("04_fulltext", result)   # 每篇存一次，中途断了不用重下

    # 用户按编号放进 papers/ 的 PDF：还没拿到全文的，或者换了新文件的
    for p in papers:
        pid, pdf = p["paper_id"], pdf_dir / f"{p['paper_id']}.pdf"
        old = result.get(pid)
        if p["local_path"] or not pdf.exists() or (old and old["status"] == "ok"):
            continue
        if old and old.get("pdf_path") and pdf.stat().st_mtime <= old.get("checked_at", 0):
            continue   # 下载过、但抽不出文字的 PDF，换了新文件才重试
        record = read_pdf(run, pid, pdf)
        record.update(manual=True, note=record["note"] or "手动放进 papers/ 的 PDF")
        keep(record)
        print(f"  {pid} 读到手动放进来的 PDF：" + (f"{record['pages']} 页" if record["status"] == "ok" else record["note"]), flush=True)
    # 用户提供的本地 PDF（L01…）
    for p in papers:
        if p["local_path"] and p["paper_id"] not in result:
            record = read_pdf(run, p["paper_id"], Path(p["local_path"]))
            keep(record)
            print(f"  {p['paper_id']} 本地 PDF：" + (f"{record['pages']} 页" if record["status"] == "ok" else record["note"]), flush=True)

    # 按初筛结果排队：先高后中，低相关的不下载；试过的不再排。没有 PDF 链接的也排：会按标题去 arXiv 找预印本
    queue = [p for level in ("high", "medium") for p in papers
             if not p["local_path"] and p["paper_id"] not in result and screen.get(p["paper_id"], {}).get("relevance") == level]
    if got() < target and queue:
        print(f"下载全文：已有 {got()} 篇，要 {target} 篇", flush=True)
    while got() < target and queue:
        p = queue.pop(0)
        pid = p["paper_id"]
        dest = pdf_dir / f"{pid}.pdf"
        reason = download(p, dest)
        if reason:
            keep({"paper_id": pid, "status": "no_pdf" if reason == NO_LINK else "failed", "pdf_path": None, "text_path": None,
                  "chars": 0, "pages": 0, "note": "没有开放获取的 PDF，建议手动下载" if reason == NO_LINK else reason[:300],
                  "manual": False})
            print(f"  {pid} 没拿到全文：{reason[:120]}", flush=True)
            continue
        record = read_pdf(run, pid, dest)
        keep(record)
        print(f"  {pid} " + (f"全文 {record['pages']} 页，{record['chars']} 字符" if record["status"] == "ok" else record["note"]), flush=True)
    run.save("04_fulltext", result)

    have = sum(1 for p in papers if not p["local_path"])
    if got() < target:
        # 候选里能下载的用完了：从存下的检索结果里再取一批候选，交给 agent 初筛
        pool = run.load("02_search_results", [])
        cap = min(meta["search"]["max_candidates"] * EXPAND_FACTOR, MAX_POOL)
        take = min(cap - have, max(10, 3 * (target - got())))
        extra = [dict(p) for p in pool[have:have + take]] if take > 0 else []
        if extra:
            for i, p in enumerate(extra, start=have + 1):
                p["paper_id"] = f"P{i:02d}"
            run.save("02_candidates", papers + extra)
            meta["expanded"] = meta.get("expanded", 0) + len(extra)
            run.save("meta", meta)
            write_screen_todo(run, papers + extra, set(screen), meta["lang"])
            print(f"全文还差 {target - got()} 篇，候选里能下载的已经用完。从检索结果里又取了 {len(extra)} 篇候选"
                  f"（{extra[0]['paper_id']}–{extra[-1]['paper_id']}）。\n"
                  f"下一步：初筛这些新候选（读 {run.dir / 'screen_todo.md'}，结果追加进 03_screen.json），再运行 fetch。")
            return
        why = "检索结果都试过了" if have >= len(pool) else f"候选已经扩到 {have} 篇，到了上限"
        print(f"只拿到 {got()} 篇全文，没到设定的 {target} 篇：{why}。")
    else:
        print(f"拿到全文 {got()} 篇。")
    print("下一步：运行 next，看要精读哪些论文。")


def gate(reviews: list[dict], meta: dict) -> dict | None:
    """读到的全文太少，或者全是低质量，就不往下找空白。返回停下的原因，证据够了返回 None。"""
    done = [r["review"] for r in reviews if r.get("review")]
    strong = sum(1 for r in done if r["quality"]["tier"] in ("high", "medium"))
    # 精读篇数本身设得比门槛小时，按精读篇数算，不然永远过不了
    need = min(meta.get("review", {}).get("min_fulltexts", DEFAULTS["min_fulltexts"]), meta["search"]["download"])
    if len(done) < need:
        return {"reason": "few", "read": len(done), "need": need, "strong": strong}
    if not strong:
        return {"reason": "all_low", "read": len(done), "need": need, "strong": 0}
    return None


def max_gaps(n_read: int) -> int:
    return min(MAX_GAPS, max(MIN_GAPS, n_read))


def available_reviewers(writer_family: str | None = None) -> list[tuple[str, str, bool]]:
    """本机能用的评审：(名字, 公司, 是不是和写 proposal 的同一家)。"""
    found = [(name, info["family"], info["family"] == writer_family) for name, info in REVIEWERS.items() if shutil.which(info["exe"])]
    if os.environ.get("IDEA_REVIEW_API_BASE") and os.environ.get("IDEA_REVIEW_API_KEY"):
        family = api_family(os.environ.get("IDEA_REVIEW_API_MODEL", ""))
        found.append(("api", family, family == writer_family))
    return found


def cmd_next(args) -> None:
    run = Run(args.run)
    meta = run.meta()
    if args.force and not meta.get("force"):
        meta["force"] = True
        run.save("meta", meta)
        print("记下了：文献证据不够也往下跑。后面的空白和 proposal 只能当线索，报告里会标明。")
    lang = meta["lang"]

    def step(text: str) -> None:
        print(f"下一步：{text}")

    plan = run.load("01_plan")
    if plan is None:
        return step(f"拆检索词，写 {run.path('01_plan')}（见 references/steps.md 第 1 节）。")
    if errs := check(plan, "QueryPlan"):
        return report_errors("01_plan.json", errs)
    papers = run.load("02_candidates")
    if papers is None:
        return step(f"运行 ir.py search {run.dir}")
    screen_list = run.load("03_screen", [])
    for i, item in enumerate(screen_list):
        if errs := check(item, "ScreenItem"):
            return report_errors(f"03_screen.json 第 {i + 1} 条", errs)
    screen = {s["paper_id"]: s for s in screen_list}
    todo = write_screen_todo(run, papers, set(screen), lang)
    if todo:
        return step(f"初筛 {len(todo)} 篇候选：读 {run.dir / 'screen_todo.md'}，结果写进（或追加进）{run.path('03_screen')}"
                    "（见 references/steps.md 第 3 节）。")

    fulltexts = run.load("04_fulltext")
    target = meta["search"]["download"]
    ok_ids = [p["paper_id"] for p in papers if (fulltexts or {}).get(p["paper_id"], {}).get("status") == "ok"]
    remote_ok = sum(1 for p in papers if not p["local_path"] and p["paper_id"] in ok_ids)
    tried = set(fulltexts or {})
    untried = [p for p in papers if not p["local_path"] and p["paper_id"] not in tried
               and screen.get(p["paper_id"], {}).get("relevance") in ("high", "medium")]
    manual = [p["paper_id"] for p in papers if (run.dir / "papers" / f"{p['paper_id']}.pdf").exists()
              and (fulltexts or {}).get(p["paper_id"], {}).get("status") not in ("ok",)
              and not (fulltexts or {}).get(p["paper_id"], {}).get("pdf_path")]
    if fulltexts is None or (remote_ok < target and untried) or manual:
        return step(f"运行 ir.py fetch {run.dir}" + (f"（papers/ 里有新放进来的 PDF：{'、'.join(manual)}）" if manual else ""))

    # 精读笔记：notes/P03.json，一篇一个文件
    notes_dir = run.dir / "notes"
    by_id = {p["paper_id"]: p for p in papers}
    reviews, missing, bad = [], [], []
    for pid in ok_ids:
        path = notes_dir / f"{pid}.json"
        if not path.exists():
            missing.append(pid)
            continue
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            bad.append((pid, [f"不是合法的 JSON（第 {e.lineno} 行：{e.msg}）"]))
            continue
        if errs := check(note, "PaperReview"):
            bad.append((pid, errs))
            continue
        reviews.append({"paper_id": pid, "review": note, "error": "", "truncated": False})
    run.save("05_reviews", reviews)
    if bad:
        for pid, errs in bad:
            report_errors(f"notes/{pid}.json", errs)
        return
    if missing:
        def size(f: dict) -> str:
            long = "，很长，见第 5 节怎么读" if f["chars"] > LONG_TEXT else ""
            return f"{f['pages']} 页，约 {f['chars'] / 10000:.1f} 万字符{long}"
        listing = "\n".join(f"  {pid}  {run.dir / fulltexts[pid]['text_path']}（{size(fulltexts[pid])}）  {by_id[pid]['title'][:80]}"
                            for pid in missing)
        return step(f"精读这 {len(missing)} 篇全文，每篇写一个 {notes_dir}/<编号>.json（见 references/steps.md 第 5 节；"
                    f"能开子代理的话一篇交给一个子代理）：\n{listing}")

    tiers = [r["review"]["quality"]["tier"] for r in reviews]
    print(f"已精读 {len(reviews)} 篇：高 {tiers.count('high')}、中 {tiers.count('medium')}、低 {tiers.count('low')}。")
    landscape = run.load("06_landscape")
    shortfall = None if landscape else gate(reviews, meta)
    if shortfall and not meta.get("force"):
        why = (f"只读到 {shortfall['read']} 篇全文，少于 {shortfall['need']} 篇" if shortfall["reason"] == "few"
               else f"读到的 {shortfall['read']} 篇全文都是低质量")
        print(f"文献证据不够，停在第 5 步：{why}。拿这么少或这么弱的文献找空白、写 proposal，结果多半站不住，后面几步不要做。")
        step(f"运行 report.py {run.dir} 出报告，然后告诉用户：\n"
             f"  - 报告里\"很相关，但没读到全文\"的论文可以自己下载 PDF，按编号放进 {run.dir / 'papers'}/（比如 P07.pdf），再让你接着跑；\n"
             "  - 检索结果本身太少的话，换几条检索式开一次新的运行；\n"
             f"  - 确实想用现有的文献跑完，运行 ir.py next {run.dir} --force。")
        return

    limit = max_gaps(len(reviews))
    if landscape is None:
        return step(f"画版图、找空白，写 {run.path('06_landscape')}。空白最多 {limit} 个（按读到的 {len(reviews)} 篇定）"
                    "（见 references/steps.md 第 6 节）。")
    if errs := check(landscape, "Landscape"):
        return report_errors("06_landscape.json", errs)
    if len(landscape["gaps"]) > limit:
        print(f"06_landscape.json 里有 {len(landscape['gaps'])} 个空白，超过上限 {limit} 个，只留前 {limit} 个。")
        landscape["gaps"] = landscape["gaps"][:limit]
        run.save("06_landscape", landscape)
    read_ids = {r["paper_id"] for r in reviews}
    cited = {e["paper_id"] for g in landscape["gaps"] for e in g["evidence"]}
    cited |= {c["paper_id"] for g in landscape["gaps"] for c in g["closest_read"]}
    if stray := sorted(cited - read_ids):
        print(f"注意：空白的证据或最近论文里引用了没精读过的论文 {'、'.join(stray)}。这两处只能写读过全文的论文，改一下 06_landscape.json。")

    n_seeds = min(max(meta["review"]["ideas"] - 1, 0), len(landscape["gaps"]))
    seeds = run.load("07_seeds")
    if seeds is None and n_seeds == 0:
        seeds = {"seeds": []}
        run.save("07_seeds", seeds)
    if seeds is None:
        return step(f"从空白里挖 {n_seeds} 个新方向，写 {run.path('07_seeds')}（见 references/steps.md 第 7 节）。")
    if errs := check(seeds, "IdeaSeeds"):
        return report_errors("07_seeds.json", errs)
    if len(seeds["seeds"]) > n_seeds:
        seeds["seeds"] = seeds["seeds"][:n_seeds]
        run.save("07_seeds", seeds)

    expected = ["I1"] + [f"I{i}" for i in range(2, len(seeds["seeds"]) + 2)]
    proposals = run.load("08_proposals")
    done_ids = []
    for i, rec in enumerate(proposals or []):
        if rec.get("proposal") is None:
            continue
        if errs := check(rec["proposal"], "Proposal"):
            return report_errors(f"08_proposals.json 里 {rec.get('proposal_id', i + 1)} 的 proposal", errs)
        done_ids.append(rec.get("proposal_id"))
    if missing_ids := [pid for pid in expected if pid not in done_ids]:
        return step(f"写 proposal，写进 {run.path('08_proposals')}。要有 {'、'.join(expected)}：I1 是用户的原始 idea，"
                    f"I2 起依次对应 07_seeds.json 里的新方向。还缺 {'、'.join(missing_ids)}（见 references/steps.md 第 8 节）。")
    cited = sorted({c["paper_id"] for rec in proposals if rec.get("proposal") for c in rec["proposal"]["closest_work"]} - set(by_id))
    if cited:
        print(f"注意：proposal 引用了候选里没有的编号 {'、'.join(cited)}。只能引用候选里的论文，别的文献写进 needs_checking。")

    metas = run.load("09_meta_reviews", [])
    reviewed = {m["proposal_id"] for m in metas if m.get("review")}
    if not reviewed >= set(expected):
        avail = [r for r in available_reviewers(meta["writer"]["family"]) if not r[2]]
        if avail:
            names = " ".join(f"--reviewer {r[0]}" for r in avail)
            return step(f"请别家的模型做元评审：ir.py review {run.dir} {names}\n"
                        f"  本机能用的别家评审：{'、'.join(f'{r[0]}（{r[1]}）' for r in avail)}。先告诉用户要调用哪几个，"
                        "这会用掉对应工具的订阅额度或 API 费用。每份可能要几分钟，放到后台跑。")
        return step("本机没找到别家公司的命令行工具（claude / codex / gemini），也没配 IDEA_REVIEW_API_*。"
                    "问用户要不要装一个并登录；都不行的话，按 references/steps.md 第 9 节的\"没有别家模型时\"用子代理代评，报告里会标明是同一家的模型。")
    failed = [m for m in metas if not m.get("review")]
    if failed:
        print(f"有 {len(failed)} 份评审失败了（{'、'.join(sorted({m['reviewer'] for m in failed}))}）。可以再运行一次 review 重试，不重试也能出报告。")

    chairs = {c["proposal_id"]: c for c in run.load("10_chair", [])}
    need_chair = [pid for pid in expected
                  if sum(1 for m in metas if m["proposal_id"] == pid and m.get("review")) >= 2
                  and not (chairs.get(pid) or {}).get("summary")]
    for pid, c in chairs.items():
        if c.get("summary") and (errs := check(c["summary"], "ChairSummary")):
            return report_errors(f"10_chair.json 里 {pid} 的 summary", errs)
    if need_chair:
        return step(f"汇总 {'、'.join(need_chair)} 的评审意见，写进 {run.path('10_chair')}（见 references/steps.md 第 10 节）。")
    step(f"运行 report.py {run.dir} 出报告，把报告路径和要点告诉用户。")


def report_errors(what: str, errs: list[str]) -> None:
    print(f"{what} 的格式有问题，改好再运行 next（格式见 references/formats.md）：")
    for e in errs:
        print(f"  - {e}")


# ---------- 元评审 ----------

def api_family(model: str) -> str:
    m = model.lower()
    for key, family in (("claude", "anthropic"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
                        ("gemini", "google"), ("deepseek", "deepseek"), ("qwen", "alibaba"), ("glm", "zhipu"),
                        ("kimi", "moonshot"), ("moonshot", "moonshot"), ("grok", "xai"), ("mistral", "mistral"),
                        ("llama", "meta"), ("minimax", "minimax"), ("doubao", "bytedance")):
        if key in m:
            return family
    return os.environ.get("IDEA_REVIEW_API_FAMILY", "other")


def extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("输出里没有找到 JSON 对象")
    return json.loads(text[start:end + 1])


class Reviewer:
    def __init__(self, spec: str, timeout: int):
        name, _, model = spec.partition(":")
        if name not in REVIEWERS and name != "api":
            raise Stop(f"不认识的评审 {name}，可选 {' / '.join([*REVIEWERS, 'api'])}")
        self.name, self.timeout = name, timeout
        if name == "api":
            missing = [k for k in API_ENV[:2] if not os.environ.get(k)]
            if missing:
                raise Stop(f"用 api 评审要先设置环境变量 {'、'.join(missing)}（OpenAI 兼容接口的地址和 key）")
            self.model = model or os.environ.get("IDEA_REVIEW_API_MODEL", "")
            if not self.model:
                raise Stop("用 api 评审要写明模型：--reviewer api:模型名，或者设置 IDEA_REVIEW_API_MODEL")
            self.family = api_family(self.model)
        else:
            self.model = model
            self.family = REVIEWERS[name]["family"]
            if not shutil.which(REVIEWERS[name]["exe"]):
                raise Stop(f"本机没有 {REVIEWERS[name]['exe']} 命令，先安装并登录")
        self.lock = threading.Semaphore(2)
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.used_model = model

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name

    def add_usage(self, model: str | None, inp: int | None, out: int | None) -> None:
        self.used_model = model or self.used_model
        self.usage["calls"] += 1
        self.usage["input_tokens"] += inp or 0
        self.usage["output_tokens"] += out or 0

    def complete(self, system: str, user: str, schema: dict, lang: str) -> str:
        with self.lock, tempfile.TemporaryDirectory(prefix="idea-review-") as tmp:
            work = Path(tmp)
            if self.name == "api":
                return self._api(system, user, schema, lang)
            if self.name == "claude":
                args = ["claude", "-p", "--output-format", "json", "--system-prompt", system,
                        "--json-schema", json.dumps(schema, ensure_ascii=False),
                        "--tools", "", "--setting-sources", "", "--no-session-persistence"]
                if self.model:
                    args += ["--model", self.model]
                return self._parse_claude(*self._run(args, user, work))
            if self.name == "codex":
                schema_path, answer = work / "schema.json", work / "answer.txt"
                schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
                # 不读 ~/.codex/config.toml，免得每次调用都启动用户配置的 MCP 服务
                args = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                        "--sandbox", "read-only", "-C", str(work), "--output-schema", str(schema_path), "-o", str(answer), "--json"]
                if self.model:
                    args += ["-m", self.model]
                return self._parse_codex(*self._run(args + ["-"], f"{system}\n\n{user}", work), answer)
            prompt = (f"{system}\n\n{user}\n\n---\n{TX[lang]['json_instruction'].format(name='MetaReview')}\n"
                      f"{json.dumps(schema, ensure_ascii=False)}")
            args = ["gemini", "-p", "", "-o", "json", "--skip-trust", "--approval-mode", "plan"]
            if self.model:
                args += ["-m", self.model]
            return self._parse_gemini(*self._run(args, prompt, work))

    def _run(self, args: list[str], stdin: str, cwd: Path) -> tuple[str, str, int]:
        env = {k: v for k, v in os.environ.items() if k not in REVIEWERS[self.name]["keys"]}
        try:
            proc = subprocess.run(args, input=stdin.encode("utf-8"), capture_output=True, cwd=cwd, env=env, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"超过 {self.timeout} 秒没有返回，可以用 --timeout 调大") from None
        return proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"), proc.returncode

    @staticmethod
    def _failed(what: str, stderr: str, code: int) -> RuntimeError:
        return RuntimeError(f"{what}（退出码 {code}）：{stderr.strip()[-400:] or '没有错误信息'}")

    def _parse_claude(self, stdout: str, stderr: str, code: int) -> str:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            raise self._failed("没有输出预期的 JSON", stderr or stdout, code) from None
        usage = data.get("usage") or {}
        self.add_usage(next(iter(data.get("modelUsage") or {}), None),
                       sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                       usage.get("output_tokens"))
        if data.get("is_error"):
            raise RuntimeError(f"报错：{str(data.get('result') or data.get('subtype'))[:400]}")
        if data.get("structured_output") is not None:
            return json.dumps(data["structured_output"], ensure_ascii=False)
        return data.get("result") or ""

    def _parse_codex(self, stdout: str, stderr: str, code: int, answer: Path) -> str:
        usage, errors = {}, []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
            elif event.get("type") in ("error", "turn.failed"):
                errors.append(str(event.get("message") or (event.get("error") or {}).get("message") or event))
        self.add_usage(None, usage.get("input_tokens"), usage.get("output_tokens"))
        text = answer.read_text(encoding="utf-8").strip() if answer.exists() else ""
        if text:
            return text
        if errors:
            raise RuntimeError(f"报错：{errors[-1][:400]}")
        raise self._failed("没有给出回答", stderr, code)

    def _parse_gemini(self, stdout: str, stderr: str, code: int) -> str:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            raise self._failed("没有输出预期的 JSON", stderr or stdout, code) from None
        models = (data.get("stats") or {}).get("models") or {}
        tokens = [m.get("tokens") or {} for m in models.values()]
        self.add_usage(next(iter(models), None), sum(t.get("prompt") or 0 for t in tokens), sum(t.get("candidates") or 0 for t in tokens))
        if data.get("error"):
            raise RuntimeError(f"报错：{str(data['error'].get('message') or data['error'])[:400]}")
        return data.get("response") or ""

    def _api(self, system: str, user: str, schema: dict, lang: str) -> str:
        base = os.environ["IDEA_REVIEW_API_BASE"].rstrip("/")
        body = {"model": self.model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{user}\n\n---\n{TX[lang]['json_instruction'].format(name='MetaReview')}\n"
                                        f"{json.dumps(schema, ensure_ascii=False)}"},
        ]}
        req = Request(f"{base}/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {os.environ['IDEA_REVIEW_API_KEY']}", "User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:300]}") from None
        except (URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"{type(e).__name__} {str(e)[:200]}") from None
        usage = data.get("usage") or {}
        self.add_usage(data.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def review_material(run: Run, meta: dict) -> tuple[str, dict, dict]:
    """精读笔记摘要（高、中质量在前）和每篇论文的全文路径，评审时拼进提示词。"""
    t = TX[meta["lang"]]
    by_id = {p["paper_id"]: p for p in run.load("02_candidates", [])}
    order = {"high": 0, "medium": 1, "low": 2}
    reviews = sorted((r for r in run.load("05_reviews", []) if r.get("review") and r["paper_id"] in by_id),
                     key=lambda r: order[r["review"]["quality"]["tier"]])
    digests = "\n\n".join(review_digest(by_id[r["paper_id"]], r["review"], t) for r in reviews) or t["no_fulltext_notes"]
    texts = {pid: run.file(f["text_path"]) for pid, f in run.load("04_fulltext", {}).items() if f.get("status") == "ok" and f.get("text_path")}
    return digests, texts, by_id


def cmd_review(args) -> None:
    run = Run(args.run)
    meta = run.meta()
    lang, t = meta["lang"], TX[meta["lang"]]
    proposals = [r for r in run.load("08_proposals", []) if r.get("proposal")]
    if not proposals:
        raise Stop("还没有写好的 proposal（08_proposals.json）。先运行 next 看进度。")
    system_raw, user_raw = (SKILL_DIR / "assets" / f"meta_review.{lang}.md").read_text(encoding="utf-8").split("---USER---", 1)
    schema = schemas(lang)["MetaReview"]
    digests, texts, by_id = review_material(run, meta)

    def excerpts(prop: dict) -> str:
        ids = list(dict.fromkeys(c["paper_id"] for c in prop["closest_work"] if c["paper_id"] in texts))[:REVIEW_EXCERPT_PAPERS]
        parts = [t["excerpt"].format(line=paper_line(by_id[pid], t), text=clip(texts[pid].read_text(encoding="utf-8"), REVIEW_EXCERPT_CHARS, t))
                 for pid in ids if pid in by_id]
        return "\n\n".join(parts) or t["no_excerpts"]

    def prompt(rec: dict) -> tuple[str, str]:
        values = {"language": meta["language"], "proposal": proposal_text(rec["proposal_id"], rec["proposal"], t),
                  "digests": digests, "fulltexts": excerpts(rec["proposal"])}
        return Template(system_raw.strip()).safe_substitute(values), Template(user_raw.strip()).safe_substitute(values)

    if args.dump:
        # 没有别家模型时给子代理用：一份 proposal 一个文件，里面是评审要求、材料和输出格式
        out = run.dir / "review_prompts"
        out.mkdir(exist_ok=True)
        for rec in proposals:
            system, user = prompt(rec)
            path = out / f"{rec['proposal_id']}.md"
            path.write_text(f"{system}\n\n{user}\n\n---\n{t['json_instruction'].format(name='MetaReview')}\n"
                            f"{json.dumps(schema, ensure_ascii=False)}\n", encoding="utf-8")
            print(f"  {path}")
        print("每份交给一个新的子代理，结果按 references/formats.md 第 9 节追加进 09_meta_reviews.json。")
        return

    writer = meta["writer"]["family"]
    specs = args.reviewer or [r[0] for r in available_reviewers(writer) if not r[2]]
    if not specs:
        raise Stop("本机没有别家公司的评审可用。运行 ir.py reviewers 看看，或者按 references/steps.md 第 9 节用子代理代评。")
    reviewers = [Reviewer(s, args.timeout) for s in specs]
    for r in reviewers:
        if r.family == writer:
            print(f"注意：{r.label} 和写 proposal 的是同一家（{writer}）的模型，评审意见参考价值打折。", flush=True)

    saved = {(m["proposal_id"], m["reviewer"]): m for m in run.load("09_meta_reviews", [])}
    jobs = [(rec, rv) for rec in proposals for rv in reviewers if not (saved.get((rec["proposal_id"], rv.name)) or {}).get("review")]
    if not jobs:
        print("这些评审都已经做完了。下一步：运行 next。")
        return
    print(f"元评审：{len(proposals)} 份 proposal × {len(reviewers)} 位评审，要做 {len(jobs)} 份（每位评审同时最多 2 份）……", flush=True)
    save_lock = threading.Lock()

    def one(rec: dict, rv: Reviewer) -> None:
        pid = rec["proposal_id"]
        system, user = prompt(rec)
        record = {"proposal_id": pid, "reviewer": rv.name, "model": rv.model, "family": rv.family, "review": None, "error": ""}
        start = time.time()
        try:
            text = rv.complete(system, user, schema, lang)
            try:
                review = extract_json(text)
                errs = check(review, "MetaReview")
            except ValueError as e:
                errs = [str(e)]
            if errs:   # 格式不对，带上错误再要一次
                text = rv.complete(system, f"{user}\n\n{t['retry'].format(error='；'.join(errs))}", schema, lang)
                review = extract_json(text)
                if errs := check(review, "MetaReview"):
                    raise RuntimeError("输出格式不对：" + "；".join(errs[:3]))
            record.update(review=review, model=rv.used_model or rv.model)
            print(f"  {rv.label} 评审完 {pid}（{time.time() - start:.0f} 秒）", flush=True)
        except (RuntimeError, ValueError, OSError) as e:
            record["error"] = str(e)[:500]
            print(f"  {rv.label} 评审 {pid} 失败：{record['error'][:200]}", flush=True)
        with save_lock:
            saved[(pid, rv.name)] = record
            run.save("09_meta_reviews", list(saved.values()))
            usage = run.load("usage", {})
            usage[rv.name] = {"model": rv.used_model or rv.model or rv.name, **rv.usage}
            run.save("usage", usage)

    with ThreadPoolExecutor(max_workers=2 * len(reviewers)) as pool:
        list(pool.map(lambda job: one(*job), jobs))
    ok = sum(1 for m in saved.values() if m.get("review"))
    print(f"完成：成功 {ok} 份，失败 {len(saved) - ok} 份。下一步：运行 next。")


def cmd_reviewers(args) -> None:
    found = available_reviewers(args.writer)
    if not found:
        print("本机没找到 claude / codex / gemini 命令，也没配 IDEA_REVIEW_API_BASE 和 IDEA_REVIEW_API_KEY。")
        return
    for name, family, same in found:
        print(f"  {name}（{family}）" + ("  ← 和你是同一家，不适合做评审" if same else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ir.py", description="idea-review skill 的辅助脚本")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="建运行目录，记下 idea 和数量")
    p.add_argument("run")
    p.add_argument("--idea")
    p.add_argument("--lang", choices=LANGUAGES, default="zh", help="报告和笔记用什么语言写")
    p.add_argument("--writer", choices=FAMILIES, help="你（写 proposal 的 agent）是哪家的模型")
    p.add_argument("--writer-model", help="你的模型名，写进报告")
    p.add_argument("--candidates", type=int, help=f"候选篇数，默认 {DEFAULTS['max_candidates']}")
    p.add_argument("--refs", type=int, help=f"精读篇数，默认 {DEFAULTS['download']}")
    p.add_argument("--ideas", type=int, help=f"proposal 份数（含原始 idea），默认 {DEFAULTS['ideas']}")
    p.add_argument("--min-fulltexts", type=int, help=f"至少读到几篇全文才往下找空白，默认 {DEFAULTS['min_fulltexts']}")
    p.add_argument("--per-query", type=int, help=f"每条检索式、每个来源取的篇数，默认 {DEFAULTS['per_query']}")
    p.add_argument("--year-from", type=int, help="只要这一年及以后的论文")
    p = sub.add_parser("search", help="检索、合并去重、取候选")
    p.add_argument("run")
    p.add_argument("--query", action="append", help="直接给检索式，可以写多次；不给就用 01_plan.json 里的")
    p.add_argument("--papers-dir", help="用户自己的 PDF 所在目录，一起读")
    p.add_argument("--again", action="store_true", help="重新检索，覆盖已有候选")
    p = sub.add_parser("fetch", help="按初筛结果下载全文")
    p.add_argument("run")
    p = sub.add_parser("next", help="检查进度和格式，告诉你下一步")
    p.add_argument("run")
    p.add_argument("--force", action="store_true", help="文献证据不够也往下跑（用户同意了才加）")
    p = sub.add_parser("reviewers", help="看本机能用哪些评审模型")
    p.add_argument("--writer", choices=FAMILIES)
    p = sub.add_parser("review", help="请别家模型做元评审")
    p.add_argument("run")
    p.add_argument("--reviewer", action="append", help="claude / codex / gemini / api，可以写成 codex:模型名；可以写多次")
    p.add_argument("--timeout", type=int, default=900, help="每次调用最多等多少秒，默认 900")
    p.add_argument("--dump", action="store_true", help="不调用模型，只把评审材料写进 review_prompts/，给子代理代评用")
    args = ap.parse_args(argv)
    for key in ("candidates", "refs", "ideas", "min_fulltexts", "per_query"):
        value = getattr(args, key, None)
        if value is not None and value < 1:
            print(f"--{key.replace('_', '-')} 要大于 0", file=sys.stderr)
            return 2
    try:
        {"init": cmd_init, "search": cmd_search, "fetch": cmd_fetch, "next": cmd_next,
         "reviewers": cmd_reviewers, "review": cmd_review}[args.cmd](args)
    except Stop as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
