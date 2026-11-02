#!/usr/bin/env python3
"""把运行目录里各步的 JSON 渲染成一个自包含的 HTML 报告（样式和命令行版一样）。只用标准库。

  report.py RUN           写 RUN/report.html
  report.py RUN --site    另写一份 RUN/site/index.html，用来发布成网页（比如 WorkBuddy 的"发布为应用"）
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ir import SKILL_DIR, TX, Run, Stop, check, gate  # noqa: E402

_REF = re.compile(r"\b([PL]\d{2,3})\b")
_GAP = re.compile(r"\b(G\d{1,2})\b")
_PAGE = re.compile(r"\[(p\.\d+(?:[-–]\d+)?)\]")

T = {
    "zh": {
        "html_lang": "zh-CN", "lp": "（", "rp": "）", "gap": "　", "title": "Idea 评审报告", "your_idea": "你的原始 idea：",
        "stat_candidates": "篇候选文献", "stat_local": "，另有本地 {n} 篇", "stat_read": "篇读了全文",
        "stat_quality": "篇高质量 / 低质量", "stat_mismatch": "篇摘要夸大或和正文矛盾", "stat_gaps": "个文献空白",
        "stat_proposals": "份 proposal × 评审模型",
        "few": "只读到 {read} 篇全文，少于 {need} 篇", "all_low": "读到的 {read} 篇全文都是低质量",
        "stopped_title": "文献证据不够，停在了第 5 步",
        "stopped_body": "{why}。拿这么少或这么弱的文献去找空白、写 proposal，结果多半站不住，所以后面几步没有跑。可以这样补：",
        "stopped_tips": [
            "把下面\"很相关，但没读到全文\"里的论文下载下来，按编号放进 <code>{papers}</code>（比如 <code>P07.pdf</code>），再让 agent 接着跑。",
            "检索结果本身太少的话，换几条检索式开一次新的运行。",
            "确实想用现有的文献跑完，让 agent 运行 <code>ir.py next {run} --force</code>。",
        ],
        "forced": "<b>文献证据偏弱</b>：{why}。下面的空白和 proposal 只能当线索，先多补几篇文献再下判断。",
        "toc": ["① 检索", "② 文献", "③ 空白", "④ 方案和评审", "⑤ 你拍板", "附录"], "to_dark": "切到深色", "to_light": "切到浅色",
        "field": "领域", "concepts": "核心概念", "queries": "检索式",
        "search_note": "检索源：Semantic Scholar、arXiv、OpenAlex。先按标题和摘要初筛，再按相关度从高到低下载全文来精读，下载失败的往下顺延；"
                       "候选里能下载的用完了还不够数，会从检索结果里再多取一些候选。",
        "counts": "参考文献数量", "set": "设定", "actual": "实际", "per_query": "每条检索式、每个来源", "take": "取 {n} 篇",
        "cand_row": "候选文献（进初筛）", "at_most": "最多 {n} 篇", "n_papers": "{n} 篇", "refs_row": "精读全文",
        "local_row": "你提供的本地 PDF", "local_note": "另算，不占精读名额",
        "expanded": "全文不够，自动从检索结果里多取了 {n} 篇", "few_cand": "检索结果去重后只有这么多",
        "few_full": "高、中相关又能下载到全文的只有这么多", "manual": "含你手动放进 papers/ 的 {n} 篇",
        "not_done": "这一步还没完成。", "skipped": "文献证据不够，这一步没有跑，见页面顶部的说明。",
        "papers_lead": "\"质量\"是模型读完全文后的判断，只看论文本身的证据；\"已发表 / 预印本\"来自检索元数据。质量按论文类型分别判断："
                       "综述看覆盖和归纳，立场文章看论证，数据集和工具看构建和质量控制，不会因为没做实验就判低。"
                       "摘要夸大或和正文矛盾的论文，结论以正文为准；摘要没交代适用范围的，也在笔记里单独列出。",
        "th_papers": ["编号", "论文", "年份", "出处", "被引", "质量", "摘要和正文"],
        "overclaim": "夸大或矛盾", "scope_only": "没交代适用范围", "consistent": "一致",
        "notes": "逐篇精读笔记", "no_read": "还没有读到全文的论文。", "local": "本地", "pub": "已发表", "pre": "预印本",
        "pub_unknown": "发表情况不明", "year_unknown": "年份不明", "venue_unknown": "出处不明", "quality": "质量",
        "overclaim_long": "摘要夸大或和正文矛盾", "etal": " 等", "link": "原文链接", "local_pdf": "本地 PDF：",
        "does": "实际做了什么", "overclaims": "摘要夸大或和正文矛盾：", "scope": "摘要没交代的适用范围",
        "problem": "问题", "method": "方法", "setup": "实验设置", "results": "主要结果", "judgement": "质量判断：",
        "strength": "证据强度", "repro": "可复现", "rigor": "严谨性", "flags": "危险信号", "relevance": "和你的 idea 的关系：",
        "limits": "局限", "open": "还没解决的问题", "none": "无", "sep": "、", "semi": "；", "colon": "：",
        "missing_title": "很相关，但没读到全文",
        "missing_lead": "把 PDF 下载下来，按编号命名放进 <code>{papers}</code>（比如 <code>P07.pdf</code>），再让 agent 接着跑："
                        "它会抽出全文、补精读，再按新的文献重做后面几步。",
        "th_missing": ["编号", "论文", "原因"], "others": "其余 {n} 篇候选（只看了标题和摘要）",
        "th_others": ["编号", "论文", "年份", "初筛", "理由"],
        "landscape": "③ 版图和空白", "now": "现状", "clusters": "几类做法", "gaps": "空白", "admit": "论文作者自己承认",
        "inferred": "模型推断", "weak_gap": "没有高、中质量的论文支撑", "evidence": "证据", "why": "为什么重要", "risk": "撞车风险",
        "gaps_lead": "有没有人做过，这里不替你判断：只列出读过的论文里离它最近的几篇，和给你自己去查的检索式。",
        "closest_read": "读过的论文里最近的", "queries": "自己补查",
        "kinds": {"untested_assumption": "没检验过的假设", "contradiction": "结论互相矛盾", "limitation": "已知局限",
                  "missing_setting": "没覆盖的场景", "measurement": "评测方法的问题", "transfer": "方法迁移", "other": "其他"},
        "proposals_lead": "proposal 由 <b>{writer}</b> 写；元评审由 {reviewers} 在新对话里各自独立完成，评审只拿到 proposal 和论文材料，看不到写作过程。",
        "no_reviewers": "（还没有评审）", "original": "你的原始 idea", "seed": "从文献空白里挖出的新方向",
        "differs": "和原始 idea 的区别：", "based_on": "依据的空白：", "why_fail": "现有方法为什么不行", "closest": "最接近的工作",
        "overlap": "重合 ", "difference": "区别 ", "min_exp": "最小实验（{d}）", "data": "数据：", "baselines": "基线：",
        "metrics": "指标：", "kill_sign": "不成立的信号", "risks": "风险", "checking": "需要补查", "ask_author": "需要你自己回答",
        "meta_reviews": "元评审", "chair": "综合元评审", "chair_by": "由 {chair} 综合 {n} 位评审的意见（{legend}），不下结论",
        "chair_legacy": "评审意见汇总", "chair_legacy_by": "（由 {writer} 整理，不下结论）",
        "chair_same": "和写 proposal 的是同一家模型", "key_issues": "要紧的问题", "all_raised": "{k} 家都提到",
        "ac": "综合时补充", "overlap_check": "撞车论文核对", "claim": "评审说：", "checked": "核对：",
        "verdicts": {"confirmed": ("材料证实撞车", "b-warn"), "partial": ("部分重合", "b-medium"),
                     "not_supported": ("材料不支持这个说法", "b-high"), "unverifiable": ("核对不了", "b-unk")},
        "settle": "怎么判断谁对：", "first_exp": "最该先做的实验", "drawn_from": "取自：", "days": "用时：",
        "chair_questions": "投入之前要回答的问题",
        "consensus": "几家都提到的问题", "disagree": "有分歧的地方", "verify": "你必须亲自核实",
        "tally": "评审列出的撞车论文", "tally_by": "程序按 {n} 家评审的意见统计", "tally_row": "{n} 家里 {k} 家",
        "tally_external": "附上的论文以外，需核实", "tally_single": "只有一家这么说，不稳",
        "tally_note": "几家都列出的，也要自己点开正文核实：评审看的是同一批材料，可能一起看错。",
        "tally_one": "这次只有一家评审，没法互相对照。", "tally_none": "评审都没在附上的论文里找到撞车的。这不等于没人做过。",
        "same_family": "和写 proposal 的是同一家模型，参考价值打折",
        "r_overlap": "撞车", "unread": "没读全文，需核实", "actually": "实际做了：", "r_overlap2": "重合：", "r_diff": "区别：",
        "r_mismatch": "摘要夸大或和正文矛盾", "deaths": "最可能的死因", "from_paper": "论文", "from_inference": "推断",
        "feasibility": "可行性", "bottleneck": "卡点：", "workaround": "绕法：", "falsify": "最便宜的证伪实验",
        "compare": "对比：", "metric": "指标：", "give_up": "放弃：", "go_on": "继续：", "before_invest": "投入之前必须回答",
        "review_failed": "评审失败：", "no_review": "还没有评审结果。", "not_written": "这份 proposal 没写出来：",
        "decide": "⑤ 你拍板", "decide_lead": "报告只帮你把材料摆齐。下面四个问题答得上来，再决定要不要投入几个月。",
        "checklist": ["评审说撞车的那几篇，我自己点开读过正文了吗？还撞吗？", "几家都提到的死因是什么？我有办法解决吗？",
                      "证伪实验开始之前，\"出现什么结果就放弃\"我写下来了吗？", "三天内跑完了吗？结果是什么？"],
        "decide_note": "跑实验的时候自己盯着看：下一个好问题，往往就藏在那些看不懂的现象里。",
        "appendix": "附录", "who": "各环节由谁完成", "th_who": ["环节", "模型"],
        "who_rows": ["拆检索词、初筛、精读、找空白、写 proposal", "元评审", "综合元评审"],
        "usage": "评审用量", "th_usage": ["评审", "调用次数", "输入 token", "输出 token"],
        "run_dir": "运行目录：<code>{run}</code>。每一步的原始结果都是 JSON，放在这个目录里。",
        "footer": "这份报告只是帮你发现盲点。AI 给出的任何论文和判断，都要自己点开核实；拍板的是你。",
        "agent": "agent（{family}）",
    },
    "en": {
        "html_lang": "en", "lp": " (", "rp": ")", "gap": " &nbsp; ", "title": "Idea Review Report", "your_idea": "Your original idea: ",
        "stat_candidates": "candidate papers", "stat_local": ", plus {n} local", "stat_read": "read in full",
        "stat_quality": "high / low quality", "stat_mismatch": "abstracts overclaim or contradict the body", "stat_gaps": "gaps in the literature",
        "stat_proposals": "proposals × reviewer models",
        "few": "Only {read} full texts were read, fewer than {need}", "all_low": "All {read} full texts read are low quality",
        "stopped_title": "Not enough evidence; stopped at step 5",
        "stopped_body": "{why}. Gaps and proposals built on so few or such weak papers would hardly hold up, so the later steps were not run. To add more:",
        "stopped_tips": [
            "Download the papers under \"Relevant, but full text not read\" below, name them by ID and put them into <code>{papers}</code> (e.g. <code>P07.pdf</code>), then ask the agent to continue.",
            "If the search returned too few papers, start a new run with different queries.",
            "If you really want to finish with the papers you have, ask the agent to run <code>ir.py next {run} --force</code>.",
        ],
        "forced": "<b>Weak evidence</b>: {why}. Treat the gaps and proposals below as leads only, and add more papers before drawing conclusions.",
        "toc": ["① Search", "② Papers", "③ Gaps", "④ Proposals & reviews", "⑤ Your call", "Appendix"], "to_dark": "Dark mode", "to_light": "Light mode",
        "field": "Field", "concepts": "Core concepts", "queries": "Queries",
        "search_note": "Sources: Semantic Scholar, arXiv, OpenAlex. Papers are screened by title and abstract first, then full texts are downloaded "
                       "in order of relevance; failed downloads are replaced by the next paper. When the downloadable candidates run out, more "
                       "candidates are taken from the search results.",
        "counts": "Number of references", "set": "Set", "actual": "Actual", "per_query": "Per query and source", "take": "{n} papers",
        "cand_row": "Candidates (screened)", "at_most": "up to {n}", "n_papers": "{n}", "refs_row": "Full texts read",
        "local_row": "Local PDFs you provided", "local_note": "counted separately",
        "expanded": "{n} more were taken from the search results automatically because full texts were short",
        "few_cand": "that is all the search returned after deduplication",
        "few_full": "that is all the high- and medium-relevance papers with downloadable full texts",
        "manual": "includes {n} you put into papers/",
        "not_done": "This step has not been done yet.", "skipped": "Not run because the evidence was not enough; see the note at the top.",
        "papers_lead": "\"Quality\" is the model's judgment after reading the full text and looks only at the paper's own evidence; "
                       "\"peer-reviewed / preprint\" comes from search metadata. Quality is judged by paper type: surveys by coverage and synthesis, "
                       "position papers by argument, datasets and tools by construction and quality control, so a paper is not rated low just "
                       "for having no experiments. Where an abstract overclaims or contradicts the body, go by the body; scope the abstract leaves "
                       "out is listed separately in the notes.",
        "th_papers": ["ID", "Paper", "Year", "Venue", "Cited", "Quality", "Abstract vs. body"],
        "overclaim": "Overclaims", "scope_only": "Scope left out", "consistent": "Consistent",
        "notes": "Reading notes", "no_read": "No full texts read yet.", "local": "Local", "pub": "Peer-reviewed", "pre": "Preprint",
        "pub_unknown": "Status unknown", "year_unknown": "year unknown", "venue_unknown": "venue unknown", "quality": "Quality",
        "overclaim_long": "Abstract overclaims or contradicts the body", "etal": " et al.", "link": "Link", "local_pdf": "Local PDF: ",
        "does": "What it actually does", "overclaims": "Abstract overclaims or contradicts the body:", "scope": "Scope the abstract leaves out",
        "problem": "Problem", "method": "Method", "setup": "Setup", "results": "Key results", "judgement": "Quality: ",
        "strength": "Evidence", "repro": "Reproducibility", "rigor": "Rigor", "flags": "Red flags", "relevance": "Relation to your idea: ",
        "limits": "Limitations", "open": "Open questions", "none": "None", "sep": ", ", "semi": "; ", "colon": ": ",
        "missing_title": "Relevant, but full text not read",
        "missing_lead": "Download the PDFs, name them by ID and put them into <code>{papers}</code> (e.g. <code>P07.pdf</code>), then ask the agent "
                        "to continue: it extracts the text, reads the new papers and redoes the later steps.",
        "th_missing": ["ID", "Paper", "Reason"], "others": "The other {n} candidates (title and abstract only)",
        "th_others": ["ID", "Paper", "Year", "Screening", "Reason"],
        "landscape": "③ Landscape & gaps", "now": "Where it stands", "clusters": "Groups of work", "gaps": "Gaps",
        "admit": "admitted by the authors", "inferred": "inferred by the model", "weak_gap": "Not backed by any high- or medium-quality paper",
        "evidence": "Evidence", "why": "Why it matters", "risk": "Already-done risk",
        "gaps_lead": "Whether someone has already done it is not judged for you here. Each gap lists the papers read that come closest to it and search queries to check for yourself.",
        "closest_read": "Closest papers read", "queries": "Check yourself",
        "kinds": {"untested_assumption": "Untested assumption", "contradiction": "Contradictory conclusions", "limitation": "Known limitation",
                  "missing_setting": "Uncovered setting", "measurement": "Flawed evaluation", "transfer": "Method transfer", "other": "Other"},
        "proposals_lead": "Proposals were written by <b>{writer}</b>; meta reviews were done independently in fresh sessions by {reviewers}, "
                          "who saw only the proposal and the paper material, not how it was written.",
        "no_reviewers": "(no reviewers yet)", "original": "Your original idea", "seed": "New direction mined from the gaps",
        "differs": "How it differs from the original idea: ", "based_on": "Based on gaps: ", "why_fail": "Why existing methods fall short",
        "closest": "Closest work", "overlap": "overlap ", "difference": "difference ", "min_exp": "Minimal experiment ({d})",
        "data": "Data: ", "baselines": "baselines: ", "metrics": "metrics: ", "kill_sign": "Sign it fails", "risks": "Risks",
        "checking": "Needs checking", "ask_author": "For you to answer", "meta_reviews": "Meta reviews", "chair": "Combined meta review",
        "chair_by": "{chair} combined the opinions of {n} reviewers ({legend}); no verdict",
        "chair_legacy": "Summary of the reviews", "chair_legacy_by": " (compiled by {writer}, no verdict)",
        "chair_same": "Same company as the proposal writer", "key_issues": "Key issues", "all_raised": "raised by all {k}",
        "ac": "added by the chair", "overlap_check": "Overlap claims checked", "claim": "Reviewers say: ", "checked": "Check: ",
        "verdicts": {"confirmed": ("Overlap confirmed by the material", "b-warn"), "partial": ("Partial overlap", "b-medium"),
                     "not_supported": ("Not supported by the material", "b-high"), "unverifiable": ("Cannot be checked", "b-unk")},
        "settle": "How to tell who is right: ", "first_exp": "Experiment to run first", "drawn_from": "Taken from: ", "days": "Duration: ",
        "chair_questions": "Questions to answer before investing",
        "consensus": "Raised by several reviewers", "disagree": "Disagreements",
        "verify": "You must check yourself", "same_family": "Same company as the proposal writer; discount accordingly",
        "tally": "Overlapping papers named by the reviewers", "tally_by": "counted by the program across {n} reviewer{s}",
        "tally_row": "{k} of {n}", "tally_external": "Not among the attached papers; check it",
        "tally_single": "Only one reviewer says so; unstable",
        "tally_note": "Open and check these yourself even when several reviewers agree: they all saw the same material and can be wrong together.",
        "tally_one": " There is only one reviewer this time, so there is nothing to compare against.",
        "tally_none": "No reviewer found an overlapping paper among the attached ones. That does not mean nobody has done it.",
        "r_overlap": "Overlap", "unread": "Full text not read; check it", "actually": "Actually does: ", "r_overlap2": "Overlap: ",
        "r_diff": "Difference: ", "r_mismatch": "Abstract overclaims or contradicts the body", "deaths": "Most likely causes of death",
        "from_paper": "paper", "from_inference": "inference", "feasibility": "Feasibility", "bottleneck": "Bottleneck: ",
        "workaround": "Workaround: ", "falsify": "Cheapest falsification", "compare": "Compare against: ", "metric": "Metric: ",
        "give_up": "Give up if: ", "go_on": "Continue if: ", "before_invest": "Answer before investing", "review_failed": "Review failed: ",
        "no_review": "No reviews yet.", "not_written": "This proposal was not written: ",
        "decide": "⑤ Your call", "decide_lead": "The report only lays out the material. Answer these four questions before spending months on it.",
        "checklist": ["Have I opened and read the papers the reviewers say overlap? Do they still overlap?",
                      "What cause of death did several reviewers name? Can I solve it?",
                      "Before starting the falsification experiment, did I write down which result means giving up?",
                      "Did it finish within three days? What was the result?"],
        "decide_note": "Watch the experiments yourself: the next good question often hides in what you cannot explain.",
        "appendix": "Appendix", "who": "Who did each step", "th_who": ["Step", "Model"],
        "who_rows": ["Queries, screening, reading, gaps, proposals", "Meta review", "Combined meta review"],
        "usage": "Reviewer usage", "th_usage": ["Reviewer", "Calls", "Input tokens", "Output tokens"],
        "run_dir": "Run directory: <code>{run}</code>. The raw result of every step is a JSON file in this directory.",
        "footer": "This report only helps you find blind spots. Open and check every paper and judgment the AI gives you; the call is yours.",
        "agent": "agent ({family})",
    },
}
LEVEL = {"zh": {"high": "高", "medium": "中", "low": "低"}, "en": {"high": "High", "medium": "Medium", "low": "Low"}}


def esc(text) -> str:
    return html.escape("" if text is None else str(text))


def refs(text) -> str:
    """转义后把论文编号、空白编号变成页内链接，把 [p.6] 变成小标签。"""
    s = esc(text)
    s = _REF.sub(r'<a class="ref" href="#paper-\1">\1</a>', s)
    s = _GAP.sub(r'<a class="ref gap" href="#gap-\1">\1</a>', s)
    return _PAGE.sub(r'<span class="page">\1</span>', s)


def chair_names(names: list[str], codes: dict[str, str], t: dict) -> list[str]:
    """综合元评审里写的是评审代号，换回评审名字。AC 是综合的模型自己补的。"""
    out = []
    for name in names:
        m = re.fullmatch(r"\s*(?:评审|Reviewer)?\s*([A-Z]{1,2})\s*", name)
        code = m.group(1) if m else name
        out.append(t["ac"] if code == "AC" else codes.get(code, name))
    return out


def chair_detail(c: dict) -> str:
    """综合元评审用的型号和思考强度，比如 opus · xhigh。"""
    return " · ".join(c[k] for k in ("model", "effort") if c.get(k))


def overlap_tally(metas: list[dict], by_id: dict[str, dict]) -> tuple[int, list[dict]]:
    """数几家评审各自把哪些论文列为撞车。由程序统计，不靠模型归纳：只有一家这么说的，报告里标成不稳。"""
    done = [m for m in metas if m.get("review")]
    rows: dict[str, dict] = {}
    for m in done:
        for o in m["review"]["overlap"]:
            known = o["paper_id"] in by_id
            # 附上的论文以外的工作没有编号，按标题归并
            key = o["paper_id"] if known else "title:" + re.sub(r"\W+", "", o["title"].lower())
            row = rows.setdefault(key, {"paper_id": o["paper_id"] if known else "",
                                        "title": by_id[o["paper_id"]]["title"] if known else o["title"], "by": []})
            if m["reviewer"] not in row["by"]:
                row["by"].append(m["reviewer"])
    return len(done), sorted(rows.values(), key=lambda r: (-len(r["by"]), not r["paper_id"], r["paper_id"], r["title"]))


def build(run: Run, site: bool = False) -> Path:
    """site=True 时写发布用的副本：不出现本机路径（运行目录只写名字，不列本地 PDF），并要求搜索引擎不收录。"""
    meta = run.meta()
    lang = meta.get("lang", "zh")
    t, lv, kinds_t = T[lang], LEVEL[lang], TX[lang]["paper_types"]
    plan = run.load("01_plan")
    papers = run.load("02_candidates", [])
    screen = {s["paper_id"]: s for s in run.load("03_screen", [])}
    fulltexts = run.load("04_fulltext", {})
    reviews = {r["paper_id"]: r for r in run.load("05_reviews", [])}
    # next 没来得及合并的精读笔记，格式对的也算上
    for path in sorted((run.dir / "notes").glob("*.json")):
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if path.stem not in reviews and not check(note, "PaperReview"):
            reviews[path.stem] = {"paper_id": path.stem, "review": note, "error": "", "truncated": False}
    landscape = run.load("06_landscape")
    proposals = run.load("08_proposals", [])
    metas = run.load("09_meta_reviews", [])
    chairs = {c["proposal_id"]: c for c in run.load("10_chair", [])}
    usage = run.load("usage", {})
    writer = meta.get("writer", {})
    writer_name = writer.get("model") or t["agent"].format(family=writer.get("family", "?"))

    def level(v):
        return f'<span class="badge b-{esc(v)}">{esc(lv.get(v, v))}</span>'

    def pubbadge(p):
        if p.get("local_path"):
            return f'<span class="badge b-unk">{t["local"]}</span>'
        return {True: f'<span class="badge b-pub">{t["pub"]}</span>', False: f'<span class="badge b-pre">{t["pre"]}</span>'}.get(
            p.get("peer_reviewed"), f'<span class="badge b-unk">{t["pub_unknown"]}</span>')

    def plist(items):
        if not items:
            return f'<span class="meta">{t["none"]}</span>'
        return '<ul class="tight">' + "".join(f"<li>{refs(i)}</li>" for i in items) + "</ul>"

    def chair_html(c: dict) -> str:
        s, codes = c["summary"], c.get("reviewers") or {}
        legend = t["sep"].join(f"{k} = {v}" for k, v in codes.items())
        same = c.get("family") and c.get("family") == writer.get("family")
        out = (f'<div class="chair"><b>{t["chair"]}</b> <span class="meta">'
               + t["chair_by"].format(chair=esc(c.get("chair", "")) + (f'{t["lp"]}{esc(chair_detail(c))}{t["rp"]}' if chair_detail(c) else ""),
                                      n=len(codes), legend=esc(legend)) + "</span>"
               + (f' <span class="badge b-warn">{t["chair_same"]}</span>' if same else "") + f'<p>{refs(s["summary"])}</p>')
        if s["key_issues"]:
            items = ""
            for i in s["key_issues"]:
                names = chair_names(i["raised_by"], codes, t)
                k = len(set(names) & set(codes.values()))
                items += (f'<li>{refs(i["issue"])}' + (f' <span class="badge b-warn">{t["all_raised"].format(k=k)}</span>' if len(codes) > 1 and k == len(codes) else "")
                          + f' <span class="meta">{t["lp"]}{esc(t["sep"].join(names))}{t["semi"]}'
                          f'{t["from_paper"] if i["source"] == "paper" else t["from_inference"]}{t["colon"]}{refs(i["basis"])}{t["rp"]}</span></li>')
            out += f'<h4>{t["key_issues"]}</h4><ol class="tight">{items}</ol>'
        if s["overlap_check"]:
            items = ""
            for o in s["overlap_check"]:
                label, cls = t["verdicts"].get(o["verdict"], (o["verdict"], "b-unk"))
                items += (f'<li>{refs(o["paper_id"]) + " " if o["paper_id"] != "external" else ""}{esc(o["title"])} <span class="badge {cls}">{esc(label)}</span>'
                          f' <span class="meta">{t["lp"]}{esc(t["sep"].join(chair_names(o["raised_by"], codes, t)))}{t["rp"]}</span><br>'
                          f'{t["claim"]}{refs(o["claim"])}<br><span class="meta">{t["checked"]}{refs(o["evidence"])}</span></li>')
            out += f'<h4>{t["overlap_check"]}</h4><ul class="tight">{items}</ul>'
        if s["disagreements"]:
            out += f'<h4>{t["disagree"]}</h4><ul class="tight">' + "".join(
                f'<li><b>{esc(d["topic"])}</b>{t["colon"]}{refs(t["semi"].join(d["positions"]))}<br>'
                f'<span class="meta">{t["settle"]}{refs(d["how_to_settle"])}</span></li>' for d in s["disagreements"]) + "</ul>"
        f = s["first_experiment"]
        out += (f'<h4>{t["first_exp"]}</h4><p style="margin:4px 0">{refs(f["experiment"])}<br>'
                f'<span class="meta">{t["compare"]}{esc(f["compare_against"])}{t["semi"]}{t["metric"]}{esc(f["metric"])}{t["semi"]}'
                f'{t["days"]}{esc(f["est_days"])}{t["semi"]}{t["drawn_from"]}{esc(t["sep"].join(chair_names(f["drawn_from"], codes, t)))}</span><br>'
                f'<span class="kill">{t["give_up"]}{refs(f["kill_if"])}</span><br><span class="go">{t["go_on"]}{refs(f["continue_if"])}</span></p>')
        if s["questions_for_author"]:
            out += f'<h4>{t["chair_questions"]}</h4>{plist(s["questions_for_author"])}'
        if s["must_verify"]:
            out += f'<h4>{t["verify"]}</h4>{plist(s["must_verify"])}'
        return out + "</div>\n"

    def legacy_chair_html(s: dict) -> str:
        """旧版本由写 proposal 的 agent 写的汇总。"""
        return (f'<div class="chair"><b>{t["chair_legacy"]}</b>{t["chair_legacy_by"].format(writer=esc(writer_name))}<p>{refs(s["summary"])}</p>'
                + (f'<h4>{t["consensus"]}</h4><ul class="tight">' + "".join(
                    f'<li>{refs(c["issue"])} <span class="meta">{t["lp"]}{esc(t["sep"].join(c["raised_by"]))}{t["rp"]}</span></li>' for c in s["consensus"]) + "</ul>"
                   if s.get("consensus") else "")
                + (f'<h4>{t["disagree"]}</h4><ul class="tight">' + "".join(
                    f'<li><b>{esc(d["topic"])}</b>{t["colon"]}{refs(t["semi"].join(d["positions"]))}</li>' for d in s["disagreements"]) + "</ul>"
                   if s.get("disagreements") else "")
                + (f'<h4>{t["verify"]}</h4>{plist(s["must_verify"])}' if s.get("must_verify") else "") + "</div>\n")

    def usage_name(name: str) -> str:
        return f'{esc(name[6:])}{t["lp"]}{t["chair"]}{t["rp"]}' if name.startswith("chair:") else esc(name)

    def kind_label(q):
        return kinds_t.get(q.get("paper_type", "other"), "") if q.get("paper_type", "other") != "other" else ""

    def title_link(p):
        return f'<a href="{esc(p["url"])}" target="_blank" rel="noopener">{esc(p["title"])}</a>' if p.get("url") else esc(p["title"])

    by_id = {p["paper_id"]: p for p in papers}
    order = {"high": 0, "medium": 1, "low": 2}
    read = sorted(((by_id[pid], r) for pid, r in reviews.items() if r.get("review") and pid in by_id),
                  key=lambda x: (order[x[1]["review"]["quality"]["tier"]], x[0]["paper_id"]))
    failed_reads = [(by_id[pid], r) for pid, r in reviews.items() if not r.get("review") and pid in by_id]
    missing = [(by_id[pid], f) for pid, f in fulltexts.items() if f["status"] != "ok" and pid in by_id]
    shown = {p["paper_id"] for p, _ in read + failed_reads + missing}
    others = sorted((p for p in papers if p["paper_id"] not in shown),
                    key=lambda p: order.get((screen.get(p["paper_id"]) or {}).get("relevance", "low"), 3))

    counts = meta["search"]
    n_cand = sum(1 for p in papers if not p.get("local_path"))
    n_full = sum(1 for p in papers if not p.get("local_path") and fulltexts.get(p["paper_id"], {}).get("status") == "ok")
    n_local = sum(1 for p in papers if p.get("local_path"))
    tiers = [r["review"]["quality"]["tier"] for _, r in read]
    n_mismatch = sum(1 for _, r in read if r["review"]["abstract_vs_body"]["overclaims"])
    n_props = sum(1 for r in proposals if r.get("proposal"))
    reviewer_rows = list({(m["reviewer"], m.get("model") or "", m.get("family") or ""): None for m in metas})
    chair_labels = t["sep"].join(dict.fromkeys(f"<b>{esc(c['chair'])}</b>" + (f' <span class="meta">{esc(chair_detail(c))}</span>' if chair_detail(c) else "")
                                               for c in chairs.values() if c.get("summary") and c.get("chair")))
    gaps = (landscape or {}).get("gaps", [])

    shortfall = gate(list(reviews.values()), meta)
    stopped = bool(shortfall and not landscape)
    strong_ids = {p["paper_id"] for p, r in read if r["review"]["quality"]["tier"] in ("high", "medium")}
    weak_gaps = {i for i, g in enumerate(gaps, start=1) if not any(e["paper_id"] in strong_ids for e in g["evidence"])}
    papers_dir, run_dir = (esc("papers/"), esc(run.dir.name)) if site else (esc(f"{run.dir}/papers/"), esc(str(run.dir)))

    out: list[str] = []
    w = out.append
    css, js = (re.sub(r"/\*.*?\*/\s*", "", (SKILL_DIR / "assets" / name).read_text(encoding="utf-8"), flags=re.S)
               for name in ("report.css", "report.js"))
    w(f'<!doctype html>\n<html lang="{t["html_lang"]}">\n<head>\n<meta charset="utf-8">\n'
      f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
      + ('<meta name="robots" content="noindex, nofollow">\n' if site else "") + f'<title>{t["title"]}</title>\n<style>\n{css}\n</style>\n<script>\n{js}</script>\n</head>\n<body>\n')

    # ---------- 页头 ----------
    w(f'<header class="hero"><div class="wrap">\n<div class="eyebrow">{t["title"]} · {esc(meta.get("created", "").replace("T", " "))}</div>\n')
    w(f'<h1>{esc(plan["restated_idea"] if plan else meta["idea"])}</h1>\n<p class="idea">{t["your_idea"]}{esc(meta["idea"])}</p>\n<div class="stats">\n')
    stats = [
        (n_cand, t["stat_candidates"] + (t["stat_local"].format(n=n_local) if n_local else "")),
        (len(read), t["stat_read"]),
        (f"{tiers.count('high')} / {tiers.count('low')}", t["stat_quality"]),
        (n_mismatch, t["stat_mismatch"]),
        (len(gaps), t["stat_gaps"]),
        (f"{n_props} × {len({r[0] for r in reviewer_rows})}", t["stat_proposals"]),
    ]
    for num, label in stats:
        w(f'<div class="stat"><b class="num">{num}</b><span>{label}</span></div>\n')
    w("</div>\n")
    if shortfall:
        why = (t["few"] if shortfall["reason"] == "few" else t["all_low"]).format(**shortfall)
        if stopped:
            tips = "".join(f"<li>{tip.format(papers=papers_dir, run=run_dir)}</li>" for tip in t["stopped_tips"])
            w(f'<div class="warn"><b>{t["stopped_title"]}</b><p style="margin:6px 0">{t["stopped_body"].format(why=why)}</p>'
              f'<ul class="tight">{tips}</ul></div>\n')
        else:
            w(f'<div class="warn">{t["forced"].format(why=why)}</div>\n')
    w("</div></header>\n")
    anchors = ["search", "papers", "gaps", "proposals", "decide", "appendix"]
    w('<nav class="toc"><div class="wrap"><div class="links">' + "".join(f'<a href="#{a}">{n}</a>' for a, n in zip(anchors, t["toc"]))
      + f'</div><button type="button" id="theme-toggle" class="theme" data-to-dark="{t["to_dark"]}" data-to-light="{t["to_light"]}" hidden></button>'
      + "</div></nav>\n")
    w('<main class="wrap">\n')

    # ---------- ① 检索 ----------
    w(f'<section id="search"><h2>{t["toc"][0]}</h2>\n')
    if plan:
        notes_c, notes_f = [], []
        if meta.get("expanded"):
            notes_c.append(t["expanded"].format(n=meta["expanded"]))
        if n_cand < counts["max_candidates"]:
            notes_c.append(t["few_cand"])
        manual = sum(1 for f in fulltexts.values() if f["status"] == "ok" and f.get("manual"))
        if manual:
            notes_f.append(t["manual"].format(n=manual))
        if n_full < counts["download"]:
            notes_f.append(t["few_full"])
        chips = "".join(f'<span class="chip">{esc(c)}</span>' for c in plan["core_concepts"])
        queries = "".join(f"<li><code>{esc(q)}</code></li>" for q in plan["search_queries"])
        w(f'<div class="card"><div class="kv"><b>{t["field"]}</b>{esc(plan["field"])}</div>'
          f'<div class="kv"><b>{t["concepts"]}</b></div><div class="chips">{chips}</div>'
          f'<div class="kv"><b>{t["queries"]}</b></div><ul class="tight">{queries}</ul>'
          f'<div class="meta">{t["search_note"]}</div>\n<h4>{t["counts"]}</h4><table>'
          f'<tr><th></th><th>{t["set"]}</th><th>{t["actual"]}</th></tr>'
          f'<tr><td>{t["per_query"]}</td><td>{t["take"].format(n=counts["per_query"])}</td><td class="meta">—</td></tr>'
          f'<tr><td>{t["cand_row"]}</td><td>{t["at_most"].format(n=counts["max_candidates"])}</td><td>{t["n_papers"].format(n=n_cand)}'
          + (f' <span class="meta">{esc(t["semi"].join(notes_c))}</span>' if notes_c else "") + "</td></tr>"
          f'<tr><td>{t["refs_row"]}</td><td>{t["n_papers"].format(n=counts["download"])}</td><td>{t["n_papers"].format(n=n_full)}'
          + (f' <span class="meta">{esc(t["semi"].join(notes_f))}</span>' if notes_f else "") + "</td></tr>"
          + (f'<tr><td>{t["local_row"]}</td><td class="meta">—</td><td>{t["n_papers"].format(n=n_local)} <span class="meta">{t["local_note"]}</span></td></tr>'
             if n_local else "") + "</table></div>\n")
    else:
        w(f'<p class="meta">{t["not_done"]}</p>\n')
    w("</section>\n")

    # ---------- ② 文献 ----------
    w(f'<section id="papers"><h2>{t["toc"][1]}</h2>\n<p class="lead">{t["papers_lead"]}</p>\n')
    if read:
        w('<div class="table-wrap"><table><thead><tr>' + "".join(
            f'<th{" class=num" if i == 4 else ""}>{h}</th>' for i, h in enumerate(t["th_papers"])) + "</tr></thead><tbody>\n")
        for p, rec in read:
            r, q = rec["review"], rec["review"]["quality"]
            avb = r["abstract_vs_body"]
            check_cell = (f'<span class="badge b-warn">{t["overclaim"]}</span>' if avb["overclaims"] else
                          f'<span class="meta">{t["scope_only"]}</span>' if avb["scope_gaps"] else f'<span class="meta">{t["consistent"]}</span>')
            kind = kind_label(q)
            w(f'<tr><td><a class="ref" href="#paper-{esc(p["paper_id"])}">{esc(p["paper_id"])}</a></td><td>{esc(p["title"])}</td>'
              f'<td class="num">{esc(p.get("year") or "—")}</td><td>{pubbadge(p)} <span class="meta">{esc(p.get("venue") or "")}</span></td>'
              f'<td class="num">{esc(p["citation_count"]) if p.get("citation_count") is not None else "—"}</td>'
              f'<td>{level(q["tier"])}{f" <span class=meta>{esc(kind)}</span>" if kind else ""}</td><td>{check_cell}</td></tr>\n')
        w("</tbody></table></div>\n")
        w(f'<h3>{t["notes"]}</h3>\n')
        for p, rec in read:
            r, q = rec["review"], rec["review"]["quality"]
            avb, kind, pid = r["abstract_vs_body"], kind_label(q), esc(p["paper_id"])
            f = fulltexts.get(p["paper_id"]) or {}
            pdf = "" if site else str(run.file(f["pdf_path"])) if f.get("pdf_path") else (p.get("local_path") or "")
            authors = t["sep"].join(p.get("authors", [])[:6]) + (t["etal"] if len(p.get("authors", [])) > 6 else "")
            meta_line = " · ".join(x for x in [esc(authors) if authors else "",
                                               f'<a href="{esc(p["url"])}" target="_blank" rel="noopener">{t["link"]}</a>' if p.get("url") else "",
                                               f'{t["local_pdf"]}<code>{esc(pdf)}</code>' if pdf else ""] if x)
            w(f'<details class="paper" id="paper-{pid}"><summary><span class="ref">{pid}</span> <span class="title">{esc(p["title"])}</span><br>'
              f'<span class="meta">{esc(p.get("year") or t["year_unknown"])} · {esc(p.get("venue") or t["venue_unknown"])} · </span>{pubbadge(p)} '
              f'{t["quality"]} {level(q["tier"])}{f" <span class=meta>· {esc(kind)}</span>" if kind else ""}'
              + (f' <span class="badge b-warn">{t["overclaim_long"]}</span>' if avb["overclaims"] else "") + "</summary>\n"
              f'<div class="body"><p class="meta">{meta_line}</p>'
              f'<div class="kv"><b>{t["does"]}</b>{refs(r["what_it_actually_does"])}</div>'
              + (f'<div class="mismatch"><b>{t["overclaims"]}</b>{plist(avb["overclaims"])}</div>' if avb["overclaims"] else "")
              + (f'<div class="kv"><b>{t["scope"]}</b>{plist(avb["scope_gaps"])}</div>' if avb["scope_gaps"] else "")
              + f'<div class="grid2"><div><div class="kv"><b>{t["problem"]}</b>{refs(r["problem"])}</div>'
              f'<div class="kv"><b>{t["method"]}</b>{refs(r["method"])}</div><div class="kv"><b>{t["setup"]}</b>{refs(r["setup"])}</div>'
              f'<h4>{t["results"]}</h4>{plist(r["key_results"])}</div>'
              f'<div><h4>{t["judgement"]}{level(q["tier"])}{f"{t['lp']}{esc(kind)}{t['rp']}" if kind else ""}</h4>'
              f'<div class="meta">{refs(q["tier_reason"])}</div>'
              f'<div class="kv"><b>{t["strength"]}</b>{level(q["evidence_strength"])}{t["gap"]}<b>{t["repro"]}</b>{level(q["reproducibility"])}</div>'
              f'<h4>{t["rigor"]}</h4>{plist(q["rigor"])}' + (f'<h4>{t["flags"]}</h4>{plist(q["red_flags"])}' if q["red_flags"] else "")
              + f'</div></div><div class="callout"><b>{t["relevance"]}</b>{refs(r["relevance_to_idea"])}</div>'
              f'<div class="grid2"><div><h4>{t["limits"]}</h4>{plist(r["limitations"])}</div>'
              f'<div><h4>{t["open"]}</h4>{plist(r["open_questions"])}</div></div></div></details>\n')
    else:
        w(f'<p class="meta">{t["no_read"]}</p>\n')
    if missing or failed_reads:
        w(f'<h3>{t["missing_title"]}</h3><p class="lead">{t["missing_lead"].format(papers=papers_dir)}</p>'
          '<div class="table-wrap"><table><thead><tr>' + "".join(f"<th>{h}</th>" for h in t["th_missing"]) + "</tr></thead><tbody>\n")
        for p, f in missing:
            w(f'<tr id="paper-{esc(p["paper_id"])}"><td><span class="ref">{esc(p["paper_id"])}</span></td><td>{title_link(p)}<br>'
              f'<span class="meta">{esc(p.get("year") or "")} {esc(p.get("venue") or "")}</span></td><td class="meta">{esc(f.get("note"))}</td></tr>\n')
        for p, r in failed_reads:
            w(f'<tr id="paper-{esc(p["paper_id"])}"><td><span class="ref">{esc(p["paper_id"])}</span></td><td>{esc(p["title"])}</td>'
              f'<td class="error">{esc(r.get("error"))}</td></tr>\n')
        w("</tbody></table></div>\n")
    if others:
        w(f'<details class="paper" style="margin-top:16px"><summary><span class="title">{t["others"].format(n=len(others))}</span></summary>'
          '<div class="body"><div class="table-wrap" style="border:0"><table><thead><tr>' + "".join(f"<th>{h}</th>" for h in t["th_others"])
          + "</tr></thead><tbody>\n")
        for p in others:
            s = screen.get(p["paper_id"]) or {}
            w(f'<tr id="paper-{esc(p["paper_id"])}"><td><span class="ref">{esc(p["paper_id"])}</span></td><td>{title_link(p)}</td>'
              f'<td class="num">{esc(p.get("year") or "—")}</td><td>{level(s["relevance"]) if s else ""}</td>'
              f'<td class="meta">{esc(s.get("reason", ""))}</td></tr>\n')
        w("</tbody></table></div></div></details>\n")
    w("</section>\n")

    # ---------- ③ 空白 ----------
    w(f'<section id="gaps"><h2>{t["landscape"]}</h2>\n')
    if landscape:
        w(f'<div class="card"><div class="kv"><b>{t["now"]}</b>{refs(landscape["summary"])}</div></div>\n')
        if landscape["clusters"]:
            w(f'<h3>{t["clusters"]}</h3><div class="grid2">')
            for c in landscape["clusters"]:
                w(f'<div class="card"><b>{esc(c["name"])}</b><div class="chips">{"".join(refs(pid) for pid in c["paper_ids"])}</div>'
                  f'<div>{refs(c["summary"])}</div></div>')
            w("</div>\n")
        w(f'<h3>{t["gaps"]}</h3>\n<p class="meta">{t["gaps_lead"]}</p>\n')
        for i, g in enumerate(gaps, start=1):
            ev = "".join(f'<li>{refs(e["paper_id"])}{t["colon"]}{refs(e["note"])}</li>' for e in g["evidence"])
            near = "".join(f'<li>{refs(c["paper_id"])}{t["colon"]}{refs(c["how_close"])}</li>' for c in g.get("closest_read", []))
            near = f'<h4>{t["closest_read"]}</h4><ul class="tight">{near}</ul>' if near else ""
            queries = " ".join(f'<code class="q">{esc(q)}</code>' for q in g.get("check_queries", []))
            queries = f'<div class="kv"><b>{t["queries"]}</b>{queries}</div>' if queries else ""
            # 旧的运行目录里还有模型写的撞车风险，照旧显示
            risk = f'<div class="kv"><b>{t["risk"]}</b>{refs(g["already_done_risk"])}</div>' if g.get("already_done_risk") else ""
            w(f'<div class="card gap-card" id="gap-G{i}"><div class="meta">G{i} · {esc(t["kinds"].get(g["kind"], g["kind"]))} · '
              f'{t["admit"] if g["source"] == "authors_admit" else t["inferred"]}'
              + (f' <span class="badge b-warn">{t["weak_gap"]}</span>' if i in weak_gaps else "") + "</div>"
              f'<h3 style="margin-top:4px">{esc(g["title"])}</h3><div>{refs(g["description"])}</div>'
              f'<h4>{t["evidence"]}</h4><ul class="tight">{ev}</ul>'
              f'<div class="kv"><b>{t["why"]}</b>{refs(g["why_it_matters"])}</div>{near}{queries}{risk}</div>\n')
    else:
        w(f'<p class="meta">{t["skipped"] if stopped else t["not_done"]}</p>\n')
    w("</section>\n")

    # ---------- ④ 方案和评审 ----------
    w(f'<section id="proposals"><h2>{t["toc"][3]}</h2>\n')
    rv_names = t["sep"].join(f"<b>{esc(n)}</b>" + (f' <span class="meta">{esc(m)}</span>' if m else "") for n, m, _ in reviewer_rows)
    if proposals:
        w(f'<p class="lead">{t["proposals_lead"].format(writer=esc(writer_name), reviewers=rv_names or t["no_reviewers"])}</p>\n')
    for rec in proposals:
        pid, prop = rec["proposal_id"], rec.get("proposal")
        w(f'<div class="card proposal" id="proposal-{esc(pid)}"><div class="tag">{esc(pid)} · {t["original"] if rec.get("origin") == "original" else t["seed"]}</div>\n')
        if not prop:
            w(f'<p class="error">{t["not_written"]}{esc(rec.get("error"))}</p></div>\n')
            continue
        seed = rec.get("seed")
        exp = prop["minimal_experiment"]
        closest = "".join(f'<li>{refs(c["paper_id"])}{t["colon"]}{t["overlap"]}{refs(c["overlap"])}{t["semi"]}{t["difference"]}{refs(c["difference"])}</li>'
                          for c in prop["closest_work"]) or f'<li class="meta">{t["none"]}</li>'
        w(f'<h3 style="margin-top:4px;font-size:19px">{esc(prop["title"])}</h3><p><b>{esc(prop["one_liner"])}</b></p>'
          + (f'<div class="callout">{t["differs"]}{refs(seed["differs_from_original"])}<br>{t["based_on"]}'
             f'{" ".join(refs(g) for g in seed["based_on_gaps"])}</div>' if seed else "")
          + f'<div class="grid2"><div><div class="kv"><b>{t["problem"]}</b>{refs(prop["problem"])}</div>'
          f'<div class="kv"><b>{t["why_fail"]}</b>{refs(prop["why_existing_fail"])}</div><div class="kv"><b>{t["method"]}</b>{refs(prop["method"])}</div></div>'
          f'<div><h4>{t["closest"]}</h4><ul class="tight">{closest}</ul></div></div>'
          f'<h4>{t["min_exp"].format(d=esc(exp["duration"]))}</h4><div>{refs(exp["setup"])}</div>'
          f'<div class="meta">{t["data"]}{esc(exp["data"])}{t["semi"]}{t["baselines"]}{esc(t["sep"].join(exp["baselines"]))}{t["semi"]}'
          f'{t["metrics"]}{esc(t["sep"].join(exp["metrics"]))}</div>'
          f'<div class="kv kill"><b>{t["kill_sign"]}</b>{refs(prop["falsification"])}</div>'
          f'<div class="grid2"><div><h4>{t["risks"]}</h4>{plist(prop["risks"])}</div><div><h4>{t["checking"]}</h4>{plist(prop["needs_checking"])}</div></div>'
          + (f'<h4>{t["ask_author"]}</h4>{plist(prop["questions_for_author"])}' if prop["questions_for_author"] else "")
          + f'<h3>{t["meta_reviews"]}</h3>\n')
        mine = [m for m in metas if m["proposal_id"] == pid]
        n, rows = overlap_tally(mine, by_id)
        chair = chairs.get(pid) or {}
        if chair.get("summary"):
            w(chair_html(chair) if "consensus" not in chair["summary"] else legacy_chair_html(chair["summary"]))
        if n:
            items = "".join(
                f'<li>{refs(r["paper_id"]) + " " if r["paper_id"] else ""}{esc(r["title"])}{t["colon"]}'
                f'{t["tally_row"].format(n=n, k=len(r["by"]))}{t["lp"]}{esc(t["sep"].join(r["by"]))}{t["rp"]}'
                + ("" if r["paper_id"] else f' <span class="badge b-unk">{t["tally_external"]}</span>')
                + (f' <span class="badge b-warn">{t["tally_single"]}</span>' if n > 1 and len(r["by"]) == 1 else "") + "</li>"
                for r in rows)
            body = (f'<ul class="tight">{items}</ul><p class="meta">{t["tally_note"]}{t["tally_one"] if n == 1 else ""}</p>' if rows
                    else f'<p class="meta">{t["tally_none"]}</p>')
            w(f'<div class="callout"><b>{t["tally"]}</b> <span class="meta">{t["lp"]}{t["tally_by"].format(n=n, s="s" if n > 1 else "")}'
              f'{t["rp"]}</span>{body}</div>\n')
        w('<div class="reviewers">')
        for m in mine:
            same = m.get("family") and m.get("family") == writer.get("family")
            w(f'<div class="reviewer"><h4>{esc(m["reviewer"])} <span class="meta">{esc(m.get("model") or "")}</span>'
              + (f' <span class="badge b-warn">{t["same_family"]}</span>' if same else "") + "</h4>")
            r = m.get("review")
            if not r:
                w(f'<p class="error">{t["review_failed"]}{esc(m.get("error"))}</p></div>')
                continue
            if r["overlap"]:
                items = "".join(
                    f'<li>{refs(o["paper_id"])} {esc(o["title"])}' + ("" if o["read_full_text"] else f' <span class="badge b-unk">{t["unread"]}</span>')
                    + f'<br><span class="meta">{t["actually"]}{refs(o["what_it_actually_does"])}</span><br>'
                    f'{t["r_overlap2"]}{refs(o["overlap"])}{t["semi"]}{t["r_diff"]}{refs(o["difference"])}</li>' for o in r["overlap"])
                overlap = f'<ul class="tight">{items}</ul>'
            else:
                overlap = f'<p class="meta">{esc(r["no_overlap_note"])}</p>'
            deaths = "".join(f'<li>{refs(d["reason"])} <span class="meta">{t["lp"]}{t["from_paper"] if d["source"] == "paper" else t["from_inference"]}'
                             f'{t["colon"]}{refs(d["basis"])}{t["rp"]}</span></li>' for d in r["causes_of_death"])
            f = r["cheapest_falsification"]
            w(f'<b>{t["r_overlap"]}</b>{overlap}'
              + (f'<b>{t["r_mismatch"]}</b>{plist(r["abstract_body_mismatches"])}' if r["abstract_body_mismatches"] else "")
              + f'<b>{t["deaths"]}</b><ol class="tight">{deaths}</ol>'
              f'<b>{t["feasibility"]}</b><p style="margin:4px 0">{t["bottleneck"]}{refs(r["feasibility"]["bottleneck"])}<br>'
              f'{t["workaround"]}{refs(r["feasibility"]["workaround"])}</p>'
              f'<b>{t["falsify"]}</b>{t["lp"]}{esc(f["est_days"])}{t["rp"]}<p style="margin:4px 0">{refs(f["experiment"])}<br>'
              f'<span class="meta">{t["compare"]}{esc(f["compare_against"])}{t["semi"]}{t["metric"]}{esc(f["metric"])}</span><br>'
              f'<span class="kill">{t["give_up"]}{refs(f["kill_if"])}</span><br><span class="go">{t["go_on"]}{refs(f["continue_if"])}</span></p>'
              f'<b>{t["before_invest"]}</b>{plist(r["questions_for_author"])}</div>')
        if not mine:
            w(f'<p class="meta">{t["no_review"]}</p>')
        w("</div></div>\n")
    if not proposals:
        w(f'<p class="meta">{t["skipped"] if stopped else t["not_done"]}</p>\n')
    w("</section>\n")

    # ---------- ⑤ 你拍板、附录 ----------
    w(f'<section id="decide"><h2>{t["decide"]}</h2><p class="lead">{t["decide_lead"]}</p><div class="card"><ul class="checklist">'
      + "".join(f'<li><label><input type="checkbox">{c}</label></li>' for c in t["checklist"])
      + f'</ul><p class="meta">{t["decide_note"]}</p></div></section>\n')
    w(f'<section id="appendix"><h2>{t["appendix"]}</h2><h3>{t["who"]}</h3><div class="table-wrap"><table><thead><tr>'
      + "".join(f"<th>{h}</th>" for h in t["th_who"]) + "</tr></thead><tbody>"
      f'<tr><td>{t["who_rows"][0]}</td><td>{esc(writer_name)}</td></tr>'
      f'<tr><td>{t["who_rows"][1]}</td><td>{rv_names or t["no_reviewers"]}</td></tr>'
      + (f'<tr><td>{t["who_rows"][2]}</td><td>{chair_labels}</td></tr>' if chair_labels else "") + "</tbody></table></div>\n")
    if usage:
        w(f'<h3>{t["usage"]}</h3><div class="table-wrap"><table><thead><tr>' + "".join(
            f'<th{" class=num" if i else ""}>{h}</th>' for i, h in enumerate(t["th_usage"])) + "</tr></thead><tbody>"
          + "".join(f'<tr><td>{usage_name(n)} <span class="meta">{esc(u.get("model"))}</span></td><td class="num">{u.get("calls", 0)}</td>'
                    f'<td class="num">{u.get("input_tokens", 0):,}</td><td class="num">{u.get("output_tokens", 0):,}</td></tr>' for n, u in usage.items())
          + "</tbody></table></div>\n")
    w(f'<p class="meta">{t["run_dir"].format(run=run_dir)}</p></section>\n</main>\n'
      f'<footer><div class="wrap">{t["footer"]}</div></footer>\n</body>\n</html>\n')

    path = run.dir / "site" / "index.html" if site else run.dir / "report.html"
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(out), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="report.py", description="把运行目录渲染成 HTML 报告")
    parser.add_argument("run", help="运行目录")
    parser.add_argument("--site", action="store_true", help="另写一份 RUN/site/index.html，不含本机路径，用来发布成网页")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run = Run(args.run)
        path = build(run)
        site = build(run, site=True) if args.site else None
    except Stop as e:
        print(e, file=sys.stderr)
        return 1
    print(f"报告：{path}")
    if site:
        print(f"发布用的网页：{site}（只发布 {site.parent} 这个目录，别发布整个运行目录：里面有论文 PDF 和本机路径）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
