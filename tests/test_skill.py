"""skill 版（skill/idea-review）的离线测试：脚本只用标准库，模型的部分用命令行版 mock 出来的数据代替。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from test_pipeline import CJK, MockProvider_sample, fake_fetch, fake_search

SCRIPTS = Path(__file__).resolve().parent.parent / "skill" / "idea-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ir  # noqa: E402
import report as skill_report  # noqa: E402


def run_ir(*args: str) -> int:
    return ir.main(list(args))


def mock_json(schema_name: str) -> dict:
    """按 schema 造一份格式正确的数据（和命令行版的 mock 模型一样）。"""
    import asyncio
    from idea_review.config import ProviderConfig
    from idea_review.llm import MockProvider, UsageTracker, strict_schema
    from idea_review import schemas
    provider = MockProvider(ProviderConfig(name="m", type="mock", model="m", family="m"), UsageTracker())
    model = getattr(schemas, schema_name)
    return json.loads(asyncio.run(provider._complete("", "P01 P02", strict_schema(model), schema_name, "zh")))


def note(tier: str = "medium") -> dict:
    data = json.loads(MockProvider_sample("PaperReview"))
    data["quality"]["tier"] = tier
    return data


def make_run(tmp_path: Path, n: int = 6, refs: int = 3, ok: tuple[str, ...] = ("P01", "P02", "P03")) -> Path:
    """建一个走到精读之前的运行目录：n 篇候选全是 high，ok 里的拿到了全文。"""
    run = tmp_path / "run"
    assert run_ir("init", str(run), "--idea", "让大模型代替真人填问卷", "--writer", "anthropic", "--refs", str(refs)) == 0
    (run / "01_plan.json").write_text(json.dumps(mock_json("QueryPlan"), ensure_ascii=False), encoding="utf-8")
    papers = [ir.new_paper(paper_id=f"P{i:02d}", title=f"Paper number {i} about surveys", year=2024, venue="ICLR",
                           peer_reviewed=True, abstract=f"Abstract {i}", pdf_urls=[f"https://example.org/{i}.pdf"],
                           sources=["openalex"]) for i in range(1, n + 1)]
    for name in ("02_candidates", "02_search_results"):
        (run / f"{name}.json").write_text(json.dumps(papers), encoding="utf-8")
    (run / "03_screen.json").write_text(json.dumps([{"paper_id": p["paper_id"], "relevance": "high", "reason": "r"} for p in papers]))
    (run / "text").mkdir()
    fulltexts = {}
    for p in papers:
        pid = p["paper_id"]
        if pid in ok:
            (run / "text" / f"{pid}.txt").write_text("[p.1]\n" + "body text " * 400, encoding="utf-8")
            fulltexts[pid] = {"paper_id": pid, "status": "ok", "pdf_path": None, "text_path": f"text/{pid}.txt", "chars": 4000,
                              "pages": 1, "note": "", "manual": False}
        else:
            fulltexts[pid] = {"paper_id": pid, "status": "no_pdf", "pdf_path": None, "text_path": None, "chars": 0, "pages": 0,
                              "note": "没有开放获取的 PDF，建议手动下载", "manual": False}
    (run / "04_fulltext.json").write_text(json.dumps(fulltexts, ensure_ascii=False), encoding="utf-8")
    return run


def write_notes(run: Path, tiers: dict[str, str]) -> None:
    (run / "notes").mkdir(exist_ok=True)
    for pid, tier in tiers.items():
        (run / "notes" / f"{pid}.json").write_text(json.dumps(note(tier), ensure_ascii=False), encoding="utf-8")


def fake_cli(tmp_path: Path, name: str, body: str) -> None:
    """在 PATH 前面放一个假的命令行工具。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!{sys.executable}\nimport json, os, sys\nstdin = sys.stdin.read()\nargv = sys.argv[1:]\n"
                      f"open({str(tmp_path / 'calls.jsonl')!r}, 'a').write(json.dumps({{'argv': argv, 'cwd': os.getcwd(), "
                      f"'has_key': 'OPENAI_API_KEY' in os.environ}}) + '\\n')\n{body}\n", encoding="utf-8")
    script.chmod(0o755)


@pytest.fixture
def only_fake_clis(tmp_path, monkeypatch):
    """PATH 里只留假的命令行工具，不碰本机真的 claude / codex / gemini。"""
    (tmp_path / "bin").mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    for key in ir.API_ENV:
        monkeypatch.delenv(key, raising=False)


