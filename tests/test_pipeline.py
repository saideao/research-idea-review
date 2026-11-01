"""不联网的端到端测试：检索和下载换成假数据，模型用 mock。"""
from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path

import pytest

from idea_review import cli, pdfs, prompts
from idea_review import pipeline as pl
from idea_review.config import ConfigError, load_config
from idea_review.llm import LLMError, MockProvider, strict_schema
from idea_review.schemas import (
    AbstractCheck, ChairSummary, FullText, IdeaSeeds, Landscape, MetaReview, Paper, PaperReview, Proposal, QueryPlan,
    ScreenBatch,
)

CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


@pytest.fixture
def mock_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(resources.files("idea_review").joinpath("examples", "config.mock.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(path)


def fake_papers(n: int = 6) -> list[Paper]:
    return [Paper(title=f"Paper number {i} about surveys", year=2024, venue="ICLR" if i % 2 else "arXiv",
                  peer_reviewed=bool(i % 2), abstract=f"Abstract {i}", pdf_urls=[f"https://example.org/{i}.pdf"],
                  sources=["openalex"]) for i in range(1, n + 1)]


async def fake_search(queries, sources, per_query, year_from, s2_key, mailto, log):
    return fake_papers()


async def fake_fetch(papers, run_dir, log):
    (run_dir / "text").mkdir(exist_ok=True)
    out = {}
    for p in papers:
        text_path = run_dir / "text" / f"{p.paper_id}.txt"
        text_path.write_text(f"[p.1]\n{p.title}\n" + "body text " * 400, encoding="utf-8")
        out[p.paper_id] = FullText(paper_id=p.paper_id, status="ok", text_path=str(text_path), chars=4000, pages=1)
    return out


def test_strict_schema_closes_every_object():
    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    for model in (PaperReview, MetaReview):
        schema = strict_schema(model)
        found = list(objects(schema))
        assert found
        for obj in found:
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])


def test_full_pipeline_with_mocks(tmp_path, mock_config, monkeypatch):
    monkeypatch.setattr(pl, "search_all", fake_search)
    monkeypatch.setattr(pl, "fetch_fulltexts", fake_fetch)
    run_dir = tmp_path / "run"
    import asyncio
    report = asyncio.run(pl.Pipeline(mock_config, run_dir, "让大模型代替真人填问卷").run())

    assert report.exists()
    html = report.read_text(encoding="utf-8")
    assert "Idea 评审报告" in html and "元评审" in html
    proposals = json.loads((run_dir / "08_proposals.json").read_text(encoding="utf-8"))
    assert len(proposals) == mock_config.review.ideas
    metas = json.loads((run_dir / "09_meta_reviews.json").read_text(encoding="utf-8"))
    assert len(metas) == mock_config.review.ideas * len(mock_config.roles.reviewers)
    assert all(m["review"] for m in metas)

    # 再跑一次同一个目录：所有步骤都应该直接读缓存，不再调用模型
    calls_before = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    asyncio.run(pl.Pipeline(mock_config, run_dir, "让大模型代替真人填问卷").run())
    calls_after = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    assert calls_before == calls_after


def test_only_overclaims_count_as_abstract_mismatch():
    assert AbstractCheck(overclaims=[], scope_gaps=["只在一个模型上测过 [p.5]"]).consistent
    assert not AbstractCheck(overclaims=["摘要说提升 50%，表里是 24% [p.7]"], scope_gaps=[]).consistent
    # 旧版本存的运行目录还能读
    old = AbstractCheck.model_validate({"consistent": False, "discrepancies": ["摘要说 A，正文是 B [p.3]"]})
    assert old.overclaims == ["摘要说 A，正文是 B [p.3]"] and old.scope_gaps == []
    assert AbstractCheck.model_validate({"consistent": True, "discrepancies": []}).consistent


def test_warns_when_writer_and_reviewer_share_a_family(mock_config):
    mock_config.providers["reviewer_b"].family = mock_config.provider("writer").family
    assert any("同一家" in w for w in mock_config.warnings())


# ---------- 参考文献数量：--candidates / --refs ----------

IDEA = "让大模型代替真人填问卷"


@pytest.fixture
def offline(monkeypatch):
    """检索返回 12 篇假论文，下载默认全部成功。mock 初筛按每批里的顺序给 高、中、低、高、中、低……"""
    async def search(*args):
        return fake_papers(12)
    monkeypatch.setattr(pl, "search_all", search)
    monkeypatch.setattr(pl, "fetch_fulltexts", fake_fetch)


def run_cli(tmp_path: Path, config, *args: str) -> int:
    return cli.main(["run", IDEA, "--config", str(config.path), "--runs-dir", str(tmp_path), "--name", "r",
                     "--query", "LLM simulated survey respondents", *args])


def resume_cli(tmp_path: Path, config, *args: str) -> int:
    return cli.main(["run", "--config", str(config.path), "--resume", str(tmp_path / "r"), *args])


