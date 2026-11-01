"""各环节的数据类型。

前半部分是要求模型按 JSON 输出的类型（字段全部必填，方便做结构化输出）；
后半部分是程序自己记录的类型（论文元数据、下载状态、一次运行的汇总）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Level = Literal["high", "medium", "low"]


# ---------- 模型输出：检索计划 ----------

class QueryPlan(BaseModel):
    restated_idea: str = Field(description="用一两句话复述这个 idea，说清要解决什么问题")
    field: str = Field(description="所属的大领域，宽泛一点即可")
    core_concepts: list[str] = Field(description="3 到 6 个核心概念")
    search_queries: list[str] = Field(description="4 到 8 条英文检索式，覆盖不同说法和相邻方向")


# ---------- 模型输出：按摘要初筛 ----------

class ScreenItem(BaseModel):
    paper_id: str = Field(description="论文编号，原样照抄，比如 P07")
    relevance: Level = Field(description="值不值得下载全文来读：high / medium / low")
    reason: str = Field(description="一句话理由")


class ScreenBatch(BaseModel):
    items: list[ScreenItem]


# ---------- 模型输出：读全文、评质量 ----------

class AbstractCheck(BaseModel):
    """摘要和正文对照。只有夸大或矛盾才算不一致；只是没交代适用范围的单独列，不打警示标记。"""
    overclaims: list[str] = Field(description="夸大或矛盾：摘要的说法正文不支持，或者数字、结论和正文对不上。"
                                              "每条写清摘要怎么说、正文实际是什么，注明页码如 [p.6]；没有就留空")
    scope_gaps: list[str] = Field(description="没交代适用范围：摘要的说法正文撑得住，但只在更窄的条件下成立"
                                              "（比如只测了部分模型、样本很小、只用了一个数据集），摘要没提。"
                                              "每条写清是什么条件，注明页码；没有就留空")

    @model_validator(mode="before")
    @classmethod
    def _from_old_format(cls, data):
        # 旧版本是 consistent + discrepancies。读旧的运行目录时，标了不一致的算夸大或矛盾，其余的算没交代适用范围
        if isinstance(data, dict) and "discrepancies" in data:
            old = data["discrepancies"]
            consistent = data.get("consistent", not old)
            data = {"overclaims": [] if consistent else old, "scope_gaps": old if consistent else []}
        return data

    @property
    def consistent(self) -> bool:
        return not self.overclaims


class QualityAssessment(BaseModel):
    evidence_strength: Level = Field(description="核心结论的证据强度")
    rigor: list[str] = Field(description="对基线、消融、统计检验、数据规模等的具体观察，注明页码")
    reproducibility: Level = Field(description="可复现程度：代码、数据、超参数是否给全")
    red_flags: list[str] = Field(description="危险信号，比如结论超出实验、没有基线、样本极小；没有就留空")
    tier: Level = Field(description="综合质量档位：high / medium / low")
    tier_reason: str = Field(description="定档理由，一两句话")


class PaperReview(BaseModel):
    what_it_actually_does: str = Field(description="依据正文（不是摘要），这篇论文实际做了什么，三五句话")
    problem: str = Field(description="它要解决的问题")
    method: str = Field(description="方法要点")
    setup: str = Field(description="实验设置：数据、基线、指标")
    key_results: list[str] = Field(description="主要结果，注明页码")
    abstract_vs_body: AbstractCheck
    quality: QualityAssessment
    relevance_to_idea: str = Field(description="和用户的 idea 有什么关系：重合在哪、能借鉴什么")
    limitations: list[str] = Field(description="局限，区分作者自己承认的和你看出来的")
    open_questions: list[str] = Field(description="文中提到或能直接看出、还没解决的问题，注明页码")


# ---------- 模型输出：版图和空白 ----------

class Evidence(BaseModel):
    paper_id: str = Field(description="支撑这条判断的论文编号")
    note: str = Field(description="这篇论文里的哪部分支撑了判断，注明页码")


class Cluster(BaseModel):
    name: str
    paper_ids: list[str]
    summary: str = Field(description="这一类工作做到了哪一步")


class Gap(BaseModel):
    kind: Literal["untested_assumption", "contradiction", "limitation", "missing_setting", "measurement", "transfer", "other"] = Field(
        description="空白的类型：没检验过的假设 / 结论互相矛盾 / 已知局限 / 没覆盖的场景 / 评测方法有问题 / 方法迁移 / 其他"
    )
    title: str = Field(description="一句话标题")
    description: str
    evidence: list[Evidence]
    source: Literal["authors_admit", "inferred"] = Field(description="authors_admit：论文作者自己承认；inferred：你推断的")
    why_it_matters: str
    already_done_risk: str = Field(description="可能已经有人做过的风险，以及理由")


class Landscape(BaseModel):
    summary: str = Field(description="这个方向现在做到了哪一步，一段话")
    clusters: list[Cluster]
    gaps: list[Gap]


# ---------- 模型输出：新 idea 和 proposal ----------

class IdeaSeed(BaseModel):
    title: str
    one_liner: str
    based_on_gaps: list[str] = Field(description="依据的空白编号，比如 G2")
    differs_from_original: str = Field(description="和用户原始 idea 的区别，不能只是换个名字")
    entry_point: str = Field(description="一周内能动手验证的切入点")


class IdeaSeeds(BaseModel):
    seeds: list[IdeaSeed]


class ClosestWork(BaseModel):
    paper_id: str
    overlap: str
    difference: str


class MinimalExperiment(BaseModel):
    setup: str
    data: str
    baselines: list[str]
    metrics: list[str]
    duration: str = Field(description="预计用时")


class Proposal(BaseModel):
    title: str
    one_liner: str
    problem: str
    why_existing_fail: str = Field(description="为什么现有方法解决不了，引用论文编号")
    closest_work: list[ClosestWork]
    method: str
    minimal_experiment: MinimalExperiment
    falsification: str = Field(description="出现什么结果，说明这个 idea 不成立")
    risks: list[str]
    needs_checking: list[str] = Field(description="需要补查的文献或事实；没有就留空")
    questions_for_author: list[str] = Field(description="作者没说清、需要作者自己回答的问题")


# ---------- 模型输出：元评审 ----------

class OverlapItem(BaseModel):
    paper_id: str = Field(description="附上论文的编号；附上的论文以外的工作写 external")
    title: str
    what_it_actually_does: str = Field(description="依据正文，它实际做了什么，注明页码")
    overlap: str
    difference: str
    read_full_text: bool = Field(description="你是否读过它的全文")


class Death(BaseModel):
    reason: str
    basis: str
    source: Literal["paper", "inference"] = Field(description="paper：论文里写的；inference：你推断的")


class Feasibility(BaseModel):
    bottleneck: str = Field(description="数据、算力、时间、技术上最可能卡住的地方")
    workaround: str


class Falsification(BaseModel):
    experiment: str
    compare_against: str
    metric: str
    kill_if: str = Field(description="出现什么结果就该放弃")
    continue_if: str = Field(description="出现什么结果才值得继续")
    est_days: str


class MetaReview(BaseModel):
    overlap: list[OverlapItem]
    no_overlap_note: str = Field(description="附上的论文里没有撞车时写明；有撞车就留空")
    abstract_body_mismatches: list[str] = Field(description="摘要夸大或和正文矛盾的地方（只是没交代适用范围的不算）；没有就留空")
    causes_of_death: list[Death] = Field(description="最多 3 条，最致命的放前面")
    feasibility: Feasibility
    cheapest_falsification: Falsification
    questions_for_author: list[str] = Field(description="作者在投入之前必须回答的 3 个问题")


class ConsensusIssue(BaseModel):
    issue: str
    raised_by: list[str] = Field(description="提出这个问题的评审名字")


class Disagreement(BaseModel):
    topic: str
    positions: list[str] = Field(description="每条写清是哪位评审、怎么看")


class ChairSummary(BaseModel):
    summary: str = Field(description="几位评审意见的概括，不下结论、不打分")
    consensus: list[ConsensusIssue] = Field(description="两位及以上评审都提到的问题")
    disagreements: list[Disagreement]
    must_verify: list[str] = Field(description="作者必须亲自点开论文核实的说法")


# lang = "en" 时发给模型的字段说明。键是"类名.字段名"，只写类名的是类本身的说明。
# 上面的中文说明改了，这里要跟着改；测试会检查英文模式下发给模型的 schema 里没有中文。
DESCRIPTIONS_EN = {
    "QueryPlan.restated_idea": "Restate the idea in one or two sentences, making clear what problem it addresses",
    "QueryPlan.field": "The broad field it belongs to; keep it general",
    "QueryPlan.core_concepts": "3 to 6 core concepts",
    "QueryPlan.search_queries": "4 to 8 English search queries covering different phrasings and adjacent directions",
    "ScreenItem.paper_id": "Paper ID, copied exactly, e.g. P07",
    "ScreenItem.relevance": "Whether it is worth downloading the full text: high / medium / low",
    "ScreenItem.reason": "One-sentence reason",
    "PaperReview.what_it_actually_does": "Based on the body (not the abstract), what this paper actually does, in three to five sentences",
    "PaperReview.problem": "The problem it addresses",
    "PaperReview.method": "Key points of the method",
    "PaperReview.setup": "Experimental setup: data, baselines, metrics",
    "PaperReview.key_results": "Key results, with page numbers",
    "PaperReview.relevance_to_idea": "How it relates to the user's idea: where they overlap and what can be borrowed",
    "PaperReview.limitations": "Limitations, separating those the authors admit from those you noticed",
    "PaperReview.open_questions": "Unresolved questions mentioned in the paper or directly evident from it, with page numbers",
    "AbstractCheck": "Abstract vs. body. Only overclaims or contradictions count as inconsistent; "
                     "scope the abstract leaves out is listed separately and not flagged as a warning.",
    "AbstractCheck.overclaims": "Overclaims or contradictions: claims in the abstract the body does not support, or numbers and "
                                "conclusions that do not match the body. For each, state what the abstract says and what the body "
                                "actually shows, with page numbers such as [p.6]; leave empty if none",
    "AbstractCheck.scope_gaps": "Scope left out: the body supports the abstract's claim, but only under narrower conditions "
                                "(e.g. only some models tested, a very small sample, a single dataset) that the abstract does "
                                "not mention. State the condition for each, with page numbers; leave empty if none",
    "QualityAssessment.evidence_strength": "Strength of the evidence behind the core conclusions",
    "QualityAssessment.rigor": "Specific observations on baselines, ablations, statistical tests, data scale, etc., with page numbers",
    "QualityAssessment.reproducibility": "Reproducibility: whether code, data and hyperparameters are fully provided",
    "QualityAssessment.red_flags": "Red flags, e.g. conclusions beyond the experiments, no baselines, tiny samples; leave empty if none",
    "QualityAssessment.tier": "Overall quality tier: high / medium / low",
    "QualityAssessment.tier_reason": "Reason for the tier, in one or two sentences",
    "Landscape.summary": "Where this direction stands now, in one paragraph",
    "Cluster.summary": "How far this group of work has gotten",
    "Gap.kind": "Type of gap: untested assumption / contradictory conclusions / known limitation / uncovered setting / "
                "flawed evaluation / method transfer / other",
    "Gap.title": "One-line title",
    "Gap.source": "authors_admit: admitted by the paper's authors; inferred: inferred by you",
    "Gap.already_done_risk": "Risk that someone has already done it, and why",
    "Evidence.paper_id": "ID of the paper supporting this point",
    "Evidence.note": "Which part of the paper supports the point, with page numbers",
    "IdeaSeed.based_on_gaps": "IDs of the gaps it is based on, e.g. G2",
    "IdeaSeed.differs_from_original": "How it differs from the user's original idea; a new name alone does not count",
    "IdeaSeed.entry_point": "An entry point that can be tested hands-on within a week",
    "Proposal.why_existing_fail": "Why existing methods cannot solve it, citing paper IDs",
    "Proposal.falsification": "What result would show the idea does not hold",
    "Proposal.needs_checking": "Literature or facts that still need checking; leave empty if none",
    "Proposal.questions_for_author": "Points the author left unclear and must answer themselves",
    "MinimalExperiment.duration": "Estimated duration",
    "OverlapItem.paper_id": "ID of an attached paper; write external for work outside the attached papers",
    "OverlapItem.what_it_actually_does": "Based on the body, what it actually does, with page numbers",
    "OverlapItem.read_full_text": "Whether you have read its full text",
    "MetaReview.no_overlap_note": "State this when none of the attached papers overlaps; leave empty if there is overlap",
    "MetaReview.abstract_body_mismatches": "Places where an abstract overclaims or contradicts the body "
                                           "(scope merely left out does not count); leave empty if none",
    "MetaReview.causes_of_death": "At most 3, most fatal first",
    "MetaReview.questions_for_author": "3 questions the author must answer before investing",
    "Death.source": "paper: stated in a paper; inference: inferred by you",
    "Feasibility.bottleneck": "Where it is most likely to get stuck: data, compute, time or technique",
    "Falsification.kill_if": "Result that means you should give up",
    "Falsification.continue_if": "Result that makes it worth continuing",
    "ChairSummary.summary": "Summary of the reviewers' opinions, with no verdict and no score",
    "ChairSummary.consensus": "Issues raised by two or more reviewers",
    "ChairSummary.must_verify": "Claims the author must personally verify by opening the papers",
    "ConsensusIssue.raised_by": "Names of the reviewers who raised this issue",
    "Disagreement.positions": "For each, state which reviewer and what they think",
}


# ---------- 程序记录 ----------

class Paper(BaseModel):
    paper_id: str = ""
    title: str
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    peer_reviewed: bool | None = None
    citation_count: int | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    s2_id: str | None = None
    openalex_id: str | None = None
    url: str | None = None
    pdf_urls: list[str] = []
    sources: list[str] = []
    query_hits: int = 0
    local_path: str | None = None


class FullText(BaseModel):
    paper_id: str
    status: Literal["ok", "no_pdf", "failed", "skipped"]
    pdf_path: str | None = None
    text_path: str | None = None
    chars: int = 0
    pages: int = 0
    note: str = ""
    manual: bool = False   # 你手动放进 papers/ 的 PDF


class ReviewRecord(BaseModel):
    paper_id: str
    review: PaperReview | None = None
    error: str = ""
    truncated: bool = False


class ProposalRecord(BaseModel):
    proposal_id: str
    origin: Literal["original", "seed"]
    seed: IdeaSeed | None = None
    proposal: Proposal | None = None
    error: str = ""


class MetaReviewRecord(BaseModel):
    proposal_id: str
    reviewer: str
    model: str
    review: MetaReview | None = None
    error: str = ""


class ChairRecord(BaseModel):
    proposal_id: str
    summary: ChairSummary | None = None
    error: str = ""
