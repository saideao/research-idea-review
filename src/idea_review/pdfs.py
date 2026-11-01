"""下载开放获取的 PDF，抽出全文文字，按页加上 [p.N] 标记，去掉参考文献。"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import httpx
from pypdf import PdfReader

from .schemas import FullText, Paper
from .search import USER_AGENT, lookup_arxiv_id

MAX_PDF_BYTES = 40 * 1024 * 1024
# pypdf 遇到不规范的 PDF 会刷很多警告，不影响抽文字，关掉
logging.getLogger("pypdf").setLevel(logging.ERROR)
_REFS = re.compile(r"\n\s*(?:\d+\.?\s*)?(References|REFERENCES|Bibliography|BIBLIOGRAPHY|参考文献)\s*\n")


NO_LINK = "没有开放获取的 PDF 链接"


def candidate_urls(p: Paper) -> list[str]:
    urls = list(p.pdf_urls)
    if p.arxiv_id:
        urls.append(f"https://arxiv.org/pdf/{p.arxiv_id}")
    return list(dict.fromkeys(urls))


async def save_pdf(client: httpx.AsyncClient, url: str, dest: Path) -> str | None:
    """下载一个链接，确实是 PDF 才存下来。返回失败原因，成功返回 None。"""
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                return f"{resp.status_code} {url}"
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("文件超过 40MB")
                chunks.append(chunk)
    except (httpx.HTTPError, ValueError) as e:
        return f"{type(e).__name__} {url}"
    data = b"".join(chunks)
    if not data.startswith(b"%PDF"):
        return f"不是 PDF（可能是登录页或网页）{url}"
    dest.write_bytes(data)
    return None


async def download(client: httpx.AsyncClient, p: Paper, dest: Path) -> str | None:
    """依次试每个链接，存下第一个真正是 PDF 的文件；都不行、又没有 arXiv 编号的，按标题去 arXiv 找预印本。
    返回失败原因，成功返回 None。"""
    reasons = []
    for url in candidate_urls(p):
        reason = await save_pdf(client, url, dest)
        if reason is None:
            return None
        reasons.append(reason)
        if "arxiv.org" in url:
            await asyncio.sleep(1)
    if not p.arxiv_id:
        arxiv_id = await lookup_arxiv_id(client, p.title)
        if arxiv_id:
            reason = await save_pdf(client, f"https://arxiv.org/pdf/{arxiv_id}", dest)
            if reason is None:
                return None
            reasons.append(reason)
    return "；".join(reasons) or NO_LINK


def extract_text(pdf_path: Path) -> tuple[str, int]:
    reader = PdfReader(str(pdf_path))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # pypdf 遇到坏页会抛各种异常，跳过这一页
            text = ""
        parts.append(f"[p.{i}]\n{text.strip()}")
    full = "\n\n".join(parts)
    # 只在后半部分找"参考文献"标题，避免误切正文里的同名小节
    cut = None
    for m in _REFS.finditer(full):
        if m.start() > len(full) * 0.5:
            cut = m.start()
    if cut:
        full = full[:cut] + "\n\n[参考文献部分已省略]"
    return full, len(reader.pages)


def pdf_title(pdf_path: Path) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        meta = reader.metadata
        if meta and meta.title and len(meta.title.strip()) > 8:
            return meta.title.strip()
        first = (reader.pages[0].extract_text() or "").strip().splitlines()
        lines = [l.strip() for l in first if len(l.strip()) > 8]
        if lines:
            return lines[0][:200]
    except Exception:
        pass
    return pdf_path.stem


def clip(text: str, max_chars: int) -> tuple[str, bool]:
    """太长的全文保留开头 80% 和结尾 20%，中间标明省略。"""
    if len(text) <= max_chars:
        return text, False
    head = int(max_chars * 0.8)
    tail = max_chars - head
    return text[:head] + "\n\n[……中间省略……]\n\n" + text[-tail:], True


def stored_path(path: Path, run_dir: Path) -> str:
    """运行目录里的文件记相对运行目录的路径，整个目录挪走也能用；目录外的（你给的本地 PDF）记绝对路径。"""
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path.resolve())


async def fetch_fulltexts(papers: list[Paper], run_dir: Path, log) -> dict[str, FullText]:
    pdf_dir, text_dir = run_dir / "papers", run_dir / "text"
    pdf_dir.mkdir(exist_ok=True)
    text_dir.mkdir(exist_ok=True)
    results: dict[str, FullText] = {}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"}
    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as client:
        for p in papers:
            pdf_path = Path(p.local_path) if p.local_path else pdf_dir / f"{p.paper_id}.pdf"
            if not pdf_path.exists():
                reason = await download(client, p, pdf_path)
                if reason:
                    status = "no_pdf" if reason == NO_LINK else "failed"
                    results[p.paper_id] = FullText(paper_id=p.paper_id, status=status, note=reason)
                    log(f"  {p.paper_id} 没拿到全文：{reason[:120]}")
                    continue
            try:
                text, pages = extract_text(pdf_path)
            except Exception as e:
                results[p.paper_id] = FullText(paper_id=p.paper_id, status="failed", pdf_path=stored_path(pdf_path, run_dir),
                                               note=f"PDF 解析失败：{e}")
                log(f"  {p.paper_id} PDF 解析失败：{e}")
                continue
            if len(text) < 2000:
                results[p.paper_id] = FullText(paper_id=p.paper_id, status="failed", pdf_path=stored_path(pdf_path, run_dir), pages=pages,
                                               note="抽出来的文字太少，可能是扫描版 PDF")
                log(f"  {p.paper_id} 抽出来的文字太少，可能是扫描版")
                continue
            text_path = text_dir / f"{p.paper_id}.txt"
            text_path.write_text(text, encoding="utf-8")
            results[p.paper_id] = FullText(paper_id=p.paper_id, status="ok", pdf_path=stored_path(pdf_path, run_dir),
                                           text_path=stored_path(text_path, run_dir), chars=len(text), pages=pages)
            log(f"  {p.paper_id} 全文 {pages} 页，{len(text)} 字符")
    return results
