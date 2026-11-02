"""整条流程：检索 → 初筛 → 下载全文 → 精读评质量 → 找空白 → 新方向 → 写 proposal → 多模型元评审 → 汇总 → 报告。

每一步的结果都存成 JSON 放在运行目录里。中途失败或者想换个评审模型重跑，
再运行一次同一个目录就会跳过已经完成的步骤。

接着跑时调大了候选篇数或精读篇数：候选从存下的检索结果里往下取，补初筛；精读不够的补下载、补精读。
你按编号手动放进 papers/ 的 PDF（比如 P07.pdf）也会在接着跑时补读。
只要补进了新的全文，或者之前精读失败的这次读成功了，第 6–10 步就按新的文献重做，旧结果挪进 history/。
调小数量不删任何已有结果。

全文不够数、候选里能下载的又用完了，会从存下的检索结果里再取一批候选，最多扩到设定候选数的 3 倍。
读到的全文太少或者全是低质量，就停在第 5 步，不往下找空白、写 proposal；加 --force 才照样往下跑。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from . import prompts
from .config import COUNT_LIMITS, Config
from .llm import LLMError, ProviderPool, UsageTracker
from .pdfs import clip, fetch_fulltexts, pdf_title
from .texts import TEXTS
from .schemas import (
    ChairRecord, ChairSummary, FullText, IdeaSeeds, Landscape, MetaReview, MetaReviewRecord, Paper,
    PaperReview, Proposal, ProposalRecord, QueryPlan, ReviewRecord, ScreenBatch, ScreenItem,
)
from .search import search_all

TIER_ORDER = {"high": 0, "medium": 1, "low": 2}
# 全文不够数时，候选最多自动扩到设定数量的几倍
EXPAND_FACTOR = 3
# 找空白时空白个数的上下限：按读到的全文篇数定，读得少就少写
MIN_GAPS, MAX_GAPS = 3, 8
# 依赖精读笔记的几步：补读了新论文就要重做
DOWNSTREAM = ("06_landscape", "07_seeds", "08_proposals", "09_meta_reviews", "10_chair")


class PipelineError(Exception):
    pass


class RunStore:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def load(self, name: str, type_):
        if not self.has(name):
            return None
        return TypeAdapter(type_).validate_json(self.path(name).read_text(encoding="utf-8"))

    def file(self, stored: str) -> Path:
        """把记录里的文件路径换成现在能用的路径。现在记的是相对运行目录的路径；
        老版本记的是相对启动目录的路径或绝对路径，运行目录挪过位置也按 papers/、text/ 找回来。"""
        path = Path(stored)
        candidates = [path] if path.is_absolute() else [self.dir / path]
        candidates += [self.dir / path.parent.name / path.name, path]
        return next((c for c in candidates if c.exists()), candidates[0])

    def save(self, name: str, value, type_) -> None:
        data = TypeAdapter(type_).dump_python(value, mode="json")
        self.path(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def evidence_shortfall(reviews: dict[str, ReviewRecord], need: int) -> dict | None:
    """读到的全文太少，或者全是低质量，拿它们找空白、写 proposal 意义不大。返回停下的原因，证据够了返回 None。"""
    done = [r.review for r in reviews.values() if r.review]
    strong = sum(1 for r in done if r.quality.tier in ("high", "medium"))
    if len(done) < need:
        return {"reason": "few", "read": len(done), "need": need, "strong": strong}
    if not strong:
        return {"reason": "all_low", "read": len(done), "need": need, "strong": 0}
    return None


def evidence_need(min_fulltexts: int, download: int) -> int:
    # 精读篇数本身设得比门槛小时（比如 --refs 2），按精读篇数算，不然永远过不了
    return min(min_fulltexts, download)


def _duration(seconds: float) -> str:
    return f"{seconds:.0f} 秒" if seconds < 60 else f"{seconds / 60:.1f} 分钟"


def make_logger(run_dir: Path):
    log_path = run_dir / "run.log"

    def log(msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return log


# ---------- 给模型看的文本（固定文字在 texts.py，中英文各一份） ----------

def paper_line(p: Paper, t: dict) -> str:
    bits = [str(p.year or t["year_unknown"]), p.venue or t["venue_unknown"], t["published"][p.peer_reviewed]]
    if p.citation_count is not None:
        bits.append(t["cited"].format(n=p.citation_count))
    return t["paper_line"].format(id=p.paper_id, title=p.title, bits=t["bits_sep"].join(bits))


def review_digest(p: Paper, r: PaperReview, t: dict) -> str:
    q, join, none = r.quality, t["join"].join, t["none"]
    text = t["review_digest"].format(
        line=paper_line(p, t), kind=t["paper_types"][q.paper_type], tier=q.tier, does=r.what_it_actually_does, method=r.method, setup=r.setup,
        results=join(r.key_results), limits=join(r.limitations) or none, open=join(r.open_questions) or none,
        strength=q.evidence_strength, flags=join(q.red_flags) or none,
    )
    for key in ("overclaims", "scope_gaps"):
        items = getattr(r.abstract_vs_body, key)
        if items:
            text += "\n" + t[key].format(items=join(items))
    return text


def abstract_digest(p: Paper, t: dict) -> str:
    return t["abstract_digest"].format(line=paper_line(p, t), abstract=p.abstract or t["none"])


def proposal_text(pid: str, prop: Proposal, t: dict) -> str:
    exp, join = prop.minimal_experiment, t["join"].join
    closest = "\n".join(t["closest_item"].format(id=c.paper_id, overlap=c.overlap, difference=c.difference)
                        for c in prop.closest_work) or f"  {t['none']}"
    return t["proposal_text"].format(
        pid=pid, title=prop.title, one_liner=prop.one_liner, problem=prop.problem, why=prop.why_existing_fail,
        closest=closest, method=prop.method, setup=exp.setup, data=exp.data,
        baselines=t["names_sep"].join(exp.baselines), metrics=t["names_sep"].join(exp.metrics), duration=exp.duration,
        falsification=prop.falsification, risks=join(prop.risks), checking=join(prop.needs_checking) or t["none"],
    )


def meta_review_text(reviewer: str, mr: MetaReview, t: dict) -> str:
    overlap = "\n".join(t["overlap_item"].format(id=o.paper_id, title=o.title, overlap=o.overlap, difference=o.difference)
                        for o in mr.overlap) or f"  {mr.no_overlap_note}"
    deaths = "\n".join(t["death_item"].format(reason=d.reason, basis=d.basis, source=t["death_source"][d.source])
                       for d in mr.causes_of_death)
    f = mr.cheapest_falsification
    return t["meta_review_text"].format(
        reviewer=reviewer, overlap=overlap, deaths=deaths, bottleneck=mr.feasibility.bottleneck,
        workaround=mr.feasibility.workaround, experiment=f.experiment, compare=f.compare_against, metric=f.metric,
        kill=f.kill_if, go=f.continue_if, days=f.est_days, questions=t["join"].join(mr.questions_for_author),
    )


# ---------- 流程 ----------

class Pipeline:
    def __init__(self, cfg: Config, run_dir: Path, idea: str, papers_dir: Path | None = None,
                 queries: list[str] | None = None, force: bool = False):
        self.cfg = cfg
        self.idea = idea.strip()
        self.papers_dir = papers_dir
        self.queries = queries or []
        self.force = force
        self.stopped: dict | None = None    # 证据不够、停在第 5 步时记下原因
        self.fetched = False                # 这次有没有下载过全文
        self.store = RunStore(run_dir)
        self.log = make_logger(run_dir)
        self.tracker = UsageTracker()
        saved_usage = self.store.path("usage")
        if saved_usage.exists():
            self.tracker.merge(json.loads(saved_usage.read_text(encoding="utf-8")))
        self.pool = ProviderPool(cfg.providers, self.tracker)
        self.lang = cfg.review.language        # 模型用什么语言写，比如 "中文"、"English"
        self.lang_code = cfg.review.lang       # 提示词和固定文字用哪个版本：zh / en
        self.t = TEXTS[self.lang_code]
        self.prev_counts: dict = {}

    def _save_usage(self) -> None:
        self.store.path("usage").write_text(json.dumps(self.tracker.by_provider, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _call(self, role_or_name: str, prompt: str, model_cls, *, by_name: bool = False, **values):
        name = role_or_name if by_name else getattr(self.cfg.roles, role_or_name)
        provider = self.pool.get(name)
        system, user = prompts.render(prompt, self.lang_code, language=self.lang, **values)
        try:
            return await provider.structured(system, user, model_cls, lang=self.lang_code)
        finally:
            self._save_usage()

    def _write_meta(self) -> None:
        """meta.json 记下 idea 和这次用的数量。接着跑时数量以这里为准，除非命令行另给。"""
        path = self.store.path("meta")
        meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "idea": self.idea,
            "created": datetime.now().isoformat(timespec="seconds"),
            "papers_dir": str(self.papers_dir) if self.papers_dir else None,
            "queries": self.queries or None,
            "lang": self.lang_code,
            "language": self.lang,
        }
        self.prev_counts = meta.get("search") or {}
        s = self.cfg.search
        counts = {"per_query": s.per_query, "max_candidates": s.max_candidates, "download": s.download}
        if self.store.has("02_candidates") and "per_query" in self.prev_counts:
            # 已经检索过，per_query 改了也不会重新检索，记实际用的
            counts["per_query"] = self.prev_counts["per_query"]
        meta["search"] = counts
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        names = {"max_candidates": "候选", "download": "精读"}
        changed = [f"{names[k]} {self.prev_counts[k]} → {counts[k]}" for k in names
                   if k in self.prev_counts and self.prev_counts[k] != counts[k]]
        self.log(f"数量：候选最多 {counts['max_candidates']} 篇，精读 {counts['download']} 篇"
                 + (f"（这次改了：{'，'.join(changed)}）" if changed else ""))

    def _bump_meta(self, key: str, n: int) -> None:
        path = self.store.path("meta")
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta[key] = meta.get(key, 0) + n
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _archive_downstream(self, why: str) -> None:
        """精读的论文变多了，第 6–10 步要按新的笔记重做。旧结果挪进 history/ 留底，不删。"""
        old = [self.store.path(n) for n in DOWNSTREAM if self.store.has(n)]
        if not old:
            return
        if (self.store.dir / "report.html").exists():
            old.append(self.store.dir / "report.html")
        dest = self.store.dir / "history" / f"{datetime.now():%Y%m%d-%H%M%S}"
        dest.mkdir(parents=True, exist_ok=True)
        for f in old:
            f.rename(dest / f.name)
        self.log(f"  {why}，第 6–10 步要按新的文献重做。旧结果移到了 {dest.relative_to(self.store.dir)}/")

    async def run(self) -> Path:
        start = time.time()
        self._write_meta()
        for w in self.cfg.warnings():
            self.log(f"注意：{w}")

        plan = await self._timed(self.step_plan())
        papers = await self._timed(self.step_search(plan))
        screen = await self._timed(self.step_screen(plan, papers))
        fulltexts = await self._timed(self.step_fulltext(papers, screen))
        while more := self._expand_candidates(papers, fulltexts):
            papers = more
            screen = await self._timed(self.step_screen(plan, papers))
            fulltexts = await self._timed(self.step_fulltext(papers, screen))
        self._log_fulltext_shortfall(papers, fulltexts)
        reviews = await self._timed(self.step_read(papers, fulltexts))

        # 已经找过空白的（比如加了 --force 跑完的），接着跑时不再拦
        shortfall = None if self.store.has("06_landscape") else evidence_shortfall(
            reviews, evidence_need(self.cfg.review.min_fulltexts, self.cfg.search.download))
        if shortfall and not self.force:
            self.stopped = shortfall
            self._log_stop(shortfall)
        else:
            if shortfall:
                self.log("证据不够，但加了 --force，照样往下跑。后面的空白和 proposal 只能当线索。")
            landscape = await self._timed(self.step_landscape(papers, screen, reviews))
            proposals = await self._timed(self.step_proposals(papers, reviews, landscape))
            metas = await self._timed(self.step_meta_reviews(papers, fulltexts, reviews, proposals))
            await self._timed(self.step_chair(proposals, metas))

        from .report import build_report
        out = build_report(self.store.dir, self.cfg)
        done = "停在第 5 步" if self.stopped else "完成"
        self.log(f"{done}，用时 {_duration(time.time() - start)}。报告：{out}")
        return out

    async def _timed(self, step):
        """跑一步。真的干了活（超过 1 秒）就记下用时，方便看时间都花在哪。"""
        start = time.time()
        result = await step
        if (spent := time.time() - start) >= 1:
            self.log(f"  用时 {_duration(spent)}")
        return result

    def _log_stop(self, why: dict) -> None:
        if why["reason"] == "few":
            self.log(f"只读到 {why['read']} 篇全文，少于 {why['need']} 篇（review.min_fulltexts），"
                     "拿这么点文献找空白、写 proposal 意义不大，第 6–10 步没有跑。")
        else:
            self.log(f"读到的 {why['read']} 篇全文都是低质量，拿它们找空白、写 proposal 意义不大，第 6–10 步没有跑。")
        self.log(f"  想补文献：把相关论文的 PDF 按编号放进 {self.store.dir / 'papers'}/（比如 P07.pdf），"
                 f"再运行 idea-review run --resume {self.store.dir}；检索结果本身太少的话，换几条检索式开一次新的运行。")
        self.log("  确实想用现有的文献跑完，接着跑时加 --force。")

    def _expand_candidates(self, papers: list[Paper], fulltexts: dict[str, FullText]) -> list[Paper] | None:
        """全文不够数、候选里能下载的又用完了，就从第一次检索存下的结果里再取一批候选。
        最多扩到设定候选数的 EXPAND_FACTOR 倍。返回加了新候选的列表；不用扩或者扩不了返回 None。"""
        s = self.cfg.search
        got = sum(1 for p in papers if not p.local_path and p.paper_id in fulltexts and fulltexts[p.paper_id].status == "ok")
        if got >= s.download:
            return None
        have = sum(1 for p in papers if not p.local_path)
        cap = min(s.max_candidates * EXPAND_FACTOR, COUNT_LIMITS["max_candidates"][1])
        pool = self.store.load("02_search_results", list[Paper]) or []
        take = min(cap - have, max(10, 3 * (s.download - got)))
        extra = pool[have:have + take] if take > 0 else []
        if not extra:
            return None
        for i, p in enumerate(extra, start=have + 1):
            p.paper_id = f"P{i:02d}"
        self.log(f"  全文还差 {s.download - got} 篇，候选里能下载的已经用完。"
                 f"从检索结果里再取 {len(extra)} 篇候选（{extra[0].paper_id}–{extra[-1].paper_id}）去初筛")
        papers = papers + extra
        self.store.save("02_candidates", papers, list[Paper])
        self._bump_meta("expanded", len(extra))
        return papers

    def _log_fulltext_shortfall(self, papers: list[Paper], fulltexts: dict[str, FullText]) -> None:
        target = self.cfg.search.download
        got = sum(1 for p in papers if not p.local_path and p.paper_id in fulltexts and fulltexts[p.paper_id].status == "ok")
        if not self.fetched or got >= target:
            return
        have = sum(1 for p in papers if not p.local_path)
        pool = self.store.load("02_search_results", list[Paper]) or []
        why = "检索结果都试过了" if have >= len(pool) else f"候选已经自动扩到 {have} 篇，到了上限"
        self.log(f"  只拿到 {got} 篇全文，没到设定的 {target} 篇：{why}。"
                 "想多读几篇，可以把 PDF 按编号放进 papers/ 再 --resume，或者换几条检索式开一次新的运行。")

    async def step_plan(self) -> QueryPlan:
        plan = self.store.load("01_plan", QueryPlan)
        if plan:
            return plan
        self.log("第 1 步：拆检索词")
        try:
            plan = await self._call("planner", "plan_queries", QueryPlan, idea=self.idea)
        except LLMError as e:
            raise PipelineError(f"拆检索词失败：{e}") from e
        if self.queries:
            plan.search_queries = self.queries
        self.store.save("01_plan", plan, QueryPlan)
        self.log(f"  检索式{'（你给的）' if self.queries else ''}：{plan.search_queries}")
        return plan

    async def step_search(self, plan: QueryPlan) -> list[Paper]:
        papers = self.store.load("02_candidates", list[Paper])
        if papers is not None:
            return self._more_candidates(papers)
        self.log("第 2 步：检索文献")
        s = self.cfg.search
        found = await search_all(plan.search_queries, s.sources, s.per_query, s.year_from,
                                 os.environ.get(s.s2_api_key_env), s.openalex_mailto, self.log)
        # 完整的检索结果也存下来：接着跑时调大候选篇数，直接从这里往下取，不用重新检索
        self.store.save("02_search_results", found, list[Paper])
        found = found[: s.max_candidates]
        for i, p in enumerate(found, start=1):
            p.paper_id = f"P{i:02d}"
        local = []
        if self.papers_dir:
            pdfs = sorted(self.papers_dir.glob("*.pdf"))
            for i, f in enumerate(pdfs, start=1):
                local.append(Paper(paper_id=f"L{i:02d}", title=pdf_title(f), sources=["local"], local_path=str(f.resolve())))
            self.log(f"  本地 PDF：{len(local)} 篇")
        papers = local + found
        if not papers:
            raise PipelineError("一篇论文都没检索到。可以换个说法重写 idea，或者用 --papers-dir 指定本地 PDF。")
        self.store.save("02_candidates", papers, list[Paper])
        self.log(f"  合并去重后 {len(found)} 篇候选")
        return papers

    def _more_candidates(self, papers: list[Paper]) -> list[Paper]:
        """接着跑时调大了候选篇数：从存下的检索结果里接着往下取。已有候选和编号都不动。"""
        target = self.cfg.search.max_candidates
        prev = self.prev_counts.get("max_candidates", target)
        have = sum(1 for p in papers if not p.local_path)
        if target <= have:
            if target < prev:
                self.log(f"  已经有 {have} 篇候选，比设定的 {target} 篇多。调小数量不会删掉已有的候选。")
            return papers
        pool = self.store.load("02_search_results", list[Paper])
        extra = pool[have:target] if pool else []
        if not extra:
            if target > prev:
                self.log(f"  检索结果去重后一共只有 {len(pool)} 篇，已经都是候选了。想要更多，调大 search.per_query 开一次新的运行。"
                         if pool is not None else "  这个运行目录没存完整的检索结果，候选篇数加不了。")
            return papers
        self.log(f"第 2 步：候选从 {have} 篇加到 {have + len(extra)} 篇（从存下的检索结果里往下取，不重新检索）")
        for i, p in enumerate(extra, start=have + 1):
            p.paper_id = f"P{i:02d}"
        papers = papers + extra
        self.store.save("02_candidates", papers, list[Paper])
        return papers

    async def step_screen(self, plan: QueryPlan, papers: list[Paper]) -> dict[str, ScreenItem]:
        saved = {s.paper_id: s for s in (self.store.load("03_screen", list[ScreenItem]) or [])}
        todo = [p for p in papers if p.paper_id not in saved]
        if not todo:
            return saved
        remote = [p for p in todo if not p.local_path]
        self.log(f"第 3 步：初筛新加的 {len(remote)} 篇候选" if saved else "第 3 步：按摘要初筛（只决定先读哪些全文）")
        size = self.cfg.review.screen_batch
        batches = [remote[i:i + size] for i in range(0, len(remote), size)]

        async def one(batch: list[Paper]) -> list[ScreenItem]:
            listing = "\n\n".join(self.t["screen_item"].format(line=paper_line(p, self.t), abstract=(p.abstract or self.t["no_abstract"])[:1500])
                                  for p in batch)
            try:
                result = await self._call("screener", "screen", ScreenBatch, idea=self.idea,
                                          restated=plan.restated_idea, papers=listing)
            except LLMError as e:
                self.log(f"  一批初筛失败，这批全部按 medium 处理：{e}")
                return [ScreenItem(paper_id=p.paper_id, relevance="medium", reason=self.t["screen_failed"]) for p in batch]
            got = {i.paper_id: i for i in result.items}
            return [got.get(p.paper_id) or ScreenItem(paper_id=p.paper_id, relevance="medium", reason=self.t["screen_missed"]) for p in batch]

        items = [i for chunk in await asyncio.gather(*(one(b) for b in batches)) for i in chunk]
        items += [ScreenItem(paper_id=p.paper_id, relevance="high", reason=self.t["screen_local"]) for p in todo if p.local_path]
        saved.update((i.paper_id, i) for i in items)
        self.store.save("03_screen", list(saved.values()), list[ScreenItem])
        counts = {lv: sum(1 for i in items if i.relevance == lv) for lv in ("high", "medium", "low")}
        self.log(f"  初筛结果：{counts}")
        return saved

    def _manual_pdfs(self, papers: list[Paper], saved: dict[str, FullText]) -> list[Paper]:
        """接着跑时，找出你按编号手动放进 papers/ 的 PDF：对应的论文还没拿到全文。"""
        since = self.store.path("04_fulltext").stat().st_mtime
        found = []
        for p in papers:
            pdf = self.store.dir / "papers" / f"{p.paper_id}.pdf"
            old = saved.get(p.paper_id)
            if p.local_path or not pdf.exists() or (old and old.status == "ok"):
                continue
            # 程序下载过、但抽不出文字的 PDF（比如扫描版）也在这里，换成新文件以后才重试
            if old and old.pdf_path and pdf.stat().st_mtime <= since:
                continue
            found.append(p)
        return found

    async def step_fulltext(self, papers: list[Paper], screen: dict[str, ScreenItem]) -> dict[str, FullText]:
        """精读篇数指拿到全文的篇数：下载失败的跳过，按初筛顺序往下补，直到够数或者高、中相关的候选用完。"""
        saved = self.store.load("04_fulltext", dict[str, FullText])
        result = dict(saved or {})
        target = self.cfg.search.download

        def relevance(p: Paper) -> str:
            return screen[p.paper_id].relevance if p.paper_id in screen else "low"

        manual = self._manual_pdfs(papers, saved) if saved is not None else []
        manual_ids = {p.paper_id for p in manual}
        # 排队：先高后中，低相关的不下载；试过的（不管成没成）不再排
        # 没有 PDF 链接的也排队：下载时会按标题去 arXiv 找预印本
        queue = [p for level in ("high", "medium") for p in papers
                 if not p.local_path and p.paper_id not in result and p.paper_id not in manual_ids and relevance(p) == level]
        local = [p for p in papers if p.local_path and p.paper_id not in result]
        got = sum(1 for p in papers if not p.local_path and p.paper_id in result and result[p.paper_id].status == "ok")
        if saved is not None and not manual and not local and (got >= target or not queue):
            if got > target and target < self.prev_counts.get("download", target):
                self.log(f"  已经拿到 {got} 篇全文，比设定的 {target} 篇多。调小数量不会删掉已经下载和精读的论文。")
            return saved

        self.fetched = True
        self.log("第 4 步：下载全文" if saved is None else f"第 4 步：补全文（已有 {got} 篇，要 {target} 篇）")
        if manual:
            self.log(f"  找到你手动放进 papers/ 的 PDF：{'、'.join(sorted(manual_ids))}")
        added, batch = 0, local + manual
        first = batch
        while True:
            take = max(target - got, 0)
            if batch is not first and take and queue:
                self.log(f"  还差 {take} 篇，往下再找")
            batch, queue = batch + queue[:take], queue[take:]
            if not batch:
                break
            fetched = await fetch_fulltexts(batch, self.store.dir, self.log)
            result.update(fetched)
            for p in batch:
                if fetched[p.paper_id].status == "no_pdf":
                    result[p.paper_id] = fetched[p.paper_id].model_copy(update={"note": self.t["no_pdf_note"]})
                if fetched[p.paper_id].status == "ok":
                    added += 1
                    got += 0 if p.local_path else 1
                    if p.paper_id in manual_ids:
                        result[p.paper_id] = fetched[p.paper_id].model_copy(update={"note": self.t["manual_note"], "manual": True})
            batch = []
        if saved is not None and added:
            self._archive_downstream(f"补进了 {added} 篇全文")
        self.store.save("04_fulltext", result, dict[str, FullText])
        n_local = sum(1 for p in papers if p.local_path and result.get(p.paper_id) and result[p.paper_id].status == "ok")
        self.log(f"  拿到全文 {got} 篇" + (f"，另有本地 PDF {n_local} 篇" if n_local else ""))
        return result

    async def step_read(self, papers: list[Paper], fulltexts: dict[str, FullText]) -> dict[str, ReviewRecord]:
        saved = {r.paper_id: r for r in (self.store.load("05_reviews", list[ReviewRecord]) or [])}
        todo = [p for p in papers if fulltexts.get(p.paper_id, FullText(paper_id="", status="skipped")).status == "ok"
                and not (saved.get(p.paper_id) and saved[p.paper_id].review)]
        if not todo:
            return saved
        self.log(f"第 5 步：精读全文、评质量（{len(todo)} 篇）")
        order = {p.paper_id: i for i, p in enumerate(papers)}

        def keep(r: ReviewRecord) -> None:
            if r.review:
                # 多了一篇笔记，已有的第 6–10 步就过时了。先挪走再存笔记，中途断了也不会漏掉重做
                self._archive_downstream(f"{r.paper_id} 这次精读成功了")
            # 每读完一篇就存一次：中途断了，已经读完的不用重读
            saved[r.paper_id] = r
            records = sorted(saved.values(), key=lambda x: order.get(x.paper_id, len(order)))
            self.store.save("05_reviews", records, list[ReviewRecord])

        async def one(p: Paper) -> None:
            text = self.store.file(fulltexts[p.paper_id].text_path).read_text(encoding="utf-8")
            text, truncated = clip(text, self.cfg.review.max_fulltext_chars)
            meta = paper_line(p, self.t) + (self.t["authors"].format(names=self.t["names_sep"].join(p.authors[:8])) if p.authors else "")
            try:
                review = await self._call("reader", "read_paper", PaperReview, idea=self.idea, meta=meta,
                                          abstract=p.abstract or self.t["abstract_missing"], fulltext=text,
                                          truncated_note=self.t["truncated"] if truncated else "")
            except LLMError as e:
                self.log(f"  {p.paper_id} 精读失败：{e}")
                keep(ReviewRecord(paper_id=p.paper_id, error=str(e), truncated=truncated))
                return
            self.log(f"  {p.paper_id} 精读完成：质量 {review.quality.tier}"
                     + ("，摘要夸大或和正文矛盾" if not review.abstract_vs_body.consistent else ""))
            keep(ReviewRecord(paper_id=p.paper_id, review=review, truncated=truncated))

        await asyncio.gather(*(one(p) for p in todo))
        return {pid: saved[pid] for pid in sorted(saved, key=lambda pid: order.get(pid, len(order)))}

    def _digests(self, papers: list[Paper], reviews: dict[str, ReviewRecord], screen: dict[str, ScreenItem] | None = None):
        by_id = {p.paper_id: p for p in papers}
        good = [(by_id[r.paper_id], r.review) for r in reviews.values() if r.review]
        good.sort(key=lambda pr: TIER_ORDER[pr[1].quality.tier])
        strong = [review_digest(p, r, self.t) for p, r in good if r.quality.tier in ("high", "medium")]
        weak = [review_digest(p, r, self.t) for p, r in good if r.quality.tier == "low"]
        if not good and screen:
            # 一篇全文都没拿到时，退回用摘要，并在文字里标明
            strong = [abstract_digest(p, self.t) for p in papers if screen.get(p.paper_id) and screen[p.paper_id].relevance == "high"][:20]
        return strong, weak

    async def step_landscape(self, papers, screen, reviews) -> Landscape:
        landscape = self.store.load("06_landscape", Landscape)
        if landscape:
            return landscape
        self.log("第 6 步：画版图、找空白")
        strong, weak = self._digests(papers, reviews, screen)
        if not strong and not weak:
            raise PipelineError("没有可用的文献笔记，没法找空白。检查网络，或者用 --papers-dir 提供本地 PDF。")
        max_gaps = min(MAX_GAPS, max(MIN_GAPS, len(strong) + len(weak)))
        try:
            landscape = await self._call("miner", "mine_gaps", Landscape, idea=self.idea, max_gaps=max_gaps,
                                         digests="\n\n".join(strong) or self.t["empty"], weak_digests="\n\n".join(weak) or self.t["empty"])
        except LLMError as e:
            raise PipelineError(f"找空白失败：{e}") from e
        if len(landscape.gaps) > max_gaps:
            self.log(f"  模型给了 {len(landscape.gaps)} 个空白，超过上限 {max_gaps} 个，只留前 {max_gaps} 个")
            landscape.gaps = landscape.gaps[:max_gaps]
        self.store.save("06_landscape", landscape, Landscape)
        self.log(f"  找到 {len(landscape.gaps)} 个空白（上限 {max_gaps} 个，按读到的 {len(strong) + len(weak)} 篇文献定）")
        return landscape

    async def step_proposals(self, papers, reviews, landscape: Landscape) -> list[ProposalRecord]:
        records = self.store.load("08_proposals", list[ProposalRecord])
        if records and all(r.proposal for r in records):
            return records
        t = self.t
        gaps_text = "\n".join(
            t["gap_line"].format(i=i, kind=g.kind, source=t["gap_source"][g.source], title=g.title, description=g.description,
                                 evidence=t["join"].join(t["gap_evidence"].format(id=e.paper_id, note=e.note) for e in g.evidence),
                                 closest=t["join"].join(t["gap_evidence"].format(id=c.paper_id, note=c.how_close)
                                                        for c in g.closest_read) or t["none"])
            for i, g in enumerate(landscape.gaps, start=1)
        ) or t["no_gaps"]

        n_seeds = min(max(self.cfg.review.ideas - 1, 0), len(landscape.gaps))
        seeds = self.store.load("07_seeds", IdeaSeeds)
        if seeds is None and n_seeds < self.cfg.review.ideas - 1:
            self.log(f"  只有 {len(landscape.gaps)} 个空白，新方向最多挖 {n_seeds} 个")
        if seeds is None and n_seeds:
            self.log(f"第 7 步：从空白里挖 {n_seeds} 个新方向")
            landscape_text = t["landscape_text"].format(summary=landscape.summary, gaps=gaps_text)
            try:
                seeds = await self._call("writer", "seeds", IdeaSeeds, idea=self.idea, landscape=landscape_text, n=n_seeds)
                seeds.seeds = seeds.seeds[:n_seeds]
            except LLMError as e:
                self.log(f"  挖新方向失败，只写原始 idea 的 proposal：{e}")
                seeds = IdeaSeeds(seeds=[])
            self.store.save("07_seeds", seeds, IdeaSeeds)
        seeds = seeds or IdeaSeeds(seeds=[])

        if not records:
            records = [ProposalRecord(proposal_id="I1", origin="original")]
            records += [ProposalRecord(proposal_id=f"I{i}", origin="seed", seed=s) for i, s in enumerate(seeds.seeds, start=2)]
        strong, weak = self._digests(papers, reviews)
        digests = "\n\n".join(strong + weak) or t["no_fulltext_notes"]
        self.log(f"第 8 步：模型 A 写 proposal（{len(records)} 份）")

        async def one(rec: ProposalRecord) -> ProposalRecord:
            if rec.proposal:
                return rec
            if rec.origin == "original":
                block = t["original_block"].format(idea=self.idea)
            else:
                s = rec.seed
                block = t["seed_block"].format(idea=self.idea, title=s.title, one_liner=s.one_liner,
                                               gaps=t["names_sep"].join(s.based_on_gaps), differs=s.differs_from_original,
                                               entry=s.entry_point)
            try:
                prop = await self._call("writer", "write_proposal", Proposal, idea_block=block, digests=digests, gaps=gaps_text)
            except LLMError as e:
                self.log(f"  {rec.proposal_id} 写 proposal 失败：{e}")
                return rec.model_copy(update={"error": str(e)})
            self.log(f"  {rec.proposal_id} 写好了：{prop.title}")
            return rec.model_copy(update={"proposal": prop, "error": ""})

        records = list(await asyncio.gather(*(one(r) for r in records)))
        self.store.save("08_proposals", records, list[ProposalRecord])
        return records

    async def step_meta_reviews(self, papers, fulltexts, reviews, proposals: list[ProposalRecord]) -> list[MetaReviewRecord]:
        saved = {(m.proposal_id, m.reviewer): m for m in (self.store.load("09_meta_reviews", list[MetaReviewRecord]) or [])}
        strong, weak = self._digests(papers, reviews)
        digests = "\n\n".join(strong + weak) or self.t["no_fulltext_notes"]
        by_id = {p.paper_id: p for p in papers}
        jobs = [(rec, name) for rec in proposals if rec.proposal for name in self.cfg.roles.reviewers
                if not (saved.get((rec.proposal_id, name)) and saved[(rec.proposal_id, name)].review)]
        if not jobs:
            return list(saved.values())
        self.log(f"第 9 步：换模型做元评审（{len(jobs)} 份）")

        def excerpts(prop: Proposal) -> str:
            ids = [c.paper_id for c in prop.closest_work if fulltexts.get(c.paper_id) and fulltexts[c.paper_id].status == "ok"]
            ids = list(dict.fromkeys(ids))[: self.cfg.review.reviewer_fulltext_papers]
            parts = []
            for pid in ids:
                text, _ = clip(self.store.file(fulltexts[pid].text_path).read_text(encoding="utf-8"), self.cfg.review.reviewer_fulltext_chars)
                parts.append(self.t["excerpt"].format(line=paper_line(by_id[pid], self.t), text=text))
            return "\n\n".join(parts) or self.t["no_excerpts"]

        async def one(rec: ProposalRecord, name: str) -> MetaReviewRecord:
            model = self.cfg.providers[name].model
            try:
                mr = await self._call(name, "meta_review", MetaReview, by_name=True,
                                      proposal=proposal_text(rec.proposal_id, rec.proposal, self.t), digests=digests,
                                      fulltexts=excerpts(rec.proposal))
            except LLMError as e:
                self.log(f"  {name} 评审 {rec.proposal_id} 失败：{e}")
                return MetaReviewRecord(proposal_id=rec.proposal_id, reviewer=name, model=model, error=str(e))
            self.log(f"  {name} 评审完 {rec.proposal_id}")
            return MetaReviewRecord(proposal_id=rec.proposal_id, reviewer=name, model=model, review=mr)

        for m in await asyncio.gather(*(one(r, n) for r, n in jobs)):
            saved[(m.proposal_id, m.reviewer)] = m
        records = list(saved.values())
        self.store.save("09_meta_reviews", records, list[MetaReviewRecord])
        return records

    async def step_chair(self, proposals: list[ProposalRecord], metas: list[MetaReviewRecord]) -> list[ChairRecord]:
        saved = {c.proposal_id: c for c in (self.store.load("10_chair", list[ChairRecord]) or [])}
        todo = []
        for rec in proposals:
            ok = [m for m in metas if m.proposal_id == rec.proposal_id and m.review]
            done = saved.get(rec.proposal_id)
            if rec.proposal and len(ok) >= 2 and not (done and done.summary):
                todo.append((rec, ok))
        if not todo:
            return list(saved.values())
        self.log(f"第 10 步：汇总评审意见（{len(todo)} 份）")

        async def one(rec, ok) -> ChairRecord:
            text = "\n\n".join(meta_review_text(m.reviewer, m.review, self.t) for m in ok)
            try:
                summary = await self._call("chair", "chair", ChairSummary, proposal_title=f"{rec.proposal_id} {rec.proposal.title}", reviews=text)
            except LLMError as e:
                self.log(f"  {rec.proposal_id} 汇总失败：{e}")
                return ChairRecord(proposal_id=rec.proposal_id, error=str(e))
            return ChairRecord(proposal_id=rec.proposal_id, summary=summary)

        for c in await asyncio.gather(*(one(r, ok) for r, ok in todo)):
            saved[c.proposal_id] = c
        records = list(saved.values())
        self.store.save("10_chair", records, list[ChairRecord])
        return records