def load(run_dir: Path, name: str):
    return json.loads((run_dir / f"{name}.json").read_text(encoding="utf-8"))


def ok_ids(run_dir: Path) -> list[str]:
    return sorted(pid for pid, f in load(run_dir, "04_fulltext").items() if f["status"] == "ok")


def test_counts_from_command_line_go_into_meta_and_report(tmp_path, mock_config, offline):
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    assert load(run_dir, "meta")["search"] == {"per_query": 10, "max_candidates": 9, "download": 3}
    assert len(load(run_dir, "02_candidates")) == 9
    assert len(load(run_dir, "02_search_results")) == 12
    # 9 篇候选里高相关的是 P01、P04、P07，精读 3 篇正好是它们
    assert ok_ids(run_dir) == ["P01", "P04", "P07"]
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "参考文献数量" in html and "最多 9 篇" in html


def test_failed_downloads_are_replaced_by_the_next_paper(tmp_path, mock_config, monkeypatch, offline):
    async def flaky_fetch(papers, run_dir, log):
        out = await fake_fetch(papers, run_dir, log)
        for pid in ("P01", "P04"):
            if pid in out:
                out[pid] = FullText(paper_id=pid, status="failed", note="404")
        return out
    monkeypatch.setattr(pl, "fetch_fulltexts", flaky_fetch)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    # P01、P04 下载失败，高相关只剩 P07，再按顺序补中相关的 P02、P05
    assert ok_ids(run_dir) == ["P02", "P05", "P07"]
    assert load(run_dir, "04_fulltext")["P01"]["status"] == "failed"
    assert "P08" not in load(run_dir, "04_fulltext")


def test_resume_with_more_refs_reads_more_and_redoes_later_steps(tmp_path, mock_config, offline):
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    old_proposals = load(run_dir, "08_proposals")

    # 不带 --refs 接着跑：沿用这个运行目录上次的数量，什么都不重做
    assert resume_cli(tmp_path, mock_config) == 0
    assert load(run_dir, "meta")["search"]["download"] == 3
    assert not (run_dir / "history").exists()

    assert resume_cli(tmp_path, mock_config, "--refs", "5") == 0
    assert load(run_dir, "meta")["search"]["download"] == 5
    assert ok_ids(run_dir) == ["P01", "P02", "P04", "P05", "P07"]
    assert len(load(run_dir, "05_reviews")) == 5
    # 已经读过的 3 篇不重读：前后两次一共只精读了 5 次
    assert (run_dir / "run.log").read_text(encoding="utf-8").count("精读完成") == 5
    # 第 6–10 步按新文献重做，旧结果留在 history/ 里
    archived = [d for d in (run_dir / "history").iterdir()]
    assert len(archived) == 1
    for name in ("06_landscape", "07_seeds", "08_proposals", "09_meta_reviews", "10_chair"):
        assert (archived[0] / f"{name}.json").exists() and (run_dir / f"{name}.json").exists()
    assert (archived[0] / "report.html").exists() and (run_dir / "report.html").exists()
    assert json.loads((archived[0] / "08_proposals.json").read_text(encoding="utf-8")) == old_proposals


def test_resume_with_fewer_refs_keeps_everything(tmp_path, mock_config, offline):
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    usage = load(run_dir, "usage")
    assert resume_cli(tmp_path, mock_config, "--refs", "2") == 0
    assert load(run_dir, "usage") == usage
    assert ok_ids(run_dir) == ["P01", "P04", "P07"]
    assert not (run_dir / "history").exists()
    assert "调小数量不会删掉已经读过的" in (run_dir / "report.html").read_text(encoding="utf-8")


