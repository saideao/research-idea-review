"""把运行目录里各步的 JSON 渲染成一个自包含的 HTML 报告。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from . import prompts
from .config import Config
from .pipeline import RunStore, evidence_need, evidence_shortfall, load_chairs
from .schemas import (
    ChairRecord, FullText, IdeaSeeds, Landscape, MetaReviewRecord, Paper, ProposalRecord, QueryPlan,
    ReviewRecord, ScreenItem,
)
from .texts import TEXTS

_REF = re.compile(r"\b([PL]\d{2,3})\b")
_GAP = re.compile(r"\b(G\d{1,2})\b")
_PAGE = re.compile(r"\[(p\.\d+(?:[-–]\d+)?)\]")


def refs(text) -> Markup:
    """转义后把论文编号、空白编号变成页内链接，把 [p.6] 变成小标签。"""
    if text is None:
        return Markup("")
    s = str(escape(str(text)))
    s = _REF.sub(r'<a class="ref" href="#paper-\1">\1</a>', s)
    s = _GAP.sub(r'<a class="ref gap" href="#gap-\1">\1</a>', s)
    s = _PAGE.sub(r'<span class="page">\1</span>', s)
    return Markup(s)


# 报告页面的固定文字：中文用 report.html.j2，英文用 report.en.html.j2，这里是两个模板共用的词表
LEVEL = {
    "zh": {"high": "高", "medium": "中", "low": "低"},
    "en": {"high": "High", "medium": "Medium", "low": "Low"},
}
KIND = {
    "zh": {"untested_assumption": "没检验过的假设", "contradiction": "结论互相矛盾", "limitation": "已知局限",
           "missing_setting": "没覆盖的场景", "measurement": "评测方法的问题", "transfer": "方法迁移", "other": "其他"},
    "en": {"untested_assumption": "Untested assumption", "contradiction": "Contradictory conclusions",
           "limitation": "Known limitation", "missing_setting": "Uncovered setting", "measurement": "Flawed evaluation",
           "transfer": "Method transfer", "other": "Other"},
}
COUNT_NOTES = {
    "zh": {"few_candidates": "检索结果去重后只有这么多", "kept_candidates": "调小数量不会删掉已有的候选",
           "manual": "含你手动放进 papers/ 的 {n} 篇", "few_fulltexts": "高、中相关又能下载到全文的只有这么多",
           "kept_fulltexts": "调小数量不会删掉已经读过的", "expanded": "全文不够，自动从检索结果里多取了 {n} 篇"},
    "en": {"few_candidates": "that is all the search returned after deduplication",
           "kept_candidates": "lowering the number does not remove existing candidates",
           "manual": "includes {n} you put into papers/",
           "few_fulltexts": "that is all the high- and medium-relevance papers with downloadable full texts",
           "kept_fulltexts": "lowering the number does not remove papers already read",
           "expanded": "{n} more were taken from the search results automatically because full texts were short"},
}
TEMPLATES = {"zh": "report.html.j2", "en": "report.en.html.j2"}


CHAIR_AC = {"zh": "综合时补充", "en": "added by the chair"}


def chair_view(c: ChairRecord | None, lang: str) -> dict | None:
    """综合元评审里写的是评审代号，换回评审名字。AC 是综合的模型自己补的。"""
    if not c or not c.summary:
        return None
    codes = c.reviewers

    def names(items: list[str]) -> list[str]:
        out = []
        for item in items:
            m = re.fullmatch(r"\s*(?:评审|Reviewer)?\s*([A-Z]{1,2})\s*", item)
            code = m.group(1) if m else item
            out.append(CHAIR_AC[lang] if code == "AC" else codes.get(code, item))
        return out

    s = c.summary.model_dump()
    for i in s["key_issues"]:
        i["raised_by"] = names(i["raised_by"])
        i["everyone"] = len(codes) > 1 and set(i["raised_by"]) >= set(codes.values())
    for o in s["overlap_check"]:
        o["raised_by"] = names(o["raised_by"])
    s["first_experiment"]["drawn_from"] = names(s["first_experiment"]["drawn_from"])
    sep = "、" if lang == "zh" else ", "
    return {"s": s, "chair": c.chair, "detail": " · ".join(x for x in (c.model, c.effort) if x), "family": c.family,
            "n": len(codes), "legend": sep.join(f"{k} = {v}" for k, v in codes.items())}


def overlap_tally(metas: list[MetaReviewRecord], by_id: dict[str, Paper]) -> dict:
    """数几家评审各自把哪些论文列为撞车。由程序统计，不靠模型归纳：只有一家这么说的，报告里标成不稳。"""
    done = [m for m in metas if m.review]
    rows: dict[str, dict] = {}
    for m in done:
        for o in m.review.overlap:
            known = o.paper_id in by_id
            # 附上的论文以外的工作没有编号，按标题归并
            key = o.paper_id if known else "title:" + re.sub(r"\W+", "", o.title.lower())
            row = rows.setdefault(key, {"paper_id": o.paper_id if known else "",
                                        "title": by_id[o.paper_id].title if known else o.title, "by": []})
            if m.reviewer not in row["by"]:
                row["by"].append(m.reviewer)
    ordered = sorted(rows.values(), key=lambda r: (-len(r["by"]), not r["paper_id"], r["paper_id"], r["title"]))
    return {"n": len(done), "rows": ordered}


def build_report(run_dir: Path, cfg: Config) -> Path:
    store = RunStore(run_dir)
    meta = json.loads(store.path("meta").read_text(encoding="utf-8")) if store.has("meta") else {}
    plan = store.load("01_plan", QueryPlan)
    papers = store.load("02_candidates", list[Paper]) or []
    screen = {s.paper_id: s for s in (store.load("03_screen", list[ScreenItem]) or [])}
    fulltexts = store.load("04_fulltext", dict[str, FullText]) or {}
    reviews = {r.paper_id: r for r in (store.load("05_reviews", list[ReviewRecord]) or [])}
    landscape = store.load("06_landscape", Landscape)
    seeds = store.load("07_seeds", IdeaSeeds)
    proposals = store.load("08_proposals", list[ProposalRecord]) or []
    metas = store.load("09_meta_reviews", list[MetaReviewRecord]) or []
    chairs = {c.proposal_id: c for c in load_chairs(store)}
    usage = json.loads(store.path("usage").read_text(encoding="utf-8")) if store.has("usage") else {}
    # 报告用这次运行的语言版本；老的运行目录 meta.json 里没有，就用当前配置
    lang = meta.get("lang", cfg.review.lang)

    by_id = {p.paper_id: p for p in papers}
    read = []
    for pid, r in reviews.items():
        if r.review and pid in by_id:
            f = fulltexts.get(pid)
            pdf = str(store.file(f.pdf_path).resolve()) if f and f.pdf_path else None
            read.append({"paper": by_id[pid], "review": r.review, "truncated": r.truncated, "pdf": pdf})
    read.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["review"].quality.tier], x["paper"].paper_id))
    failed_reads = [{"paper": by_id[pid], "error": r.error} for pid, r in reviews.items() if not r.review and pid in by_id]
    missing = [{"paper": by_id[pid], "fulltext": f} for pid, f in fulltexts.items()
               if f.status != "ok" and pid in by_id]
    reviewed_ids = {x["paper"].paper_id for x in read} | {x["paper"].paper_id for x in missing} | {x["paper"].paper_id for x in failed_reads}
    others = [{"paper": p, "screen": screen.get(p.paper_id)} for p in papers if p.paper_id not in reviewed_ids]
    others.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["screen"].relevance if x["screen"] else "low", 3))

    proposal_views = []
    for rec in proposals:
        mine = [m for m in metas if m.proposal_id == rec.proposal_id]
        proposal_views.append({
            "rec": rec,
            "metas": mine,
            "tally": overlap_tally(mine, by_id),
            "chair": chair_view(chairs.get(rec.proposal_id), lang),
        })

    # 这次运行设定的数量；老的运行目录 meta.json 里没有，就用当前配置
    s = cfg.search
    counts = {"per_query": s.per_query, "max_candidates": s.max_candidates, "download": s.download} | (meta.get("search") or {})
    stats = {
        "candidates": sum(1 for p in papers if not p.local_path),
        "fulltext": sum(1 for p in papers if not p.local_path and p.paper_id in fulltexts and fulltexts[p.paper_id].status == "ok"),
        "local": sum(1 for p in papers if p.local_path),
        "read": len(read),
        "high": sum(1 for x in read if x["review"].quality.tier == "high"),
        "low": sum(1 for x in read if x["review"].quality.tier == "low"),
        "mismatch": sum(1 for x in read if not x["review"].abstract_vs_body.consistent),
        "proposals": sum(1 for r in proposals if r.proposal),
        "reviewers": len(cfg.roles.reviewers),
        "gaps": len(landscape.gaps) if landscape else 0,
    }
    # 设定的数量和实际对不上时，说明原因
    manual = sum(1 for f in fulltexts.values() if f.status == "ok" and f.manual)
    expanded = meta.get("expanded", 0)
    notes = COUNT_NOTES[lang]
    count_notes = {"candidates": [], "fulltext": []}
    if expanded:
        count_notes["candidates"].append(notes["expanded"].format(n=expanded))
    if stats["candidates"] < counts["max_candidates"]:
        count_notes["candidates"].append(notes["few_candidates"])
    elif stats["candidates"] - expanded > counts["max_candidates"]:
        count_notes["candidates"].append(notes["kept_candidates"])
    if manual:
        count_notes["fulltext"].append(notes["manual"].format(n=manual))
    if stats["fulltext"] < counts["download"]:
        count_notes["fulltext"].append(notes["few_fulltexts"])
    elif stats["fulltext"] - manual > counts["download"]:
        count_notes["fulltext"].append(notes["kept_fulltexts"])

    # 读到的全文太少或者全是低质量：没找空白的说明停在了第 5 步，加 --force 跑完的提醒证据偏弱
    shortfall = evidence_shortfall(reviews, evidence_need(cfg.review.min_fulltexts, counts["download"]))
    # 证据里没有一篇高、中质量全文的空白，单独标出来
    strong_ids = {x["paper"].paper_id for x in read if x["review"].quality.tier in ("high", "medium")}
    weak_gaps = {i for i, g in enumerate(landscape.gaps, start=1)
                 if not any(e.paper_id in strong_ids for e in g.evidence)} if landscape else set()

    env = Environment(loader=PackageLoader("idea_review", "templates"), autoescape=select_autoescape(["html", "j2"]))
    env.filters["refs"] = refs
    env.globals.update(LEVEL=LEVEL[lang], KIND=KIND[lang], PAPER_TYPE=TEXTS[lang]["paper_types"], lang=lang)
    html = env.get_template(TEMPLATES[lang]).render(
        meta=meta, idea=meta.get("idea", ""), plan=plan, stats=stats, counts=counts, count_notes=count_notes, read=read, failed_reads=failed_reads,
        missing=missing, others=others, landscape=landscape, seeds=seeds, proposals=proposal_views,
        shortfall=shortfall, stopped=bool(shortfall and not landscape), weak_gaps=weak_gaps,
        cfg=cfg, warnings=cfg.warnings(lang), usage=usage, run_dir=str(run_dir.resolve()),
        prompt_texts=[(n, prompts.raw(n, lang)) for n in prompts.NAMES],
    )
    out = run_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out
