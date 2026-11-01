"""程序拼进提示词、写进结果文件的固定文字，中英文各一份，按配置里的 [review] lang 选。

提示词正文在 prompts/（中文）和 prompts/en/（英文）里；这里是程序自己拼的那部分：
论文信息、精读笔记摘要、proposal 和评审的转述、JSON 格式要求等。改的时候两份一起改。
命令行日志和报错不在这里，目前只有中文。
"""
from __future__ import annotations

LANGS = ("zh", "en")

ZH = {
    # 论文信息
    "published": {True: "已发表", False: "预印本", None: "发表情况不明"},
    "year_unknown": "年份不明",
    "venue_unknown": "出处不明",
    "cited": "被引 {n}",
    "bits_sep": "，",
    "paper_line": "[{id}] {title}（{bits}）",
    "authors": "\n作者：{names}",
    "names_sep": "、",
    "join": "；",
    "none": "无",
    "empty": "（无）",
    # 初筛
    "screen_item": "{line}\n摘要：{abstract}",
    "no_abstract": "无摘要",
    "screen_failed": "初筛失败，默认 medium",
    "screen_missed": "模型漏判，默认 medium",
    "screen_local": "你提供的本地 PDF",
    # 下载和精读
    "no_pdf_note": "没有开放获取的 PDF，建议手动下载",
    "manual_note": "你手动放进 papers/ 的 PDF",
    "abstract_missing": "（没有单独的摘要，见全文开头）",
    "truncated": "（太长，中间有省略）",
    # 精读笔记摘要
    "paper_types": {"empirical": "方法或实证研究", "survey": "综述", "position": "立场或观点文章",
                    "resource": "数据集、基准或工具", "theory": "理论分析", "other": "其他"},
    "review_digest": (
        "{line} 类型：{kind}；质量档位：{tier}\n"
        "  实际做了什么：{does}\n"
        "  方法：{method}\n"
        "  实验设置：{setup}\n"
        "  主要结果：{results}\n"
        "  局限：{limits}\n"
        "  未解决的问题：{open}\n"
        "  证据强度：{strength}；危险信号：{flags}"
    ),
    "overclaims": "  摘要夸大或和正文矛盾：{items}",
    "scope_gaps": "  摘要没交代的适用范围：{items}",
    "abstract_digest": "{line}（只有摘要，没读到全文）\n  摘要：{abstract}",
    "no_fulltext_notes": "（没有读到全文）",
    # 空白和新方向
    "gap_line": "G{i}（{kind}，{source}）{title}：{description}；证据：{evidence}；已有人做的风险：{risk}",
    "gap_source": {"authors_admit": "作者承认", "inferred": "推断"},
    "gap_evidence": "{id} {note}",
    "no_gaps": "（没有找到明确的空白）",
    "landscape_text": "概况：{summary}\n\n空白：\n{gaps}",
    "original_block": "作者的 idea：\n{idea}",
    "seed_block": (
        "从文献空白里挖出的新方向（作者原始 idea 是：{idea}）：\n"
        "题目：{title}\n一句话：{one_liner}\n依据的空白：{gaps}\n和原始 idea 的区别：{differs}\n切入点：{entry}"
    ),
    # proposal 和评审
    "closest_item": "  - {id}：重合 {overlap}；区别 {difference}",
    "proposal_text": (
        "编号：{pid}\n"
        "题目：{title}\n"
        "一句话：{one_liner}\n"
        "要解决的问题：{problem}\n"
        "为什么现有方法解决不了：{why}\n"
        "最接近的工作：\n{closest}\n"
        "方法：{method}\n"
        "最小实验：{setup}；数据：{data}；基线：{baselines}；指标：{metrics}；用时：{duration}\n"
        "出现什么结果说明不成立：{falsification}\n"
        "风险：{risks}\n"
        "需要补查：{checking}"
    ),
    "excerpt": "{line}\n<<<全文节选开始\n{text}\n全文节选结束>>>",
    "no_excerpts": "（proposal 引用的论文没有可用的全文）",
    "overlap_item": "  - {id} {title}：重合 {overlap}；区别 {difference}",
    "death_item": "  - {reason}（依据：{basis}；{source}）",
    "death_source": {"paper": "论文", "inference": "推断"},
    "meta_review_text": (
        "## 评审：{reviewer}\n"
        "撞车：\n{overlap}\n"
        "死因：\n{deaths}\n"
        "可行性：卡点 {bottleneck}；绕法 {workaround}\n"
        "证伪实验：{experiment}；对比 {compare}；指标 {metric}；放弃条件 {kill}；继续条件 {go}；用时 {days}\n"
        "必须回答的问题：{questions}"
    ),
    # 结构化输出
    "json_instruction": "只输出一个 JSON 对象，不要输出其他文字。JSON 必须符合下面的 JSON Schema（{name}）：",
    "retry": "上一次的输出没有通过格式校验：{error}\n请重新输出完整的 JSON，只输出 JSON。",
    "ping_system": "你是测试助手。按要求的 JSON 格式输出。",
    "ping_user": "idea：让大模型代替真人填社会调查问卷。请输出检索计划，检索式给 4 条。",
}

