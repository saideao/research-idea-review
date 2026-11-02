# 你要写的 JSON 文件

所有字段都必填；没有内容的列表写 `[]`。档位只能是 `high` / `medium` / `low`。
完整定义（含每个字段的说明）在 `assets/schemas.zh.json`，`ir.py next` 按它检查，格式不对会指出是哪个字段。
例子里的 `...` 换成实际内容。

## 01_plan.json（QueryPlan）

```json
{
  "restated_idea": "一两句话复述 idea 要解决的问题",
  "field": "所属的大领域",
  "core_concepts": ["概念 1", "概念 2", "概念 3"],
  "search_queries": ["llm simulated survey respondents", "synthetic survey responses language models", "..."]
}
```

## 03_screen.json（ScreenItem 的列表）

```json
[
  {"paper_id": "P01", "relevance": "high", "reason": "一句话理由"},
  {"paper_id": "P02", "relevance": "low", "reason": "..."}
]
```

候选扩充后再初筛，把新结果追加到同一个列表里，旧的保留。

## notes/P03.json（PaperReview，一篇一个文件，文件名就是编号）

```json
{
  "what_it_actually_does": "依据正文，三五句话",
  "problem": "...",
  "method": "...",
  "setup": "数据、基线、指标",
  "key_results": ["结果 1 [p.6]", "结果 2 [p.7]"],
  "abstract_vs_body": {
    "overclaims": ["摘要说……，正文实际只……[p.5]"],
    "scope_gaps": ["只在……条件下测过，摘要没提 [p.4]"]
  },
  "quality": {
    "paper_type": "empirical",
    "evidence_strength": "medium",
    "rigor": ["有三个基线，没有消融 [p.6]"],
    "reproducibility": "low",
    "red_flags": [],
    "tier": "medium",
    "tier_reason": "一两句话"
  },
  "relevance_to_idea": "和用户的 idea 重合在哪、能借鉴什么",
  "limitations": ["作者承认：……[p.9]", "我看出来的：……"],
  "open_questions": ["……[p.9]"]
}
```

`paper_type` 只能是 `empirical` / `survey` / `position` / `resource` / `theory` / `other`。

## 06_landscape.json（Landscape）

```json
{
  "summary": "这个方向现在做到了哪一步",
  "clusters": [
    {"name": "一类做法", "paper_ids": ["P01", "P04"], "summary": "这类工作做到了哪一步"}
  ],
  "gaps": [
    {
      "kind": "untested_assumption",
      "title": "一句话标题",
      "description": "...",
      "evidence": [{"paper_id": "P04", "note": "哪部分支撑这个判断 [p.8]"}],
      "source": "authors_admit",
      "why_it_matters": "...",
      "already_done_risk": "可能已经有人做过的风险和理由"
    }
  ]
}
```

`kind` 只能是 `untested_assumption`（没检验过的假设）/ `contradiction`（结论互相矛盾）/ `limitation`（已知局限）/
`missing_setting`（没覆盖的场景）/ `measurement`（评测方法的问题）/ `transfer`（方法迁移）/ `other`。
`source` 只能是 `authors_admit` / `inferred`。

## 07_seeds.json（IdeaSeeds）

```json
{
  "seeds": [
    {
      "title": "...",
      "one_liner": "...",
      "based_on_gaps": ["G1", "G3"],
      "differs_from_original": "和原始 idea 的区别，不能只是换个名字",
      "entry_point": "一周内能动手验证的切入点"
    }
  ]
}
```

## 08_proposals.json（列表，每份 proposal 一条）

```json
[
  {
    "proposal_id": "I1",
    "origin": "original",
    "seed": null,
    "error": "",
    "proposal": {
      "title": "...",
      "one_liner": "...",
      "problem": "...",
      "why_existing_fail": "为什么现有方法解决不了，引用编号，比如 P04 只……",
      "closest_work": [{"paper_id": "P04", "overlap": "...", "difference": "..."}],
      "method": "...",
      "minimal_experiment": {
        "setup": "...", "data": "...", "baselines": ["..."], "metrics": ["..."], "duration": "预计用时"
      },
      "falsification": "出现什么结果说明这个 idea 不成立",
      "risks": ["..."],
      "needs_checking": ["需要补查的文献或事实"],
      "questions_for_author": ["作者没说清、要作者自己回答的问题"]
    }
  },
  {
    "proposal_id": "I2",
    "origin": "seed",
    "seed": {"title": "...", "one_liner": "...", "based_on_gaps": ["G1"], "differs_from_original": "...", "entry_point": "..."},
    "error": "",
    "proposal": {"title": "...", "...": "和上面一样的字段"}
  }
]
```

I2 起的 `seed` 照抄 07_seeds.json 里对应的那一条（第一条对应 I2，第二条对应 I3）。

## 09_meta_reviews.json（一般由 ir.py review 写）

只有用子代理代评时才需要你手写，每份评审一条：

```json
[
  {
    "proposal_id": "I1",
    "reviewer": "subagent",
    "model": "你的模型名",
    "family": "anthropic",
    "error": "",
    "review": {"...": "MetaReview 格式，见 assets/schemas.zh.json 里的 MetaReview"}
  }
]
```

## 10_chair.json（列表，每份有两份以上评审的 proposal 一条）

```json
[
  {
    "proposal_id": "I1",
    "error": "",
    "summary": {
      "summary": "几位评审意见的概括，不下结论、不打分",
      "consensus": [{"issue": "...", "raised_by": ["codex", "gemini"]}],
      "disagreements": [{"topic": "...", "positions": ["codex：……", "gemini：……"]}],
      "must_verify": ["作者必须亲自点开论文核实的说法"]
    }
  }
]
```
