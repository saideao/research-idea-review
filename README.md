# idea-review：给你的研究 idea 做一次文献调研和多模型评审

中文 | [English](README.en.md)

输入一句话的研究 idea，它会：

1. **拆检索词**：把 idea 拆成几条英文检索式，覆盖不同说法和相邻方向。
2. **检索**：在 Semantic Scholar、arXiv、OpenAlex 上搜，合并去重。
3. **初筛**：按标题和摘要判断先读哪些。这一步只决定"值不值得读全文"，不判断"有没有人做过"。
4. **下载全文**：把开放获取的 PDF 下载到本地（检索结果里没挂链接的，按标题去 arXiv 找预印本），抽出文字并按页标上 `[p.N]`。
5. **精读、评质量**：逐篇读全文，写"它实际做了什么"，核对摘要和正文是否一致，先分清论文类型（实证、综述、立场文章、数据集等），再按这一类的标准分成高、中、低三档。读到的全文太少或者全是低质量，就停在这一步（见[证据不够时](#证据不够时)）。
6. **找空白**：根据精读笔记画出这个方向的版图，找出有论文证据支撑的空白。每个空白列出读过的论文里离它最近的几篇，和给你自己补查的检索式，不判断有没有人做过。读到的文献少，空白也少写。
7. **挖新方向**：从空白里挖出几个和你的原始 idea 有实质区别的方向。
8. **写 proposal（模型 A）**：给你的原始 idea 和每个新方向各写一份 proposal。
9. **元评审（模型 B、C、D…）**：换成别家公司的模型，各开一个新对话，只给 proposal 和论文材料，写 meta review：撞车、死因、可行性、最便宜的证伪实验，不打分、不下结论。
10. **汇总**：整理几位评审的共识和分歧，列出你必须亲自核实的说法。每篇被说撞车的论文有几家评审提到，由程序直接数，只有一家提到的标成不稳。
11. **出报告**：生成一个自包含的 HTML 报告，手机和电脑都能看。

拍板的是你。报告只负责把材料摆齐。

## 为什么这么设计

- **读全文，不只读摘要。** 很多 agent 查文献时只读摘要，可有的论文摘要写的和正文实际做的并不一样，光看摘要可能就把一个方向跳过了。所以精读和评审都基于全文。摘要夸大或者和正文矛盾的地方会标出来；摘要的说法没错、只是没交代适用范围的（比如只在部分模型上测过），单独列出，不算矛盾。
- **写的和审的不是同一家。** 模型都有点讨好用户，给自己写的东西打分也偏高。配置里给每个模型标上 `family`，写 proposal 的和做评审的是同一家时，程序会提醒你。
- **评审不打分，也不判断"有没有人做过"。** 模型判断"新不新、行不行"很不稳定，问法改几个字结论就可能变。有一篇预印本专门测过（Sternlicht 等，[arXiv 2610.02022](https://arxiv.org/abs/2610.02022)）：6 个模型判断研究 idea 新不新，提示词只改一句，有的模型在同一批配对上一半以上判得不一样；给模型查文献、加大推理预算，帮助都不大。所以只让它交出能核实的东西：哪篇论文、第几页、哪个实验。空白也一样，只列最近的论文和补查用的检索式。
- **文献质量只看证据。** "已发表 / 预印本"、引用数单独标出来；质量档位只看论文本身的实验和论证，而且按论文类型分别判断，综述、立场文章不会因为没做实验就被判成低质量。
- **证据不够就不往下编。** 只读到两三篇，或者读到的都是低质量论文时，硬让模型找空白、写 proposal，它也能写出一大堆，但多半站不住，还白花钱和时间。所以这时程序会停下来，告诉你怎么补文献。

## Skill 版

不想配 API key、不想装命令行工具的，可以用 `skill/idea-review/` 里的 skill：装进 Claude Code、Codex 这类 code agent，直接跟它说"帮我看看这个 idea"就行。

和命令行版的区别：

- 读论文、找空白、写 proposal 的是你正在用的 agent 自己，用的是它的订阅，不用另外配 API key。
- 检索、下载全文、检查格式、"证据不够就停"、调别家模型做元评审，由 skill 里的脚本完成，规则和命令行版一样。脚本只用 Python 标准库。
- 元评审调用本机装好的别家命令行工具（codex、gemini、claude 里去掉和 agent 同一家的），或者 OpenAI 兼容接口。一家都没有时用子代理代评，报告里会标明是同一家的模型。
- 运行目录里的文件格式和命令行版一样，报告样式也一样。

在 Claude Code 里安装：

```bash
cp -r skill/idea-review ~/.claude/skills/
```

只想在某个项目里用，就复制到那个项目的 `.claude/skills/` 下。其他支持 Agent Skills 的工具，放进它们各自的 skills 目录。需要能联网、有 Python 3.10 以上；装了 uv 最好（会自动装抽 PDF 文字用的 pypdf），没有的话装 pypdf 或 poppler 都行。

装好以后在 agent 里说"用 idea-review 帮我看看这个 idea：……"。精读那一步 agent 要读好几篇全文，比较费额度；默认精读 8 篇，可以让它调小。skill 的说明文件是中文写的，跟它说英文的话，笔记和报告会用英文写。

## 安装

需要 Python 3.11 以上和 [uv](https://docs.astral.sh/uv/)。

```bash
cd idea-review
uv sync
```

之后用 `uv run idea-review ...` 运行。想在任何目录直接用 `idea-review`，可以装成命令行工具：

```bash
uv tool install .
```

## 配置

```bash
idea-review init
```

会在当前目录生成两个文件：

- `config.toml`：正式配置。在 `[providers.xxx]` 里登记模型，在 `[roles]` 里指定每个环节用哪个。
- `config.mock.toml`：试跑配置。检索和下载是真的，模型换成假的，不花钱，用来看流程和报告长什么样。假模型拆不出真的检索词，试跑时要用 `--query` 自己给英文检索式（见下文）。

`idea-review init --lang en` 生成的是英文版配置：提示词、报告页面都用英文，模型也用英文写。中英文由配置里的 `[review] lang` 决定（`zh` 或 `en`），一次运行从头到尾用同一个版本，接着跑时不跟着改过的配置变。命令行里的日志和报错目前只有中文。

支持两种用法：调 API，或者用本机 code agent 的订阅。

| type | 适用 | 说明 |
| --- | --- | --- |
| `anthropic` | Claude | 走 Anthropic 官方 SDK，用结构化输出保证 JSON 格式 |
| `openai_compatible` | OpenAI、Gemini、DeepSeek、Kimi、通义千问、智谱 GLM、豆包，以及本机的 LiteLLM 等代理 | 走 OpenAI 官方 SDK，填 `base_url` 即可 |
| `claude_code` | Claude 订阅 | 调用本机的 `claude` 命令（Claude Code），不需要 API Key，见[下文](#用本机-code-agent-的订阅) |
| `codex` | ChatGPT 订阅 | 调用本机的 `codex exec`，不需要 API Key |
| `gemini_cli` | Gemini CLI 登录的账号 | 调用本机的 `gemini` 命令 |

API Key 不写进配置文件，只写环境变量的名字。运行前在终端里设置：

```bash
export ANTHROPIC_API_KEY=...
```

```bash
export OPENAI_API_KEY=...
```

Claude 如果已经用 `ant auth login` 登录过，可以把 `api_key_env` 删掉，SDK 会自动用登录的凭据。

模型名经常更新，示例配置里的名字仅供参考，以各家控制台里你能用的为准。配好以后先检查一遍：

```bash
idea-review check --ping
```

`--ping` 会给每个模型发一个很小的请求，确认接口和 JSON 输出都能用。

### 用本机 code agent 的订阅

已经订阅了 Claude 或 ChatGPT，可以不用 API，直接让 idea-review 调用本机装好的 Claude Code、Codex。先把命令行工具装好、登录，然后在 `config.toml` 里这样写：

```toml
[providers.claude_sub]
type = "claude_code"
model = "sonnet"          # 可选，不填用 Claude Code 的默认模型

[providers.codex_sub]
type = "codex"            # 可选 model，不填用 Codex 的默认模型

[roles]
planner = "claude_sub"
screener = "claude_sub"
reader = "claude_sub"
miner = "claude_sub"
writer = "claude_sub"
reviewers = ["codex_sub"]
chair = "codex_sub"
```

也可以和 API 混着用，比如精读和写作用订阅，评审再加一个走 API 的别家模型。

- `family` 不用填，默认分别是 `anthropic`、`openai`、`google`。
- 调用时会去掉 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY` 这几个环境变量，免得命令行工具改走 API 计费。
- 每次调用都在一个空的临时目录里新开一次会话，不读你的项目文件。Claude Code 关掉了所有工具、不加载你的设置；Codex 用只读沙箱，不读 `~/.codex/config.toml`（免得每次调用都启动你配置的 MCP 服务），要指定模型就在 idea-review 的配置里写 `model`。
- 默认同一个模型最多同时跑 2 个调用（`concurrency`），每次最多等 900 秒（`timeout`），都可以在配置里改。
- 订阅有用量上限。一次完整运行要调用几十次模型；Codex 每次调用还会带上它自己约 2 万 token 的系统提示，比走 API 费额度。
- `gemini_cli` 是按 Gemini CLI 的文档接的，还没有实际跑通过。Gemini CLI 如果配置成用 API Key，走的还是 API 计费。

## 使用

```bash
idea-review run "让大模型代替真人填社会调查问卷，看能不能复现真实人群的回答分布"
```

常用参数：

- `--query "英文检索式"`：自己给检索式，可以写多次，代替模型拆的检索词。你很清楚该搜哪些关键词时可以用；用 `config.mock.toml` 试跑时必须给。只能在新开运行时用。
- `--refs N`：精读篇数，下载全文并逐篇精读 N 篇。不写就用配置里的 `search.download`（默认 15）。
- `--candidates N`：候选篇数，检索去重后留 N 篇去初筛。不写就用配置里的 `search.max_candidates`（默认 80）。
- `--papers-dir 文件夹`：把你自己下载好的 PDF 一起交给它，这些论文一定会被精读。
- `--idea-file idea.txt`：idea 比较长时写在文件里。
- `--resume runs/某次运行`：中途失败了，或者改了配置想接着跑。已经完成的步骤会直接读结果，不会重复花钱。可以同时加 `--refs`、`--candidates` 调大数量，见下一节。
- `--force`：读到的全文太少或者全是低质量时，程序默认停在第 5 步；加上它照样往下跑，见[证据不够时](#证据不够时)。
- `--config config.mock.toml`：试跑，不花钱。要配合 `--query`：

  ```bash
  idea-review run "让大模型代替真人填社会调查问卷" --config config.mock.toml --query "LLM simulated survey respondents" --query "silicon sampling public opinion" --candidates 15 --refs 4
  ```

### 参考文献数量

```bash
idea-review run "你的 idea" --refs 25 --candidates 120
```

- **精读篇数**（`--refs`，范围 1–50）：真正拿到全文、逐篇精读的篇数。按初筛结果排队，先高相关、再中相关，低相关的不下载。有的论文下载失败（链接失效、拿到的是登录页），就往下顺延一篇。检索结果里没挂 PDF 链接的论文，会按标题去 arXiv 找一次预印本。高、中相关的候选都试完了还不够数，会从第一次检索存下的结果里再取一批候选去初筛、接着下载，最多扩到候选篇数的 3 倍，报告里会注明多取了几篇。`--papers-dir` 给的本地 PDF 另算，不占名额。
- **候选篇数**（`--candidates`，范围 5–300）：检索结果合并去重、粗排以后，留前 N 篇去初筛。初筛只看标题和摘要，比较便宜。
- 精读篇数不能多于候选篇数。数字超出范围，或者配置里写的不是整数，程序会直接报错并说明该改哪里。
- 这次用的数量会写进运行目录的 `meta.json`，报告"① 检索"一节会列出设定了多少篇、实际拿到多少篇。

精读篇数是最花钱的一项：每多读一篇，精读模型大约多用 2 万输入 token，后面写 proposal 和评审时给模型看的笔记也会变长。

**接着跑时改数量**（`--resume` 加 `--refs` / `--candidates`）：

- 不带这两个参数：沿用这个运行目录上次用的数量，`config.toml` 里改过的数不会生效。
- 调大精读篇数：已经下载、精读过的论文都保留，只补下载、补精读差的那几篇。只要补进了新的全文，第 6–10 步（找空白、挖新方向、写 proposal、元评审、汇总）就会按新的文献重做，这几步要再花一次钱。旧结果和旧报告移到运行目录的 `history/时间/` 下，不删。
- 调大候选篇数：从第一次检索时存下的完整结果（`02_search_results.json`）里接着往下取，不重新检索，已有候选的编号不变。新候选只补初筛；如果精读还没够数，会从新候选里补。精读已经够数时，后面的步骤不重做。想让新候选也有机会被精读，就同时调大 `--refs`。
- 调小：什么都不删，也不重做。
- `search.per_query` 只在第一次检索时用，接着跑时改了不生效。

```bash
idea-review run --resume runs/某次运行 --refs 25
```

### 证据不够时

精读完以后，如果读到的全文少于 3 篇（`[review] min_fulltexts`），或者读到的全是低质量，程序就停在第 5 步，不往下找空白、写 proposal。拿这么少或这么弱的文献去找空白，结果多半站不住，后面几步的钱和时间也就省下了。报告照样生成，里面有已经读到的文献、没拿到全文的论文，顶部写明为什么停、怎么补：

- 补文献：把论文 PDF 按编号放进这次运行的 `papers/`，再 `--resume`（见[输出](#输出)一节）。够数了就接着往下跑。
- 检索结果本身太少：换几条检索式（`--query`），或者调大 `search.per_query`，开一次新的运行。
- 确实想用现有的文献跑完：`--resume` 时加 `--force`。报告顶部会提醒"文献证据偏弱"。

精读篇数设得比门槛还小时（比如 `--refs 2`），门槛按精读篇数算。

往下跑时也会按证据多少收着写：找空白时，空白个数的上限跟读到的全文篇数走（最少 3 个，最多 8 个）；新方向的个数不超过空白个数；证据里没有一篇高、中质量论文的空白，报告里会单独标出来。

### 重新生成报告

只想重新生成报告（比如改了模板）：

```bash
idea-review report runs/某次运行
```

## 输出

每次运行一个目录，默认在 `runs/` 下：

```
runs/20261031-1540-让大模型代替真人填/
├── report.html          报告
├── meta.json            idea、语言、这次用的数量，以及全文不够时自动多取了几篇候选
├── papers/              下载的 PDF（P01.pdf …），也可以手动放进去
├── text/                抽出的全文文字
├── history/             接着跑时调大精读篇数，被替换下来的第 6–10 步旧结果
├── 01_plan.json         检索计划
├── 02_search_results.json 合并去重后的全部检索结果
├── 02_candidates.json   候选文献（全部检索结果的前 N 篇，加上全文不够时自动多取的）
├── 03_screen.json       初筛结果
├── 04_fulltext.json     全文下载情况
├── 05_reviews.json      逐篇精读和质量判断
├── 06_landscape.json    版图和空白
├── 07_seeds.json        新方向
├── 08_proposals.json    proposal
├── 09_meta_reviews.json 元评审
├── 10_chair.json        评审汇总
├── usage.json           各模型的 token 用量
└── run.log              运行日志
```

运行目录可以整个挪走、改名，或者拷到别的电脑上，再用 `--resume` 接着跑：里面记的文件路径都是相对这个目录的。

有些很相关的论文没有开放获取的 PDF，报告里会单独列出来。你可以手动下载，按编号命名（比如 `P07.pdf`）放进这次运行的 `papers/` 文件夹，再用 `--resume` 跑一次，不用删任何结果文件：

```bash
idea-review run --resume runs/某次运行
```

程序会找出 `papers/` 里新放进来、还没读过的 PDF，抽出全文、补精读，再按新的文献重做第 6–10 步（找空白到汇总），旧结果移到 `history/` 下。已经读过的论文不重读。

- 报告"其余候选"里的论文也一样，按编号放进去就会补读，不管初筛给的是什么档。
- 手动补的论文算在精读篇数里，报告会注明其中几篇是你手动补的。
- 程序下载到的是扫描版、抽不出文字时，用新文件覆盖同名的 PDF 再跑一次就行。

精读失败的论文（比如模型接口报错）不用手动处理，用 `--resume` 接着跑时会重读。只要有一篇读成功了，第 6–10 步同样会按新的文献重做，这几步要再花一次钱；都没读成功就什么都不重做。

## 提示词

每一步的提示词都在 `src/idea_review/prompts/` 里，一个文件一步，可以直接改；英文版在 `src/idea_review/prompts/en/`，文件名一样。文件里用一行 `---USER---` 分开 system 和 user 两部分，`$idea` 这样的是变量。报告的附录里也会附上这次用到的全部提示词。

| 文件 | 环节 |
| --- | --- |
| `plan_queries.md` | 拆检索词 |
| `screen.md` | 按摘要初筛 |
| `read_paper.md` | 精读全文、评质量（先分论文类型，再按这一类的标准定档）、核对摘要和正文（分"夸大或矛盾"和"没交代适用范围"两类） |
| `mine_gaps.md` | 画版图、找空白（`$max_gaps` 是空白个数的上限，程序按读到的全文篇数填） |
| `seeds.md` | 挖新方向 |
| `write_proposal.md` | 写 proposal（模型 A） |
| `meta_review.md` | 元评审（模型 B、C、D…） |
| `chair.md` | 汇总共识和分歧 |

程序自己拼进提示词的文字（论文信息、笔记摘要、JSON 格式要求等）在 `src/idea_review/texts.py`，发给模型的字段说明在 `src/idea_review/schemas.py`，都是中英文各一份。改提示词时，如果想让英文版也跟着变，记得两份一起改。

## 费用和时间

按默认配置（下载 15 篇全文、4 份 proposal、3 个评审模型）粗算，负责精读和写作的模型大约用 30 多万输入 token，每个评审模型大约 10 万。实际用量会写在报告附录里。整个流程通常十几分钟到半小时，主要花在检索限流和等模型回答上；日志里会记下每一步用了多久。

想省钱，可以把 `screener` 和 `planner` 换成便宜的模型，或者调小精读篇数（`--refs` 或 `search.download`）和 `review.ideas`，或者改用[订阅](#用本机-code-agent-的订阅)。文献证据不够时程序会停在第 5 步，写 proposal 和评审这几步最花钱，不会白花。

## 注意

- 你的 idea 和论文全文会发给你配置的模型服务商。还没公开的想法，自己掂量要不要发。
- 只下载开放获取的 PDF，仅供个人研究使用。
- Semantic Scholar 不带 key 时经常限流，程序会自动跳过它，只用另外两个来源。可以去官网免费申请一个 key，设置成 `S2_API_KEY`。
- 扫描版 PDF 抽不出文字，会标成"没读到全文"。
- 模型给出的任何论文和判断都要自己点开核实，尤其是"某某论文已经做过"这类说法。

## 测试

```bash
uv run pytest
```

测试不联网，用假的检索结果和 mock 模型把整条流程跑一遍。skill 版的脚本在 `tests/test_skill.py` 里测，评审用的命令行工具换成了假的。
