# idea-review: a literature review and multi-model review for your research idea

[中文](README.md) | English

Give it a one-sentence research idea, and it will:

1. **Plan queries**: turn the idea into several English search queries covering different phrasings and adjacent directions.
2. **Search**: search Semantic Scholar, arXiv and OpenAlex, then merge and deduplicate.
3. **Screen**: decide from titles and abstracts which papers to read first. This step only decides "is it worth reading in full", not "has someone already done this".
4. **Download full texts**: download open-access PDFs (for papers without a PDF link in the search results, look for an arXiv preprint by title), extract the text, and mark each page with `[p.N]`.
5. **Read and rate quality**: read each full text, write down "what it actually does", check the abstract against the body, decide the paper type (empirical, survey, position paper, dataset, etc.), and sort the papers into high, medium and low tiers by the standard for that type. If too few full texts were read, or all are low quality, stop here (see [When the evidence is not enough](#when-the-evidence-is-not-enough)).
6. **Find gaps**: map out the direction from the reading notes and find gaps backed by evidence from the papers. Fewer papers read means fewer gaps.
7. **Mine new directions**: dig out a few directions from the gaps that differ in substance from your original idea.
8. **Write proposals (Model A)**: write one proposal for your original idea and one for each new direction.
9. **Meta review (Models B, C, D…)**: switch to models from other companies, each in a fresh conversation, given only the proposal and the paper material. They write meta reviews covering overlap, causes of death, feasibility and the cheapest falsification experiment, with no scores and no verdict.
10. **Summarize**: compile where the reviewers agree and disagree, and list the claims you must verify yourself.
11. **Report**: produce a self-contained HTML report that reads well on phones and computers.

You make the call. The report just lays out the material.

## Why it is designed this way

- **Read full texts, not just abstracts.** Many agents read only abstracts when searching the literature, but some abstracts differ from what the body actually does, and reading abstracts alone can make you skip a direction. So reading and reviewing are based on full texts. Places where an abstract overclaims or contradicts the body are flagged; where the abstract's claim holds but it leaves out its scope (e.g. only tested on some models), that is listed separately and not counted as a contradiction.
- **The writer and the reviewers come from different companies.** Models tend to please the user and rate their own writing too kindly. Each model in the config is tagged with a `family`, and the program warns you when the proposal writer and a reviewer share one.
- **Reviewers do not score.** Models are unstable at judging "is it novel, will it work": change a few words in the question and the conclusion may flip. So they only hand over things you can verify: which paper, which page, which experiment.
- **Paper quality is judged on evidence only.** "Peer-reviewed / preprint" and citation counts are shown separately; the quality tier is based only on the paper's own experiments and arguments, judged by paper type, so a survey or position paper is not rated low just for having no experiments.
- **No evidence, no made-up analysis.** With only two or three papers read, or only low-quality ones, a model asked to find gaps and write proposals will still produce plenty, but most of it will not hold up, and it costs money and time. So the program stops here and tells you how to add more literature.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd idea-review
uv sync
```

Then run it with `uv run idea-review ...`. To use `idea-review` from any directory, install it as a command-line tool:

```bash
uv tool install .
```

## Configure

```bash
idea-review init --lang en
```

This creates two files in the current directory:

- `config.toml`: the real configuration. Register models under `[providers.xxx]` and choose which model handles each step under `[roles]`.
- `config.mock.toml`: a trial configuration. Search and downloads are real, but the models are mocks, so it costs nothing. Use it to see how the pipeline runs and what the report looks like. Mock models cannot plan real search queries, so give English queries yourself with `--query` when trying it out (see below).

With `--lang en`, the prompts and the report page are in English and the models write in English. The language is set by `[review] lang` in the config (`en` or `zh`). A run keeps the same language from start to finish, even if you change the config before resuming it. **Command-line logs and error messages are currently in Chinese only**, as are a few technical notes on failed downloads.

You can call APIs, or use the subscription of a code agent on your computer:

| type | For | Notes |
| --- | --- | --- |
| `anthropic` | Claude | Uses the official Anthropic SDK, with structured output to guarantee the JSON format |
| `openai_compatible` | OpenAI, Gemini, DeepSeek, and any other service with an OpenAI-compatible API, including a local proxy such as LiteLLM | Uses the official OpenAI SDK; just fill in `base_url` |
| `claude_code` | A Claude subscription | Runs the local `claude` command (Claude Code); no API key needed, see [below](#use-a-code-agent-subscription) |
| `codex` | A ChatGPT subscription | Runs the local `codex exec`; no API key needed |
| `gemini_cli` | The account Gemini CLI is logged in to | Runs the local `gemini` command |

API keys do not go in the config file; only the name of the environment variable does. Set them in your terminal before running:

```bash
export ANTHROPIC_API_KEY=...
```

```bash
export OPENAI_API_KEY=...
```

If you have already logged in to Claude with `ant auth login`, you can delete `api_key_env` and the SDK will use those credentials automatically.

Model names change often. The names in the example config are for reference only; use the ones available to you in each vendor's console. Once configured, check it first:

```bash
idea-review check --ping
```

`--ping` sends a very small request to each model to confirm that the API and the JSON output both work.

### Use a code agent subscription

If you subscribe to Claude or ChatGPT, idea-review can run the Claude Code or Codex installed on your computer instead of calling an API. Install the command-line tool and log in first, then write this in `config.toml`:

```toml
[providers.claude_sub]
type = "claude_code"
model = "sonnet"          # optional; leave out to use Claude Code's default model

[providers.codex_sub]
type = "codex"            # model is optional; leave out to use Codex's default model

[roles]
planner = "claude_sub"
screener = "claude_sub"
reader = "claude_sub"
miner = "claude_sub"
writer = "claude_sub"
reviewers = ["codex_sub"]
chair = "codex_sub"
```

You can also mix them with APIs, e.g. use a subscription for reading and writing and add a reviewer from another company through its API.

- No need to set `family`; it defaults to `anthropic`, `openai` and `google` respectively.
- The environment variables `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `GEMINI_API_KEY` are removed for these calls, so the tools do not switch to API billing.
- Each call starts a fresh session in an empty temporary folder and does not read your project files. Claude Code runs with all tools turned off and without loading your settings. Codex runs in a read-only sandbox and does not read `~/.codex/config.toml` (so it does not start the MCP servers you configured on every call); set `model` in the idea-review config to choose a model.
- By default at most 2 calls run at once per model (`concurrency`), and each call waits at most 900 seconds (`timeout`); both can be changed in the config.
- Subscriptions have usage limits. A full run makes dozens of model calls, and Codex adds its own system prompt of about 20,000 tokens to every call, so it uses up the quota faster than the API would.
- `gemini_cli` follows Gemini CLI's documentation but has not been run end to end yet. If Gemini CLI is set up with an API key, it is still billed as API usage.

## Usage

```bash
idea-review run "Use LLMs in place of human respondents for social surveys, and see whether they reproduce the response distributions of real populations"
```

Common options:

- `--query "English query"`: give search queries yourself (repeatable) instead of the ones the model plans. Useful when you know exactly which keywords to search; required when trying it out with `config.mock.toml`. Only for new runs.
- `--refs N`: number of papers read in full; download and read N full texts. Defaults to `search.download` in the config (15).
- `--candidates N`: number of candidates; keep N papers after deduplication for screening. Defaults to `search.max_candidates` in the config (80).
- `--papers-dir folder`: hand over PDFs you have already downloaded. These papers are always read.
- `--idea-file idea.txt`: put a longer idea in a file.
- `--resume runs/some-run`: continue a run that stopped halfway, or after changing the config. Finished steps are read from their saved results, so you do not pay for them again. You can add `--refs` and `--candidates` at the same time to raise the numbers; see the next section.
- `--force`: by default the program stops after step 5 when too few full texts were read or all are low quality; with this it carries on. See [When the evidence is not enough](#when-the-evidence-is-not-enough).
- `--config config.mock.toml`: a free trial run. Use it together with `--query`:

  ```bash
  idea-review run "Use LLMs in place of human respondents for social surveys" --config config.mock.toml --query "LLM simulated survey respondents" --query "silicon sampling public opinion" --candidates 15 --refs 4
  ```

### Number of references

```bash
idea-review run "your idea" --refs 25 --candidates 120
```

- **Papers read in full** (`--refs`, 1–50): the number of papers whose full text is actually obtained and read. Papers are queued by the screening result, high relevance first, then medium; low-relevance papers are not downloaded. If a download fails (a dead link, or a login page instead of a PDF), the next paper takes its place. For papers without a PDF link in the search results, the program looks for an arXiv preprint by title once. If all high- and medium-relevance candidates have been tried and the number is still not reached, more candidates are taken from the search results saved during the first search, screened and downloaded, up to 3 times the number of candidates; the report notes how many were added. Local PDFs from `--papers-dir` are counted separately and do not use up this quota.
- **Candidates** (`--candidates`, 5–300): after merging, deduplicating and rough ranking, the top N search results go on to screening. Screening only looks at titles and abstracts, so it is fairly cheap.
- The number read in full cannot exceed the number of candidates. If a number is out of range, or the config has something other than an integer, the program stops with an error that says where to fix it.
- The numbers used are written to `meta.json` in the run directory, and the "① Search" section of the report shows the setting and the actual count.

Papers read in full are the most expensive part: each extra paper costs the reading model roughly 20,000 more input tokens, and the notes shown to the models when writing proposals and reviewing also grow longer.

**Changing the numbers when resuming** (`--resume` with `--refs` / `--candidates`):

- Without these options: the run keeps the numbers it used last time; changes to `config.toml` do not apply.
- Raising the number read in full: papers already downloaded and read are kept; only the missing ones are downloaded and read. As soon as any new full text is added, steps 6–10 (find gaps, mine new directions, write proposals, meta review, summarize) are redone with the new literature, and you pay for those steps again. The old results and old report are moved to `history/<time>/` in the run directory, not deleted.
- Raising the number of candidates: more are taken from the full search results saved during the first search (`02_search_results.json`), without searching again; existing candidates keep their IDs. New candidates are only screened. If the number read in full has not been reached yet, new candidates fill it; if it has, the later steps are not redone. To give new candidates a chance to be read, raise `--refs` as well.
- Lowering a number: nothing is deleted and nothing is redone.
- `search.per_query` is only used for the first search; changing it when resuming has no effect.

```bash
idea-review run --resume runs/some-run --refs 25
```

### When the evidence is not enough

After reading, if fewer than 3 full texts were read (`[review] min_fulltexts`), or all of them are low quality, the program stops after step 5 and does not find gaps or write proposals. Gaps built on so little or such weak literature are unlikely to hold up, and this also saves the money and time of the later steps. The report is still produced, with the papers read so far and those whose full texts were not obtained, and at the top it says why the run stopped and how to add more:

- Add literature: put the papers' PDFs into the run's `papers/` folder, named by ID, and `--resume` (see [Output](#output)). Once there are enough, the run continues.
- If the search itself returned too few papers: start a new run with different queries (`--query`) or a larger `search.per_query`.
- If you do want to finish with the literature you have: add `--force` when resuming. The top of the report will warn that the evidence is weak.

If the number read in full is set below the threshold (e.g. `--refs 2`), that number is used as the threshold.

The later steps also scale with the evidence: the maximum number of gaps follows the number of full texts read (at least 3, at most 8); there are never more new directions than gaps; and gaps whose evidence includes no high- or medium-quality paper are marked in the report.

### Regenerate the report

To regenerate only the report (for example after editing the template):

```bash
idea-review report runs/some-run
```

## Output

Each run gets its own directory, under `runs/` by default:

```
runs/20261031-1540-Use-LLMs-in-place-of/
├── report.html          the report
├── meta.json            the idea, the language, the numbers used, and how many candidates were added automatically
├── papers/              downloaded PDFs (P01.pdf …); you can also put PDFs here yourself
├── text/                extracted full texts
├── history/             old results of steps 6–10, replaced after reading more papers on resume
├── 01_plan.json         search plan
├── 02_search_results.json all search results after deduplication
├── 02_candidates.json   candidates (the top N of all search results, plus any added automatically when full texts were short)
├── 03_screen.json       screening results
├── 04_fulltext.json     full-text download status
├── 05_reviews.json      reading notes and quality ratings
├── 06_landscape.json    landscape and gaps
├── 07_seeds.json        new directions
├── 08_proposals.json    proposals
├── 09_meta_reviews.json meta reviews
├── 10_chair.json        summary of the reviews
├── usage.json           token usage per model
└── run.log              run log
```

You can move or rename a run directory, or copy it to another computer, and still `--resume` it: the file paths it records are relative to the directory itself.

Some highly relevant papers have no open-access PDF; the report lists them separately. You can download them yourself, name each by its ID (e.g. `P07.pdf`), put them in the run's `papers/` folder, and run `--resume` again. There is no need to delete any result files:

```bash
idea-review run --resume runs/some-run
```

The program finds PDFs newly put into `papers/` that have not been read yet, extracts the full texts, reads them, and then redoes steps 6–10 (from finding gaps through the summary) with the new literature, moving the old results to `history/`. Papers already read are not read again.

- The same works for papers in the report's "other candidates" list: put them in by ID and they will be read, whatever tier screening gave them.
- Papers you add yourself count toward the number read in full, and the report notes how many of them you added.
- If the PDF the program downloaded is a scanned copy with no extractable text, overwrite it with a new file of the same name and run again.

Papers whose reading failed (for example because of an API error) need no manual handling: they are read again when you `--resume`. If at least one of them succeeds, steps 6–10 are redone with the new literature as well, and you pay for those steps again; if none succeeds, nothing is redone.

## Prompts

The English prompts for each step are in `src/idea_review/prompts/en/`, one file per step, and you can edit them directly (the Chinese versions are in `src/idea_review/prompts/`, with the same file names). A line `---USER---` separates the system part from the user part; things like `$idea` are variables. The report's appendix includes all the prompts used for that run.

| File | Step |
| --- | --- |
| `plan_queries.md` | Plan search queries |
| `screen.md` | Screen by abstract |
| `read_paper.md` | Read full texts, rate quality (decide the paper type first, then judge by the standard for that type), check abstract against body (in two kinds: "overclaims or contradictions" and "scope left out") |
| `mine_gaps.md` | Map the landscape, find gaps (`$max_gaps` is the maximum number of gaps, filled in by the program from the number of full texts read) |
| `seeds.md` | Mine new directions |
| `write_proposal.md` | Write proposals (Model A) |
| `meta_review.md` | Meta review (Models B, C, D…) |
| `chair.md` | Summarize agreement and disagreement |

Text the program itself splices into the prompts (paper information, note digests, JSON format instructions, etc.) is in `src/idea_review/texts.py`, and the field descriptions sent to the models are in `src/idea_review/schemas.py`. Both come in a Chinese and an English version; if you change one, change the other too.

## Cost and time

With the default config (15 full texts, 4 proposals, 3 reviewer models), as a rough estimate the reading and writing model uses a bit over 300,000 input tokens, and each reviewer model about 100,000. Actual usage is listed in the report's appendix. A full run usually takes ten-odd minutes to half an hour, mostly spent on search rate limits and waiting for the models; the log records how long each step took.

To save money, switch `screener` and `planner` to cheaper models, lower the number read in full (`--refs` or `search.download`) and `review.ideas`, or use a [subscription](#use-a-code-agent-subscription) instead. When the evidence is not enough the program stops after step 5, so the most expensive steps, writing proposals and reviewing them, are not wasted.

## Notes

- Your idea and the full texts of papers are sent to the model providers you configure. Think about whether to send ideas you have not made public yet.
- Only open-access PDFs are downloaded, for personal research use only.
- Without a key, Semantic Scholar often rate-limits requests; the program then skips it automatically and uses the other two sources. You can apply for a free key on its website and set it as `S2_API_KEY`.
- Scanned PDFs have no extractable text and are marked as "not read in full".
- Verify every paper and judgment the models give you by opening the source yourself, especially claims like "paper X has already done this".

## Tests

```bash
uv run pytest
```

The tests do not use the network: they run the whole pipeline with fake search results and mock models.