def test_resume_with_more_candidates_takes_them_from_saved_results(tmp_path, mock_config, offline):
    assert run_cli(tmp_path, mock_config, "--candidates", "6", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    before = load(run_dir, "02_candidates")
    assert resume_cli(tmp_path, mock_config, "--candidates", "9") == 0
    after = load(run_dir, "02_candidates")
    pool = load(run_dir, "02_search_results")
    assert after[:6] == before
    assert [p["paper_id"] for p in after[6:]] == ["P07", "P08", "P09"]
    assert [p["title"] for p in after[6:]] == [p["title"] for p in pool[6:9]]
    # 新候选补了初筛；精读已经够 3 篇，不补读，后面几步也不重做
    assert {s["paper_id"] for s in load(run_dir, "03_screen")} == {p["paper_id"] for p in after}
    assert ok_ids(run_dir) == ["P01", "P02", "P04"]
    assert not (run_dir / "history").exists()


def test_resume_reads_pdfs_you_put_into_papers(tmp_path, mock_config, monkeypatch, offline):
    async def fetch(papers, run_dir, log):
        # P04 没有开放获取的 PDF，除非 papers/ 里已经有了
        out = await fake_fetch(papers, run_dir, log)
        if "P04" in out and not (run_dir / "papers" / "P04.pdf").exists():
            out["P04"] = FullText(paper_id="P04", status="no_pdf", note="没有链接")
        return out
    monkeypatch.setattr(pl, "fetch_fulltexts", fetch)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    assert ok_ids(run_dir) == ["P01", "P02", "P07"]

    (run_dir / "papers").mkdir(exist_ok=True)
    (run_dir / "papers" / "P04.pdf").write_bytes(b"%PDF-1.4 manual")
    assert resume_cli(tmp_path, mock_config) == 0
    fulltexts = load(run_dir, "04_fulltext")
    assert fulltexts["P04"]["status"] == "ok" and fulltexts["P04"]["manual"]
    assert "P04" in {r["paper_id"] for r in load(run_dir, "05_reviews") if r["review"]}
    assert (run_dir / "run.log").read_text(encoding="utf-8").count("精读完成") == 4
    assert len(list((run_dir / "history").iterdir())) == 1
    assert "含你手动放进 papers/ 的 1 篇" in (run_dir / "report.html").read_text(encoding="utf-8")

    # 再跑一次：P04 已经读过，不再当成新放进来的
    usage = load(run_dir, "usage")
    assert resume_cli(tmp_path, mock_config) == 0
    assert load(run_dir, "usage") == usage
    assert len(list((run_dir / "history").iterdir())) == 1


def test_unreadable_pdf_is_retried_only_after_you_replace_it(tmp_path, mock_config, monkeypatch, offline):
    async def fetch(papers, run_dir, log):
        # 程序下载到的 P04 是扫描版，抽不出文字；换成别的文件以后才能读
        out = await fake_fetch(papers, run_dir, log)
        pdf = run_dir / "papers" / "P04.pdf"
        if "P04" in out:
            if not pdf.exists():
                pdf.parent.mkdir(exist_ok=True)
                pdf.write_bytes(b"scanned")
            if pdf.read_bytes() == b"scanned":
                out["P04"] = FullText(paper_id="P04", status="failed", pdf_path=str(pdf), note="抽出来的文字太少")
        return out
    monkeypatch.setattr(pl, "fetch_fulltexts", fetch)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    usage = load(run_dir, "usage")
    assert resume_cli(tmp_path, mock_config) == 0
    assert load(run_dir, "usage") == usage

    pdf = run_dir / "papers" / "P04.pdf"
    pdf.write_bytes(b"%PDF-1.4 readable")
    later = (run_dir / "04_fulltext.json").stat().st_mtime + 5
    os.utime(pdf, (later, later))
    assert resume_cli(tmp_path, mock_config) == 0
    assert load(run_dir, "04_fulltext")["P04"]["status"] == "ok"


def test_resume_redoes_later_steps_when_a_failed_read_succeeds(tmp_path, mock_config, monkeypatch, offline):
    real_call = pl.Pipeline._call
    broken = {"[P04]"}

    async def call(self, role, prompt, model_cls, **values):
        if prompt == "read_paper" and any(pid in values["meta"] for pid in broken):
            raise LLMError("接口报错")
        return await real_call(self, role, prompt, model_cls, **values)
    monkeypatch.setattr(pl.Pipeline, "_call", call)
    # 精读 4 篇，P04 失败后还剩 3 篇，够得上往下找空白的门槛
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "4") == 0
    run_dir = tmp_path / "r"
    assert next(r for r in load(run_dir, "05_reviews") if r["paper_id"] == "P04")["error"]
    old_landscape = load(run_dir, "06_landscape")

    # 重读还是失败：后面几步不重做
    assert resume_cli(tmp_path, mock_config) == 0
    assert not (run_dir / "history").exists()

    broken.clear()
    assert resume_cli(tmp_path, mock_config) == 0
    assert next(r for r in load(run_dir, "05_reviews") if r["paper_id"] == "P04")["review"]
    assert (run_dir / "run.log").read_text(encoding="utf-8").count("精读完成") == 4
    archived = list((run_dir / "history").iterdir())
    assert len(archived) == 1
    assert json.loads((archived[0] / "06_landscape.json").read_text(encoding="utf-8")) == old_landscape
    assert (run_dir / "10_chair.json").exists()


def test_mock_runs_need_queries_from_the_command_line(tmp_path, mock_config, offline, capsys):
    argv = ["run", IDEA, "--config", str(mock_config.path), "--runs-dir", str(tmp_path), "--name", "r"]
    assert cli.main(argv) == 2
    assert "--query" in capsys.readouterr().out
    assert not (tmp_path / "r").exists()

    assert cli.main([*argv, "--query", " LLM survey respondents ", "--query", "silicon sampling"]) == 0
    run_dir = tmp_path / "r"
    assert load(run_dir, "01_plan")["search_queries"] == ["LLM survey respondents", "silicon sampling"]
    assert load(run_dir, "meta")["queries"] == ["LLM survey respondents", "silicon sampling"]
    assert resume_cli(tmp_path, mock_config, "--query", "something else") == 2
    assert "只能在新开运行时用" in capsys.readouterr().out


