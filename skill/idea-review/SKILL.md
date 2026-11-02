---
name: idea-review
description: 给一个还很粗糙的研究 idea 做文献调研和可行性审查：拆检索词，在 Semantic Scholar、arXiv、OpenAlex 上检索，按摘要初筛，下载开放获取的全文精读并评质量，找出有论文证据的空白，写 proposal，再请别家公司的模型（本机的 codex、gemini、claude 命令行工具或 OpenAI 兼容接口）做元评审，最后出一份 HTML 报告。用户说"这个 idea 有没有人做过""帮我查查相关文献""这个研究想法靠不靠谱""找找这个方向还有什么空白""把这个想法写成 proposal 再找人挑毛病"，或者 review my research idea、is this idea novel、find literature gaps 时，都用这个 skill，哪怕用户没提报告或评审。
---

# idea-review：查文献、找空白、写 proposal、换模型挑毛病

分工：要动脑子的步骤你自己做（拆检索词、初筛、精读评质量、找空白、写 proposal、汇总评审意见）；固定的步骤交给脚本：

- `scripts/ir.py`：检索、下载全文、检查文件格式和进度、调用别家模型做元评审。
- `scripts/report.py`：把运行目录里的结果渲染成 HTML 报告。

这样分是因为：检索接口有限流和去重的细节，PDF 要校验、按页标号；调别家命令行工具的参数错一个就会读到用户的配置或者改走 API 计费；"证据不够就停"这条规矩要每次都执行，不能看心情。这些写死在脚本里，你把精力放在读论文和写东西上。

下文 `SKILL_DIR` 指这个文件所在的目录，`RUN` 指这次的运行目录。

## 开始前

先和用户对齐这几件事，能用默认的就别问：

- **idea**：一两句话就够。只有一个词、看不出要解决什么问题的，请用户补一句。
- **语言**：跟用户说话的语言走，`--lang zh` 或 `--lang en`。笔记、proposal、报告都用这个语言写。
- **数量**：默认候选 40 篇、精读 8 篇、proposal 3 份（原始 idea 1 份，加 2 个新方向）。用户想快一点可以调小 `--refs`，想读得全一点调大。
- **评审**：运行 `ir.py reviewers --writer <你的公司>`，看本机有哪些别家的模型能用，告诉用户你打算调用哪几个。这会用掉对应工具的订阅额度或 API 费用，用户点头再调。

环境：要能联网、能运行 Python 3.10+。优先用 `uv run SKILL_DIR/scripts/ir.py ...`，它会自动装抽 PDF 文字用的 pypdf；没有 uv 就用 `python3`，缺 pypdf 时脚本会提示怎么装。运行目录默认建在用户当前目录下：`idea-review-runs/<日期>-<几个英文词>/`。

## 流程

每做完一步就运行一次 `ir.py next RUN`。它检查你写的文件格式对不对，告诉你下一步做什么、要写几个。中途断了或者换了会话，也从 `next` 接着来，已经做完的不用重做。

**0. 建目录**

```bash
uv run SKILL_DIR/scripts/ir.py init RUN --idea "用户的 idea 原文" --lang zh --writer anthropic --writer-model "你的模型名"
```

`--writer` 填你自己是哪家的模型：Claude 填 anthropic，GPT / Codex 填 openai，Gemini 填 google，其他填 other。元评审要换别家的模型，这一项决定排除谁。

**1. 拆检索词**：写 `RUN/01_plan.json`。要求见 references/steps.md 第 1 节。

**2. 检索**：`ir.py search RUN`。用户手上有 PDF 的，加 `--papers-dir 目录`，这些论文编号 L01、L02…，不占精读名额。

**3. 初筛**：读 `RUN/screen_todo.md`，每篇给 high / medium / low 和一句理由，写进 `RUN/03_screen.json`。这一步只判断值不值得读全文，不判断有没有人做过。见第 3 节。

**4. 下载全文**：`ir.py fetch RUN`。它按初筛结果从高到低下载，下载失败的往下顺延，没有链接的会按标题去 arXiv 找预印本。如果它说"又取了 N 篇候选"，说明能下载的不够，回到第 3 步初筛这些新候选（追加进 `03_screen.json`），再运行 fetch。

**5. 精读**：`next` 会列出要读的论文和全文文件（`RUN/text/P03.txt`，已经用 `[p.N]` 标了页）。每篇写一个 `RUN/notes/P03.json`，要求见第 5 节。