def test_skill_report_renders_a_cli_run(tmp_path, monkeypatch):
    """skill 的文件格式和命令行版一样：命令行版的运行目录能直接出报告，格式检查也都通过。"""
    import asyncio
    from importlib import resources
    from idea_review import pipeline as pl
    from idea_review.config import load_config
    monkeypatch.setattr(pl, "search_all", fake_search)
    monkeypatch.setattr(pl, "fetch_fulltexts", fake_fetch)
    for lang, name, idea in (("zh", "config.mock.toml", "让大模型代替真人填问卷"), ("en", "config.mock.en.toml", "LLMs as survey respondents")):
        cfg_path = tmp_path / name
        cfg_path.write_text(resources.files("idea_review").joinpath("examples", name).read_text(encoding="utf-8"), encoding="utf-8")
        run = tmp_path / f"run-{lang}"
        asyncio.run(pl.Pipeline(load_config(cfg_path), run, idea, queries=["q"]).run())
        for r in json.loads((run / "05_reviews.json").read_text(encoding="utf-8")):
            assert ir.check(r["review"], "PaperReview") == []
        for m in json.loads((run / "09_meta_reviews.json").read_text(encoding="utf-8")):
            assert ir.check(m["review"], "MetaReview") == []
        assert_theme_toggle((run / "report.html").read_text(encoding="utf-8"), lang)
        assert skill_report.main([str(run)]) == 0
        html = (run / "report.html").read_text(encoding="utf-8")
        assert ("元评审" if lang == "zh" else "Meta reviews") in html
        assert_theme_toggle(html, lang)
        if lang == "en":
            assert not CJK.search(html)


def assert_theme_toggle(html, lang):
    """报告能切浅色/深色：按钮在导航栏里，记住选择的脚本在 <body> 之前，深色样式能被手动选中。"""
    assert 'id="theme-toggle"' in html and ("切到深色" if lang == "zh" else "Dark mode") in html
    assert html.index("idea-review-theme") < html.index("<body>")
    assert ':root[data-theme="dark"]' in html and ':root:not([data-theme="light"])' in html


def test_skill_assets_match_the_cli_templates():
    """skill 版的样式和切换脚本是命令行版的副本，只有第一行注释不同。"""
    templates = SCRIPTS.parent.parent.parent / "src" / "idea_review" / "templates"
    for cli_name, skill_name in (("_style.css", "report.css"), ("_theme.js", "report.js")):
        cli_text = (templates / cli_name).read_text(encoding="utf-8").split("\n", 1)[1]
        skill_text = (SCRIPTS.parent / "assets" / skill_name).read_text(encoding="utf-8").split("\n", 1)[1]
        assert cli_text == skill_text, skill_name


def test_skill_report_lists_closest_papers_and_counts_overlaps(tmp_path, monkeypatch):
    """skill 报告和命令行版一样：空白只列最近的论文和检索式，撞车论文由程序数几家评审提到。旧格式的空白照样显示。"""
    import asyncio
    from importlib import resources
    from idea_review import pipeline as pl
    from idea_review.config import load_config
    monkeypatch.setattr(pl, "search_all", fake_search)
    monkeypatch.setattr(pl, "fetch_fulltexts", fake_fetch)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(resources.files("idea_review").joinpath("examples", "config.mock.toml").read_text(encoding="utf-8"), encoding="utf-8")
    run = tmp_path / "run"
    asyncio.run(pl.Pipeline(load_config(cfg_path), run, "让大模型代替真人填问卷", queries=["q"]).run())
    assert skill_report.main([str(run)]) == 0
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "<h4>读过的论文里最近的</h4>" in html and "<b>自己补查</b>" in html and "<b>撞车风险</b>" not in html
    assert "程序按 2 家评审的意见统计" in html and "2 家里" in html

    landscape = json.loads((run / "06_landscape.json").read_text(encoding="utf-8"))
    for g in landscape["gaps"]:
        del g["closest_read"], g["check_queries"]
        g["already_done_risk"] = "旧版本写的撞车风险"
    (run / "06_landscape.json").write_text(json.dumps(landscape, ensure_ascii=False), encoding="utf-8")
    assert skill_report.main([str(run)]) == 0
    assert "<b>撞车风险</b>旧版本写的撞车风险" in (run / "report.html").read_text(encoding="utf-8")

    papers = {"P01": {"title": "Paper one"}, "P02": {"title": "Paper two"}}
    item = lambda pid, title: {"paper_id": pid, "title": title}  # noqa: E731
    metas = [{"reviewer": "b", "review": {"overlap": [item("P01", "x"), item("P02", "x"), item("external", "Survey Bots: A Study")]}},
             {"reviewer": "c", "review": {"overlap": [item("P01", "y"), item("external", "survey bots - a study")]}},
             {"reviewer": "d", "review": None, "error": "超时"}]
    n, rows = skill_report.overlap_tally(metas, papers)
    assert n == 2
    assert [(r["paper_id"], r["title"], r["by"]) for r in rows] == [
        ("P01", "Paper one", ["b", "c"]), ("", "Survey Bots: A Study", ["b", "c"]), ("P02", "Paper two", ["b"])]