EN = {
    "published": {True: "peer-reviewed", False: "preprint", None: "publication status unknown"},
    "year_unknown": "year unknown",
    "venue_unknown": "venue unknown",
    "cited": "cited {n} times",
    "bits_sep": ", ",
    "paper_line": "[{id}] {title} ({bits})",
    "authors": "\nAuthors: {names}",
    "names_sep": ", ",
    "join": "; ",
    "none": "none",
    "empty": "(none)",
    "screen_item": "{line}\nAbstract: {abstract}",
    "no_abstract": "no abstract",
    "screen_failed": "Screening failed; defaulted to medium",
    "screen_missed": "Missed by the model; defaulted to medium",
    "screen_local": "Local PDF you provided",
    "no_pdf_note": "No open-access PDF; consider downloading it yourself",
    "manual_note": "PDF you put into papers/",
    "abstract_missing": "(no separate abstract; see the start of the full text)",
    "truncated": " (too long; the middle is omitted)",
    "paper_types": {"empirical": "method or empirical study", "survey": "survey", "position": "position or opinion paper",
                    "resource": "dataset, benchmark or tool", "theory": "theoretical analysis", "other": "other"},
    "review_digest": (
        "{line} Type: {kind}; quality tier: {tier}\n"
        "  What it actually does: {does}\n"
        "  Method: {method}\n"
        "  Setup: {setup}\n"
        "  Key results: {results}\n"
        "  Limitations: {limits}\n"
        "  Open questions: {open}\n"
        "  Evidence strength: {strength}; red flags: {flags}"
    ),
    "overclaims": "  Abstract overclaims or contradicts the body: {items}",
    "scope_gaps": "  Scope the abstract leaves out: {items}",
    "abstract_digest": "{line} (abstract only; full text not read)\n  Abstract: {abstract}",
    "no_fulltext_notes": "(no full texts were read)",
    "gap_line": "G{i} ({kind}, {source}) {title}: {description}; evidence: {evidence}; risk that it is already done: {risk}",
    "gap_source": {"authors_admit": "admitted by the authors", "inferred": "inferred"},
    "gap_evidence": "{id} {note}",
    "no_gaps": "(no clear gaps found)",
    "landscape_text": "Overview: {summary}\n\nGaps:\n{gaps}",
    "original_block": "The author's idea:\n{idea}",
    "seed_block": (
        "A new direction mined from gaps in the literature (the author's original idea is: {idea}):\n"
        "Title: {title}\nOne-liner: {one_liner}\nBased on gaps: {gaps}\nHow it differs from the original idea: {differs}\n"
        "Entry point: {entry}"
    ),
    "closest_item": "  - {id}: overlap {overlap}; difference {difference}",
    "proposal_text": (
        "ID: {pid}\n"
        "Title: {title}\n"
        "One-liner: {one_liner}\n"
        "Problem: {problem}\n"
        "Why existing methods fall short: {why}\n"
        "Closest work:\n{closest}\n"
        "Method: {method}\n"
        "Minimal experiment: {setup}; data: {data}; baselines: {baselines}; metrics: {metrics}; duration: {duration}\n"
        "Result that would show the idea does not hold: {falsification}\n"
        "Risks: {risks}\n"
        "Needs checking: {checking}"
    ),
    "excerpt": "{line}\n<<<FULL-TEXT EXCERPT START\n{text}\nFULL-TEXT EXCERPT END>>>",
    "no_excerpts": "(none of the papers cited by the proposal has a usable full text)",
    "overlap_item": "  - {id} {title}: overlap {overlap}; difference {difference}",
    "death_item": "  - {reason} (basis: {basis}; {source})",
    "death_source": {"paper": "from a paper", "inference": "inferred"},
    "meta_review_text": (
        "## Reviewer: {reviewer}\n"
        "Overlap:\n{overlap}\n"
        "Causes of death:\n{deaths}\n"
        "Feasibility: bottleneck {bottleneck}; workaround {workaround}\n"
        "Falsification experiment: {experiment}; compare against {compare}; metric {metric}; "
        "kill if {kill}; continue if {go}; duration {days}\n"
        "Questions the author must answer: {questions}"
    ),
    "json_instruction": "Output a single JSON object and nothing else. The JSON must follow this JSON Schema ({name}):",
    "retry": "The previous output failed validation: {error}\nOutput the complete JSON again, and only the JSON.",
    "ping_system": "You are a test assistant. Output JSON in the required format.",
    "ping_user": "Idea: use LLMs in place of human respondents for social surveys. Output a search plan with 4 queries.",
}

TEXTS = {"zh": ZH, "en": EN}