这一步最占篇幅，一篇全文几万字。能开子代理（比如 Claude Code 的 Task 工具）就一篇交给一个子代理，并行读：给它 idea 原文、论文编号、全文路径、references/steps.md 第 5 节和 references/formats.md 里 notes 那一节，让它把 JSON 直接写到 `RUN/notes/<编号>.json`。不能开子代理就一篇一篇来，读完一篇立刻写文件，再读下一篇。全文长就分段读，方法和实验部分不能跳。

**证据门槛**：读完运行 `next`。读到的全文少于 3 篇（`--min-fulltexts` 可改），或者全是 low，它会说停。停了就别往下找空白、写 proposal：两三篇弱论文撑不起十个空白，硬写只会生成看起来像样的空话。这时直接出报告（第 11 步），告诉用户报告顶部写的补救办法：手动下载没拿到全文的论文，按编号放进 `RUN/papers/`（比如 `P07.pdf`）后让你接着跑；或者换检索式开一次新的运行。只有用户明确说"就用现有的跑完"，才运行 `ir.py next RUN --force`。

**6. 版图和空白**：写 `RUN/06_landscape.json`。空白个数不超过 `next` 给的上限（按读到的篇数定，3 到 8 个）。见第 6 节。

**7. 新方向**：写 `RUN/07_seeds.json`，个数按 `next` 说的（不会超过空白个数）。见第 7 节。

**8. proposal**：写 `RUN/08_proposals.json`。I1 是用户的原始 idea，I2 起依次对应 seeds 里的新方向。见第 8 节。

**9. 元评审**：`next` 会给出能用的评审，比如：

```bash
uv run SKILL_DIR/scripts/ir.py review RUN --reviewer codex --reviewer gemini
```

每份可能要几分钟，放到后台跑，跑完再 `next`。评审在空的临时目录里、新会话里独立完成，只看得到 proposal 和论文材料。本机一个别家的模型都没有时，见第 9 节的"没有别家模型时"。

**10. 汇总**：每份 proposal 有两份及以上成功的评审时，写 `RUN/10_chair.json`。只有一份评审的跳过。见第 10 节。

**11. 出报告**：

```bash
python3 SKILL_DIR/scripts/report.py RUN
```

把报告路径告诉用户（浏览器打开）。回复里简短说清：读了几篇全文、质量怎么分布、找到几个空白、几家评审都提到的问题是什么。不要替用户下"值得做"或"不值得做"的结论，拍板的是用户。

## 每一步都要守的规矩

- **不编造**：论文、页码、数字、结论都要来自全文或检索结果。拿不准就写拿不准。
- **判断依据正文**：摘要常常说得比正文大。引用全文时写页码，格式 `[p.6]`。
- **分清来源**：哪些是论文写的，哪些是你推断的，要写明。
- **只引用候选里的编号**（P07、L02 这种）。需要别的文献，写进 `needs_checking`，不要凭记忆补论文。
- **不夸、不打分、不替用户拍板**。报告是帮用户发现盲点的。

## 运行目录里的文件

| 文件 | 谁写 | 内容 |
|---|---|---|
| `meta.json` | init | idea、语言、数量、你是哪家的模型 |
| `01_plan.json` | 你 | 复述、领域、核心概念、检索式 |
| `02_search_results.json`、`02_candidates.json` | search | 全部检索结果、候选（带编号） |
| `screen_todo.md` | search / fetch / next | 还没初筛的候选，给你读 |
| `03_screen.json` | 你 | 初筛结果 |
| `04_fulltext.json`、`papers/`、`text/` | fetch | 全文下载记录、PDF、抽出的文字 |
| `notes/P03.json` | 你（或子代理） | 一篇一个精读笔记 |
| `05_reviews.json` | next | 合并后的精读笔记 |
| `06_landscape.json` | 你 | 版图和空白 |
| `07_seeds.json` | 你 | 新方向 |
| `08_proposals.json` | 你 | proposal |
| `09_meta_reviews.json`、`usage.json` | review | 元评审、评审用量 |
| `10_chair.json` | 你 | 评审意见汇总 |
| `report.html` | report.py | 报告 |

## 参考文件

- `references/steps.md`：每一步的具体要求，做到哪一步读哪一节。
- `references/formats.md`：你要写的每个 JSON 文件的格式和例子，写文件前看对应一节。
- `assets/schemas.zh.json`：完整的 JSON Schema，`next` 按它检查格式。
