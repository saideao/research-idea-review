"""命令行入口：idea-review init / check / run / report。"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import replace
from datetime import datetime
from importlib import resources
from pathlib import Path

from .config import ConfigError, check_counts, load_config
from .llm import LLMError, ProviderPool, UsageTracker
from .pipeline import Pipeline, PipelineError
from .schemas import QueryPlan
from .texts import LANGS, TEXTS


# 命令行参数 → config.toml 里 [search] 的哪一项
COUNT_FLAGS = {"refs": "download", "candidates": "max_candidates"}


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+|[一-鿿]", text)[:12]
    # 汉字直接连起来，英文单词之间加短横线，太长时在词的边界截断
    slug = "".join(("-" if i and w.isascii() and words[i - 1].isascii() else "") + w for i, w in enumerate(words))
    if len(slug) > 24 and slug[24:25].isascii() and slug[24:25].isalnum() and "-" in slug[:24]:
        slug = slug[:24].rsplit("-", 1)[0]
    return (slug or "idea")[:24]


def _count(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"要填整数，现在是 {text!r}") from None


def apply_counts(cfg, args, saved: dict, meta_path: Path | None = None) -> None:
    """定这次的候选篇数和精读篇数：命令行参数优先，其次是这个运行目录上次用的，最后是 config.toml。"""
    updates, labels = {}, {}
    for arg, key in COUNT_FLAGS.items():
        value = getattr(args, arg)
        if value is not None:
            updates[key], labels[key] = value, f"--{arg}"
        elif key in saved:
            updates[key], labels[key] = saved[key], f"{meta_path} 里的 search.{key}"
    cfg.search = replace(cfg.search, **updates)
    check_counts(cfg.search, labels)


def cmd_init(args) -> int:
    suffix = "" if args.lang == "zh" else f".{args.lang}"
    for source, name in ((f"config.example{suffix}.toml", "config.toml"), (f"config.mock{suffix}.toml", "config.mock.toml")):
        target = Path(args.dir) / name
        if target.exists():
            print(f"已存在，跳过：{target}")
            continue
        src = resources.files("idea_review").joinpath("examples", source)
        target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已生成：{target}")
    print("下一步：编辑 config.toml，填好模型和环境变量名，然后运行 `idea-review check`。")
    return 0


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    print(f"配置文件：{cfg.path}")
    for role in ("planner", "screener", "reader", "miner", "writer", "chair"):
        print(f"  {role:<9} → {cfg.provider(role).describe()}")
    print(f"  reviewers → {'、'.join(p.describe() for p in cfg.reviewer_providers())}")
    for w in cfg.warnings():
        print(f"注意：{w}")
    if not args.ping:
        print("加上 --ping 会给每个模型发一个很小的请求，确认能用（走 API 的会产生少量费用，走订阅的用掉一点额度）。")
        return 0

    async def ping() -> int:
        pool = ProviderPool(cfg.providers, UsageTracker())
        t = TEXTS[cfg.review.lang]
        bad = 0
        for name in cfg.providers:
            try:
                plan = await pool.get(name).structured(t["ping_system"], t["ping_user"], QueryPlan, retries=0,
                                                       lang=cfg.review.lang)
                print(f"  ✓ {name}：{plan.search_queries[0]}")
            except LLMError as e:
                bad += 1
                print(f"  ✗ {name}：{e}")
        return 1 if bad else 0
    return asyncio.run(ping())


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    if args.resume:
        run_dir = Path(args.resume)
        meta = run_dir / "meta.json"
        if not meta.exists():
            print(f"{run_dir} 不是一个运行目录（没有 meta.json）")
            return 2
        saved = json.loads(meta.read_text(encoding="utf-8"))
        idea = saved["idea"]
        papers_dir = saved.get("papers_dir")
        papers_dir = Path(papers_dir) if papers_dir else None
        queries = saved.get("queries") or []
        # 一次运行从头到尾用同一个语言版本，不跟着改过的配置变
        cfg.review.lang = saved.get("lang", cfg.review.lang)
        cfg.review.language = saved.get("language", cfg.review.language)
        if args.query:
            print("--query 只能在新开运行时用。已有运行目录的检索式记在 01_plan.json 里。")
            return 2
        counts_from = (saved.get("search") or {}, meta)
    else:
        idea = Path(args.idea_file).read_text(encoding="utf-8") if args.idea_file else (args.idea or "")
        if not idea.strip():
            print("请给出 idea：idea-review run \"你的 idea\"，或者 --idea-file idea.txt")
            return 2
        name = args.name or f"{datetime.now():%Y%m%d-%H%M}-{_slug(idea)}"
        run_dir = Path(args.runs_dir) / name
        if run_dir.exists() and any(run_dir.iterdir()):
            print(f"{run_dir} 已经存在。要接着跑，用 --resume {run_dir}")
            return 2
        papers_dir = Path(args.papers_dir).resolve() if args.papers_dir else None
        if papers_dir and not papers_dir.is_dir():
            print(f"--papers-dir 不是文件夹：{papers_dir}")
            return 2
        queries = [q.strip() for q in args.query or [] if q.strip()]
        if not queries and cfg.provider("planner").type == "mock":
            print("mock 模型不会真的拆检索词，直接检索会搜不到论文。用 mock 配置试跑时，请用 --query 给几条英文检索式，比如：\n"
                  "  idea-review run \"你的 idea\" --config config.mock.toml "
                  "--query \"LLM simulated survey respondents\" --query \"silicon sampling public opinion\"")
            return 2
        counts_from = ({}, None)
    try:
        apply_counts(cfg, args, *counts_from)
    except ConfigError as e:
        print(f"数量设置有问题：{e}")
        return 2
    print(f"运行目录：{run_dir}")
    pipeline = Pipeline(cfg, run_dir, idea, papers_dir, queries, force=args.force)
    try:
        out = asyncio.run(pipeline.run())
    except PipelineError as e:
        print(f"停下了：{e}\n已经完成的步骤都保存在 {run_dir}，修好问题后用 --resume 接着跑。")
        return 1
    if pipeline.stopped:
        print(f"文献证据不够，停在了第 5 步。报告里有已经读到的文献和补文献的办法：{out}")
        return 0
    print(f"报告已生成：{out}")
    return 0


def cmd_report(args) -> int:
    from .report import build_report
    cfg = load_config(args.config)
    out = build_report(Path(args.run_dir), cfg)
    print(f"报告已生成：{out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="idea-review", description="给研究 idea 做一次文献调研加多模型评审，输出 HTML 报告。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="在当前目录生成 config.toml 和 config.mock.toml")
    p.add_argument("--dir", default=".")
    p.add_argument("--lang", choices=LANGS, default="zh", help="配置文件的版本：zh 中文 / en 英文（提示词和报告也用英文）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="检查配置；加 --ping 实际调用每个模型")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--ping", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="跑完整流程")
    p.add_argument("idea", nargs="?", help="一两句话描述你的 idea")
    p.add_argument("--idea-file", help="从文件读 idea")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--name", help="运行目录名，默认用时间和 idea 开头")
    p.add_argument("--papers-dir", help="你自己下载好的 PDF 所在文件夹，会全部精读")
    p.add_argument("--query", action="append", metavar="检索式",
                   help="自己给英文检索式，可以写多次，代替模型拆的检索词。用 mock 配置试跑时必须给")
    p.add_argument("--refs", type=_count, metavar="N",
                   help="精读篇数：下载全文并逐篇精读 N 篇，覆盖配置里的 search.download")
    p.add_argument("--candidates", type=_count, metavar="N",
                   help="候选篇数：检索去重后留 N 篇去初筛，覆盖配置里的 search.max_candidates")
    p.add_argument("--resume", help="接着跑一个已有的运行目录；可以同时用 --refs / --candidates 调大数量")
    p.add_argument("--force", action="store_true",
                   help="读到的全文太少或者全是低质量时，默认停在第 5 步；加上这个照样往下找空白、写 proposal")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="根据已有结果重新生成 HTML 报告")
    p.add_argument("run_dir")
    p.add_argument("--config", default="config.toml")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"配置有问题：{e}")
        return 2
    except KeyboardInterrupt:
        print("\n已中断。完成的步骤都已保存，可以用 --resume 接着跑。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