def test_downloaded_files_are_stored_relative_to_the_run_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pdfs, "extract_text", lambda path: ("[p.1]\n" + "body text " * 400, 1))
    run_dir = Path("runs") / "r"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "papers" / "P01.pdf").write_bytes(b"%PDF-1.4")
    local = tmp_path / "mine.pdf"
    local.write_bytes(b"%PDF-1.4")
    papers = [Paper(paper_id="P01", title="t"), Paper(paper_id="L01", title="t", local_path=str(local))]
    import asyncio
    got = asyncio.run(pdfs.fetch_fulltexts(papers, run_dir, lambda msg: None))
    assert (got["P01"].pdf_path, got["P01"].text_path) == ("papers/P01.pdf", "text/P01.txt")
    assert got["L01"].pdf_path == str(local.resolve())


def test_run_store_finds_files_after_the_run_dir_moves(tmp_path):
    store = pl.RunStore(tmp_path / "moved")
    (store.dir / "text").mkdir()
    want = store.dir / "text" / "P01.txt"
    want.write_text("x", encoding="utf-8")
    assert store.file("text/P01.txt") == want                          # 现在的记法：相对运行目录
    assert store.file("runs/old-name/text/P01.txt") == want            # 老版本：相对当时的启动目录
    assert store.file("/somewhere/else/old-name/text/P01.txt") == want  # 老版本：绝对路径，目录后来挪过
    local = tmp_path / "mine.pdf"
    local.write_bytes(b"%PDF-1.4")
    assert store.file(str(local)) == local                             # 你给的本地 PDF


def test_moved_run_dir_can_be_resumed_from_anywhere(tmp_path, mock_config, monkeypatch, offline):
    async def fetch_like_real(papers, run_dir, log):
        out = await fake_fetch(papers, run_dir, log)
        return {pid: f.model_copy(update={"text_path": pdfs.stored_path(Path(f.text_path), run_dir)}) for pid, f in out.items()}
    monkeypatch.setattr(pl, "fetch_fulltexts", fetch_like_real)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    moved = tmp_path / "elsewhere" / "renamed"
    moved.parent.mkdir()
    (tmp_path / "r").rename(moved)
    monkeypatch.chdir(moved.parent)
    # 补读一篇以后第 6–10 步重做，元评审要读挪过位置的旧全文
    assert cli.main(["run", "--config", str(mock_config.path), "--resume", str(moved), "--refs", "4"]) == 0
    assert len([r for r in load(moved, "05_reviews") if r["review"]]) == 4
    assert all(m["review"] for m in load(moved, "09_meta_reviews"))