def test_closest_papers_must_have_been_read(tmp_path, capsys):
    run = make_run(tmp_path)
    write_notes(run, {"P01": "high", "P02": "medium", "P03": "low"})
    landscape = mock_json("Landscape")
    landscape["gaps"][0]["closest_read"] = [{"paper_id": "P05", "how_close": "近在哪 [p.2]"}]
    (run / "06_landscape.json").write_text(json.dumps(landscape, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    run_ir("next", str(run))
    assert "引用了没精读过的论文 P05" in capsys.readouterr().out


def test_check_points_at_the_wrong_fields():
    bad = note()
    bad["quality"]["tier"] = "great"
    del bad["method"]
    bad["key_results"] = "x"
    errors = ir.check(bad, "PaperReview")
    assert any("缺少字段 method" in e for e in errors)
    assert any("key_results" in e and "array" in e for e in errors)
    assert any("tier" in e and "great" in e for e in errors)


def test_next_stops_at_the_evidence_gate_until_forced(tmp_path, capsys):
    run = make_run(tmp_path, ok=("P01", "P02"))
    capsys.readouterr()
    run_ir("next", str(run))
    assert "精读这 2 篇" in capsys.readouterr().out

    write_notes(run, {"P01": "high", "P02": "medium"})
    run_ir("next", str(run))
    out = capsys.readouterr().out
    assert "停在第 5 步" in out and "只读到 2 篇全文，少于 3 篇" in out and "06_landscape" not in out
    assert skill_report.main([str(run)]) == 0
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "文献证据不够，停在了第 5 步" in html and "很相关，但没读到全文" in html and "P03" in html

    run_ir("next", str(run), "--force")
    out = capsys.readouterr().out
    assert "06_landscape.json" in out and "空白最多 3 个" in out


def test_all_low_quality_papers_also_stop(tmp_path, capsys):
    run = make_run(tmp_path)
    write_notes(run, {"P01": "low", "P02": "low", "P03": "low"})
    capsys.readouterr()
    run_ir("next", str(run))
    assert "都是低质量" in capsys.readouterr().out


def test_bad_notes_are_reported_and_not_merged(tmp_path, capsys):
    run = make_run(tmp_path)
    write_notes(run, {"P01": "high", "P02": "high"})
    (run / "notes" / "P03.json").write_text('{"problem": "x"}', encoding="utf-8")
    capsys.readouterr()
    run_ir("next", str(run))
    out = capsys.readouterr().out
    assert "notes/P03.json 的格式有问题" in out and "缺少字段" in out
    assert [r["paper_id"] for r in json.loads((run / "05_reviews.json").read_text())] == ["P01", "P02"]


def test_fetch_takes_more_candidates_when_downloads_run_out(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"
    run_ir("init", str(run), "--idea", "x", "--writer", "openai", "--candidates", "5", "--refs", "3")
    pool = [ir.new_paper(paper_id="", title=f"Paper {i}", pdf_urls=[f"https://example.org/{i}.pdf"]) for i in range(1, 21)]
    cands = [dict(p, paper_id=f"P{i:02d}") for i, p in enumerate(pool[:5], start=1)]
    (run / "02_search_results.json").write_text(json.dumps(pool))
    (run / "02_candidates.json").write_text(json.dumps(cands))
    (run / "03_screen.json").write_text(json.dumps([{"paper_id": p["paper_id"], "relevance": "high", "reason": "r"} for p in cands]))
    monkeypatch.setattr(ir, "download", lambda p, dest: None if p["title"] in ("Paper 2", "Paper 7") else ir.NO_LINK)
    monkeypatch.setattr(ir, "read_pdf", lambda run_, pid, pdf: {"paper_id": pid, "status": "ok", "pdf_path": None,
                                                               "text_path": f"text/{pid}.txt", "chars": 3000, "pages": 2,
                                                               "note": "", "manual": False})
    capsys.readouterr()
    assert run_ir("fetch", str(run)) == 0
    out = capsys.readouterr().out
    assert "又取了 10 篇候选（P06–P15）" in out
    assert len(json.loads((run / "02_candidates.json").read_text())) == 15
    assert "P06" in (run / "screen_todo.md").read_text(encoding="utf-8")
    assert json.loads((run / "meta.json").read_text())["expanded"] == 10

    assert run_ir("fetch", str(run)) == 1   # 新候选没初筛之前不下载
    assert "还有 10 篇候选没初筛" in capsys.readouterr().err
    screen = json.loads((run / "03_screen.json").read_text())
    screen += [{"paper_id": f"P{i:02d}", "relevance": "medium", "reason": "r"} for i in range(6, 16)]
    (run / "03_screen.json").write_text(json.dumps(screen))
    assert run_ir("fetch", str(run)) == 0
    out = capsys.readouterr().out
    assert "只拿到 2 篇全文" in out and "到了上限" in out   # 只有 Paper 2 和 7 能下载；候选已经到 5 × 3 = 15 篇


def test_arxiv_atom_is_parsed_without_the_xml_library():
    xml = (b'<feed><title>query</title><entry><id>http://arxiv.org/abs/2209.06899v2</id><published>2022-09-14T00:00:00Z</published>'
           b'<title>Out of One, Many:\n  Using Language Models</title><summary> We &amp; they </summary>'
           b'<author><name>A. One</name></author><author><name>B. Two</name></author>'
           b'<arxiv:journal_ref xmlns:arxiv="http://arxiv.org/schemas/atom">Political Analysis</arxiv:journal_ref></entry></feed>')
    [e] = ir.arxiv_entries(xml)
    assert e["title"] == "Out of One, Many: Using Language Models" and e["summary"] == "We & they"
    assert e["authors"] == ["A. One", "B. Two"] and e["journal_ref"] == "Political Analysis" and e["doi"] is None
    assert ir.norm_arxiv(e["id"]) == "2209.06899"


def walk_to_proposals(tmp_path: Path, capsys) -> Path:
    run = make_run(tmp_path)
    write_notes(run, {"P01": "high", "P02": "medium", "P03": "low"})
    landscape = mock_json("Landscape")
    landscape["gaps"] = landscape["gaps"] * 3   # 6 个，超过上限 3 个
    (run / "06_landscape.json").write_text(json.dumps(landscape, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    run_ir("next", str(run))
    out = capsys.readouterr().out
    assert "只留前 3 个" in out and "挖 2 个新方向" in out
    seeds = mock_json("IdeaSeeds")
    (run / "07_seeds.json").write_text(json.dumps(seeds, ensure_ascii=False), encoding="utf-8")
    run_ir("next", str(run))
    assert len(json.loads((run / "07_seeds.json").read_text())["seeds"]) == 2
    assert "还缺 I1、I2、I3" in capsys.readouterr().out
    proposal = mock_json("Proposal")
    proposal["closest_work"][0]["paper_id"] = "P01"
    seeds = json.loads((run / "07_seeds.json").read_text())["seeds"]
    records = [{"proposal_id": "I1", "origin": "original", "seed": None, "error": "", "proposal": proposal}]
    records += [{"proposal_id": f"I{i}", "origin": "seed", "seed": s, "error": "", "proposal": proposal} for i, s in enumerate(seeds, start=2)]
    (run / "08_proposals.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return run


CODEX_OK = f"""
review = {json.dumps(mock_json("MetaReview"), ensure_ascii=False)!r}
out = argv[argv.index("-o") + 1]
open(out, "w").write(review)
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 1000, "output_tokens": 200}}}}))
"""


def test_review_calls_other_companies_and_report_is_complete(tmp_path, capsys, only_fake_clis, monkeypatch):
    run = walk_to_proposals(tmp_path, capsys)
    fake_cli(tmp_path, "codex", CODEX_OK)
    fake_cli(tmp_path, "claude", "print('should not be called')")   # 和写 proposal 的同一家，不该被选上
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    run_ir("next", str(run))
    out = capsys.readouterr().out
    assert "--reviewer codex" in out and "--reviewer claude" not in out

    assert run_ir("review", str(run)) == 0
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 3 and all(c["argv"][0] == "exec" and not c["has_key"] for c in calls)
    assert all(Path(c["cwd"]).name.startswith("idea-review-") for c in calls)   # 在空的临时目录里跑
    metas = json.loads((run / "09_meta_reviews.json").read_text())
    assert {m["proposal_id"] for m in metas if m["review"]} == {"I1", "I2", "I3"}
    assert json.loads((run / "usage.json").read_text())["codex"]["calls"] == 3

    run_ir("next", str(run))
    assert "运行 report.py" in capsys.readouterr().out   # 每份只有一位评审，不用汇总
    assert skill_report.main([str(run)]) == 0
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "codex" in html and "停在了第 5 步" not in html and 'id="gap-G3"' in html and 'id="gap-G4"' not in html


def test_two_reviewers_need_a_chair_summary(tmp_path, capsys, only_fake_clis):
    run = walk_to_proposals(tmp_path, capsys)
    fake_cli(tmp_path, "codex", CODEX_OK)
    review = json.dumps(mock_json("MetaReview"), ensure_ascii=False)
    fake_cli(tmp_path, "gemini", f"print(json.dumps({{'response': {review!r}, 'stats': {{'models': {{'gemini-x': "
                                 f"{{'tokens': {{'prompt': 10, 'candidates': 5}}}}}}}}}}))")
    assert run_ir("review", str(run)) == 0
    capsys.readouterr()
    run_ir("next", str(run))
    assert "汇总 I1、I2、I3 的评审意见" in capsys.readouterr().out
    chairs = [{"proposal_id": f"I{i}", "error": "", "summary": mock_json("ChairSummary")} for i in (1, 2, 3)]
    (run / "10_chair.json").write_text(json.dumps(chairs, ensure_ascii=False), encoding="utf-8")
    run_ir("next", str(run))
    assert "运行 report.py" in capsys.readouterr().out
    assert skill_report.main([str(run)]) == 0
    assert "评审意见汇总" in (run / "report.html").read_text(encoding="utf-8")


def test_failed_review_is_recorded_and_retried(tmp_path, capsys, only_fake_clis):
    run = walk_to_proposals(tmp_path, capsys)
    fake_cli(tmp_path, "codex", "print(json.dumps({'type': 'error', 'message': 'quota exceeded'})); sys.exit(1)")
    assert run_ir("review", str(run), "--reviewer", "codex") == 0
    metas = json.loads((run / "09_meta_reviews.json").read_text())
    assert all(not m["review"] and "quota exceeded" in m["error"] for m in metas)
    fake_cli(tmp_path, "codex", CODEX_OK)
    assert run_ir("review", str(run), "--reviewer", "codex") == 0
    assert all(m["review"] for m in json.loads((run / "09_meta_reviews.json").read_text()))


def test_no_other_company_points_to_the_fallback(tmp_path, capsys, only_fake_clis):
    run = walk_to_proposals(tmp_path, capsys)
    fake_cli(tmp_path, "claude", "print('x')")
    run_ir("next", str(run))
    assert "没找到别家公司的命令行工具" in capsys.readouterr().out
    assert run_ir("review", str(run), "--dump") == 0
    text = (run / "review_prompts" / "I1.md").read_text(encoding="utf-8")
    assert "Area Chair" in text and "P01" in text and '"causes_of_death"' in text and "$language" not in text


def test_init_needs_the_writer_and_keeps_counts(tmp_path, capsys):
    run = tmp_path / "run"
    assert run_ir("init", str(run), "--idea", "x") == 1
    assert "--writer" in capsys.readouterr().err
    assert run_ir("init", str(run), "--idea", "x", "--writer", "google") == 0
    meta = json.loads((run / "meta.json").read_text())
    assert meta["search"]["download"] == ir.DEFAULTS["download"] and meta["writer"]["family"] == "google"
    assert run_ir("init", str(run), "--refs", "12") == 0
    assert json.loads((run / "meta.json").read_text())["search"]["download"] == 12
    assert run_ir("init", str(run), "--refs", "0") == 2