@pytest.mark.parametrize("change, message", [
    (("download = 5", "download = 0"), "精读篇数要在 1 到 50 之间"),
    (("download = 5", 'download = "15"'), "要填整数"),
    (("max_candidates = 40", "max_candidates = 1000"), "候选篇数要在 5 到 300 之间"),
    (("max_candidates = 40\ndownload = 5", "max_candidates = 10\ndownload = 20"), "不能多于"),
    (("download = 5", "downloads = 5"), "不认识的字段"),
])
def test_config_counts_are_checked(tmp_path, change, message):
    text = resources.files("idea_review").joinpath("examples", "config.mock.toml").read_text(encoding="utf-8")
    assert change[0] in text
    path = tmp_path / "config.toml"
    path.write_text(text.replace(*change), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_command_line_counts_are_checked(tmp_path, mock_config, offline, capsys):
    assert run_cli(tmp_path, mock_config, "--refs", "999") == 2
    assert "--refs = 999 超出范围" in capsys.readouterr().out
    assert run_cli(tmp_path, mock_config, "--candidates", "8", "--refs", "9") == 2
    assert "不能多于" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run_cli(tmp_path, mock_config, "--refs", "abc")
    assert not (tmp_path / "r").exists()


# ---------- 英文版：[review] lang = "en" ----------

@pytest.fixture
def en_config(tmp_path: Path):
    path = tmp_path / "config.en.toml"
    path.write_text(resources.files("idea_review").joinpath("examples", "config.mock.en.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(path)


def test_english_schemas_and_prompts_have_no_chinese():
    for model in (QueryPlan, ScreenBatch, PaperReview, Landscape, IdeaSeeds, Proposal, MetaReview, ChairSummary):
        assert not CJK.search(json.dumps(strict_schema(model, "en"), ensure_ascii=False)), model.__name__
    for name in prompts.NAMES:
        assert not CJK.search(prompts.raw(name, "en")), name
        system, user = prompts.render(name, "en")
        assert system and user


def test_english_run_sends_no_chinese_to_models(tmp_path, en_config, monkeypatch, offline):
    sent = []
    real_complete = MockProvider._complete

    async def spy(self, system, user, schema, schema_name, lang):
        sent.append(system + user + json.dumps(schema, ensure_ascii=False))
        return await real_complete(self, system, user, schema, schema_name, lang)
    monkeypatch.setattr(MockProvider, "_complete", spy)
    argv = ["run", "Use LLMs in place of human survey respondents", "--config", str(en_config.path),
            "--runs-dir", str(tmp_path), "--name", "r", "--query", "LLM survey respondents", "--refs", "3"]
    assert cli.main(argv) == 0
    run_dir = tmp_path / "r"
    assert sent and not any(CJK.search(text) for text in sent)
    meta = load(run_dir, "meta")
    assert meta["lang"] == "en" and meta["language"] == "English"
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert '<html lang="en">' in html and "Idea Review Report" in html
    assert not CJK.search(html)


def test_resume_keeps_the_language_of_the_run(tmp_path, en_config, mock_config, offline):
    argv = ["run", "Use LLMs in place of human survey respondents", "--config", str(en_config.path),
            "--runs-dir", str(tmp_path), "--name", "r", "--query", "LLM survey respondents", "--refs", "3"]
    assert cli.main(argv) == 0
    run_dir = tmp_path / "r"
    usage = load(run_dir, "usage")
    # 换成中文配置接着跑，也还是英文版，不重做任何步骤
    assert resume_cli(tmp_path, mock_config) == 0
    assert load(run_dir, "meta")["lang"] == "en"
    assert load(run_dir, "usage") == usage
    assert "Idea Review Report" in (run_dir / "report.html").read_text(encoding="utf-8")


def test_init_writes_english_config(tmp_path):
    assert cli.main(["init", "--dir", str(tmp_path), "--lang", "en"]) == 0
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.review.lang == "en" and cfg.review.language == "English"
    assert load_config(tmp_path / "config.mock.toml").review.lang == "en"
    assert not CJK.search((tmp_path / "config.toml").read_text(encoding="utf-8"))


def test_unknown_lang_is_rejected(tmp_path):
    text = resources.files("idea_review").joinpath("examples", "config.mock.toml").read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(text.replace("ideas = 3", 'ideas = 3\nlang = "fr"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="review.lang"):
        load_config(path)


# ---------- 证据不够就停、空白个数、自动扩大候选 ----------

def only_these_download(*ids: str):
    """只有这几篇能下载到全文；别的论文放进 papers/ 以后才算下载到。"""
    async def fetch(papers, run_dir, log):
        out = await fake_fetch(papers, run_dir, log)
        for pid in list(out):
            if pid not in ids and not (run_dir / "papers" / f"{pid}.pdf").exists():
                out[pid] = FullText(paper_id=pid, status="no_pdf", note="没有链接")
        return out
    return fetch


def test_too_few_fulltexts_stop_before_finding_gaps(tmp_path, mock_config, monkeypatch, offline, capsys):
    monkeypatch.setattr(pl, "fetch_fulltexts", only_these_download("P01", "P02"))
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "5") == 0
    run_dir = tmp_path / "r"
    assert "停在了第 5 步" in capsys.readouterr().out
    assert ok_ids(run_dir) == ["P01", "P02"]
    # 第 6–10 步没跑，也没调模型
    assert not any((run_dir / f"{name}.json").exists() for name in pl.DOWNSTREAM)
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "文献证据不够，停在了第 5 步" in html and "只读到 2 篇全文，少于 3 篇" in html
    assert "这一步没有跑" in html

    # 补了一篇 PDF 再接着跑：够 3 篇了，往下跑完
    (run_dir / "papers").mkdir(exist_ok=True)
    (run_dir / "papers" / "P04.pdf").write_bytes(b"%PDF-1.4 manual")
    assert resume_cli(tmp_path, mock_config) == 0
    assert (run_dir / "10_chair.json").exists()
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "停在了第 5 步" not in html and "文献证据偏弱" not in html


def test_force_runs_the_later_steps_anyway(tmp_path, mock_config, monkeypatch, offline):
    monkeypatch.setattr(pl, "fetch_fulltexts", only_these_download("P01", "P02"))
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "5") == 0
    run_dir = tmp_path / "r"
    assert resume_cli(tmp_path, mock_config, "--force") == 0
    assert (run_dir / "10_chair.json").exists()
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "文献证据偏弱" in html and "停在了第 5 步" not in html
    # 已经跑完的，之后接着跑不用再加 --force
    usage = load(run_dir, "usage")
    assert resume_cli(tmp_path, mock_config) == 0
    assert load(run_dir, "usage") == usage


def test_all_low_quality_papers_stop_before_finding_gaps(tmp_path, mock_config, monkeypatch, offline):
    monkeypatch.setattr(MockProvider, "tier", "low")
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    assert not (run_dir / "06_landscape.json").exists()
    assert "读到的 3 篇全文都是低质量" in (run_dir / "report.html").read_text(encoding="utf-8")


def test_small_refs_lower_the_threshold(tmp_path, mock_config, offline):
    # 只要精读 2 篇时，读到 2 篇就够了
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "2") == 0
    assert (tmp_path / "r" / "10_chair.json").exists()


def test_candidates_grow_when_downloadable_ones_run_out(tmp_path, mock_config, monkeypatch, offline):
    async def fetch(papers, run_dir, log):
        out = await fake_fetch(papers, run_dir, log)
        if "P01" in out:
            out["P01"] = FullText(paper_id="P01", status="failed", note="404")
        return out
    monkeypatch.setattr(pl, "fetch_fulltexts", fetch)
    # 6 篇候选里高、中相关的是 P01、P02、P04、P05，P01 下载失败，只剩 3 篇
    assert run_cli(tmp_path, mock_config, "--candidates", "6", "--refs", "4") == 0
    run_dir = tmp_path / "r"
    candidates = load(run_dir, "02_candidates")
    assert [p["paper_id"] for p in candidates] == [f"P{i:02d}" for i in range(1, 13)]
    assert {s["paper_id"] for s in load(run_dir, "03_screen")} == {p["paper_id"] for p in candidates}
    # 新候选里第一篇高相关的 P07 补上
    assert ok_ids(run_dir) == ["P02", "P04", "P05", "P07"]
    assert load(run_dir, "meta")["expanded"] == 6
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "自动从检索结果里多取了 6 篇" in html and "调小数量不会删掉已有的候选" not in html


def test_gap_count_follows_the_papers_read(tmp_path, mock_config, monkeypatch, offline):
    real_call = pl.Pipeline._call
    seen = {}

    async def call(self, role, prompt, model_cls, **values):
        result = await real_call(self, role, prompt, model_cls, **values)
        if prompt == "mine_gaps":
            seen["max_gaps"] = values["max_gaps"]
            result.gaps = result.gaps * 5       # 模型不听话，给了 10 个
        return result
    monkeypatch.setattr(pl.Pipeline, "_call", call)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    run_dir = tmp_path / "r"
    assert seen["max_gaps"] == 3
    assert len(load(run_dir, "06_landscape")["gaps"]) == 3
    assert "只留前 3 个" in (run_dir / "run.log").read_text(encoding="utf-8")


def test_new_directions_never_outnumber_gaps(tmp_path, mock_config, monkeypatch, offline):
    real_call = pl.Pipeline._call

    async def call(self, role, prompt, model_cls, **values):
        result = await real_call(self, role, prompt, model_cls, **values)
        if prompt == "mine_gaps":
            result.gaps = result.gaps[:1]
        return result
    monkeypatch.setattr(pl.Pipeline, "_call", call)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    # 配置要 3 份 proposal（原始 idea + 2 个新方向），但只有 1 个空白，新方向只挖 1 个
    assert len(load(tmp_path / "r", "08_proposals")) == 2


def test_gaps_without_strong_papers_are_marked(tmp_path, mock_config, monkeypatch, offline):
    real_call = pl.Pipeline._call

    async def call(self, role, prompt, model_cls, **values):
        result = await real_call(self, role, prompt, model_cls, **values)
        if prompt == "mine_gaps":
            result.gaps[0].evidence = [e.model_copy(update={"paper_id": "P09"}) for e in result.gaps[0].evidence]
        return result
    monkeypatch.setattr(pl.Pipeline, "_call", call)
    assert run_cli(tmp_path, mock_config, "--candidates", "9", "--refs", "3") == 0
    html = (tmp_path / "r" / "report.html").read_text(encoding="utf-8")
    # G1 只引了没读到全文的 P09
    assert html.count("没有高、中质量的论文支撑") == 1


def test_paper_type_is_shown_and_old_notes_still_load():
    review = PaperReview.model_validate(json.loads(MockProvider_sample("PaperReview")))
    assert review.quality.paper_type in ("empirical", "survey", "position", "resource", "theory", "other")
    old = review.model_dump()
    del old["quality"]["paper_type"]
    assert PaperReview.model_validate(old).quality.paper_type == "other"
    paper = Paper(paper_id="P01", title="A survey of things")
    review.quality.paper_type = "survey"
    from idea_review.texts import TEXTS
    assert "类型：综述" in pl.review_digest(paper, review, TEXTS["zh"])
    assert "Type: survey" in pl.review_digest(paper, review, TEXTS["en"])


def MockProvider_sample(schema_name: str) -> str:
    import asyncio
    from idea_review.config import ProviderConfig
    from idea_review.llm import UsageTracker
    provider = MockProvider(ProviderConfig(name="m", type="mock", model="m", family="m"), UsageTracker())
    model = {"PaperReview": PaperReview}[schema_name]
    return asyncio.run(provider._complete("", "P01", strict_schema(model), schema_name, "zh"))


def test_english_stopped_report_has_no_chinese(tmp_path, en_config, monkeypatch, offline):
    monkeypatch.setattr(pl, "fetch_fulltexts", only_these_download("P01"))
    argv = ["run", "Use LLMs in place of human survey respondents", "--config", str(en_config.path),
            "--runs-dir", str(tmp_path), "--name", "r", "--query", "LLM survey respondents", "--refs", "3"]
    assert cli.main(argv) == 0
    html = (tmp_path / "r" / "report.html").read_text(encoding="utf-8")
    assert "stopped after step 5" in html and "Only 1 full text read, fewer than 3" in html
    assert not CJK.search(html)


def test_min_fulltexts_is_checked(tmp_path):
    text = resources.files("idea_review").joinpath("examples", "config.mock.toml").read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(text.replace("ideas = 3", "ideas = 3\nmin_fulltexts = 0"), encoding="utf-8")
    with pytest.raises(ConfigError, match="min_fulltexts"):
        load_config(path)


# ---------- 找全文：按标题去 arXiv 找预印本、ACL Anthology ----------

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">{}</feed>"""
ENTRY = "<entry><id>http://arxiv.org/abs/{id}v2</id><title>{title}</title></entry>"


def mock_client(handler):
    import httpx
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_arxiv_lookup_needs_the_same_title(monkeypatch):
    import asyncio
    from idea_review import search

    async def no_sleep(_):
        return None
    monkeypatch.setattr(search.asyncio, "sleep", no_sleep)

    def handler(request):
        entries = ENTRY.format(id="2101.00001", title="Silicon Samples:\n  Simulating Survey Respondents") + \
                  ENTRY.format(id="2101.00002", title="Something else entirely")
        import httpx
        return httpx.Response(200, text=ATOM.format(entries))

    async def go(title):
        async with mock_client(handler) as client:
            return await search.lookup_arxiv_id(client, title)
    assert asyncio.run(go("Silicon samples: simulating survey respondents")) == "2101.00001"
    assert asyncio.run(go("Silicon samples for opinion polls")) is None
    assert asyncio.run(go("Short")) is None


def test_download_falls_back_to_arxiv_by_title(tmp_path, monkeypatch):
    import asyncio
    import httpx
    from idea_review import search

    async def no_sleep(_):
        return None
    monkeypatch.setattr(search.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(pdfs.asyncio, "sleep", no_sleep)
    title = "Silicon Samples: Simulating Survey Respondents"

    def handler(request):
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=ATOM.format(ENTRY.format(id="2101.00001", title=title)))
        if request.url.path == "/pdf/2101.00001":
            return httpx.Response(200, content=b"%PDF-1.4 preprint")
        return httpx.Response(200, text="<html>login</html>")

    async def go(paper):
        async with mock_client(handler) as client:
            return await pdfs.download(client, paper, tmp_path / f"{paper.paper_id}.pdf")
    # 唯一的链接是登录页，按标题在 arXiv 找到了预印本
    assert asyncio.run(go(Paper(paper_id="P01", title=title, pdf_urls=["https://publisher.example/x"]))) is None
    assert (tmp_path / "P01.pdf").read_bytes().startswith(b"%PDF")
    # 没有链接、arXiv 上也找不到：算没有开放获取的 PDF
    assert asyncio.run(go(Paper(paper_id="P02", title="An unrelated paper title here"))) == pdfs.NO_LINK


def test_semantic_scholar_acl_papers_get_a_pdf_link():
    import asyncio
    import httpx
    from idea_review import search

    def handler(request):
        return httpx.Response(200, json={"data": [{"title": "A parser", "externalIds": {"ACL": "2020.acl-main.1"},
                                                   "openAccessPdf": None, "venue": "ACL"}]})

    async def go():
        async with mock_client(handler) as client:
            return await search.search_semantic_scholar(client, "parser", 5, None, None)
    assert asyncio.run(go())[0].pdf_urls == ["https://aclanthology.org/2020.acl-main.1.pdf"]


# ---------- 用本机 code agent 的订阅：claude_code / codex / gemini_cli ----------

def fake_agent(tmp_path: Path, body: str) -> str:
    """写一个假的命令行工具：把收到的参数、输入、工作目录和环境记到 calls.jsonl，再按 body 输出。"""
    import sys
    script = tmp_path / "fake_agent"
    script.write_text(f"""#!{sys.executable}
import json, os, sys, time
stdin = sys.stdin.read()
with open({str(tmp_path / 'calls.jsonl')!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(),
                         "has_key": any(k in os.environ for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"))}}) + "\\n")
argv = sys.argv[1:]
{body}
""", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def agent_calls(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


PLAN = {"restated_idea": "复述", "field": "社会科学", "core_concepts": ["a"], "search_queries": ["llm survey respondents"]}


def agent_provider(ptype: str, command: str, **extra):
    from idea_review.config import _provider
    from idea_review.llm import UsageTracker, make_provider
    tracker = UsageTracker()
    return make_provider(_provider("agent", {"type": ptype, "command": command, **extra}), tracker), tracker


def test_claude_code_provider_uses_the_subscription_in_an_empty_dir(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    command = fake_agent(tmp_path, f"""print(json.dumps({{"type": "result", "is_error": False, "result": "",
    "structured_output": {PLAN!r}, "usage": {{"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}},
    "modelUsage": {{"claude-test-1": {{}}}}}}))""")
    provider, tracker = agent_provider("claude_code", command, model="sonnet", effort="high")
    plan = asyncio.run(provider.structured("系统提示", "用户提示", QueryPlan))
    assert plan.search_queries == ["llm survey respondents"]
    call = agent_calls(tmp_path)[0]
    argv = call["argv"]
    assert argv[argv.index("--system-prompt") + 1] == "系统提示" and call["stdin"] == "用户提示"
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
    assert json.loads(argv[argv.index("--json-schema") + 1])["title"] == "QueryPlan"
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "high"
    # 不带 API key，在一个用完就删的空目录里跑
    assert not call["has_key"]
    assert Path(call["cwd"]).resolve() != Path.cwd().resolve() and not Path(call["cwd"]).exists()
    assert tracker.by_provider["agent"] == {"model": "claude-test-1", "calls": 1, "input_tokens": 15, "output_tokens": 7}


def test_claude_code_errors_are_reported(tmp_path):
    import asyncio
    command = fake_agent(tmp_path, """print(json.dumps({"type": "result", "is_error": True, "result": "Not logged in"}))""")
    provider, _ = agent_provider("claude_code", command)
    with pytest.raises(LLMError, match="Not logged in"):
        asyncio.run(provider.structured("s", "u", QueryPlan, retries=0))


def test_codex_provider_reads_the_last_message(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-passed")
    command = fake_agent(tmp_path, f"""out = argv[argv.index("-o") + 1]
schema = json.load(open(argv[argv.index("--output-schema") + 1]))
assert schema["title"] == "QueryPlan"
open(out, "w").write(json.dumps({PLAN!r}))
print(json.dumps({{"type": "thread.started"}}))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 20, "output_tokens": 3}}}}))""")
    provider, tracker = agent_provider("codex", command, model="gpt-x", effort="medium")
    plan = asyncio.run(provider.structured("系统提示", "用户提示", QueryPlan))
    assert plan.field == "社会科学"
    call = agent_calls(tmp_path)[0]
    argv = call["argv"]
    assert argv[0] == "exec" and argv[-1] == "-" and "--ignore-user-config" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[argv.index("-m") + 1] == "gpt-x"
    assert 'model_reasoning_effort="medium"' in argv
    assert call["stdin"].startswith("系统提示") and call["stdin"].endswith("用户提示")
    assert not call["has_key"]
    assert tracker.by_provider["agent"]["input_tokens"] == 20


def test_codex_failure_without_answer_is_reported(tmp_path):
    import asyncio
    command = fake_agent(tmp_path, """print(json.dumps({"type": "turn.failed", "error": {"message": "usage limit reached"}}))
sys.exit(1)""")
    provider, _ = agent_provider("codex", command)
    with pytest.raises(LLMError, match="usage limit reached"):
        asyncio.run(provider.structured("s", "u", QueryPlan, retries=0))


def test_gemini_cli_provider_parses_the_response(tmp_path):
    import asyncio
    command = fake_agent(tmp_path, f"""print(json.dumps({{"response": "```json\\n" + json.dumps({PLAN!r}) + "\\n```",
    "stats": {{"models": {{"gemini-test": {{"tokens": {{"prompt": 30, "candidates": 4}}}}}}}}}}))""")
    provider, tracker = agent_provider("gemini_cli", command)
    plan = asyncio.run(provider.structured("系统提示", "用户提示", QueryPlan))
    assert plan.core_concepts == ["a"]
    call = agent_calls(tmp_path)[0]
    assert "JSON Schema" in call["stdin"] and call["stdin"].startswith("系统提示")
    assert tracker.by_provider["agent"] == {"model": "gemini-test", "calls": 1, "input_tokens": 30, "output_tokens": 4}


def test_agent_timeout_and_missing_command(tmp_path):
    import asyncio
    command = fake_agent(tmp_path, "time.sleep(5)")
    provider, _ = agent_provider("claude_code", command, timeout=1)
    with pytest.raises(LLMError, match="超过 1 秒"):
        asyncio.run(provider.structured("s", "u", QueryPlan, retries=0))
    provider, _ = agent_provider("codex", str(tmp_path / "no-such-command"))
    with pytest.raises(LLMError, match="找不到命令"):
        asyncio.run(provider.structured("s", "u", QueryPlan, retries=0))


def test_agent_cli_config_defaults(tmp_path):
    text = resources.files("idea_review").joinpath("examples", "config.mock.toml").read_text(encoding="utf-8")
    text = text.replace('''[providers.reviewer_c]
type = "mock"''', '''[providers.claude_sub]
type = "claude_code"
command = "/no/such/claude"

[providers.reviewer_c]
type = "mock"''')
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    cfg = load_config(path)
    p = cfg.providers["claude_sub"]
    assert (p.model, p.family, p.concurrency) == ("", "anthropic", 2)
    assert "默认模型" in p.describe()
    assert any("/no/such/claude" in w and "没找到" in w for w in cfg.warnings())
    path.write_text(text.replace('command = "/no/such/claude"', "timeout = 0"), encoding="utf-8")
    with pytest.raises(ConfigError, match="timeout"):
        load_config(path)
