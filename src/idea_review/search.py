"""在 Semantic Scholar、arXiv、OpenAlex 上检索论文，合并去重。三个接口都免费，不需要账号。"""
from __future__ import annotations

import asyncio
import math
import re
import xml.etree.ElementTree as ET

import httpx

from .schemas import Paper

USER_AGENT = "idea-review/0.1 (literature review helper)"
S2_FIELDS = "title,abstract,year,venue,citationCount,externalIds,openAccessPdf,authors,url,publicationTypes,journal"
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_STOP = {"a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with", "by", "via", "using", "from", "is", "are"}


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _norm_arxiv(aid: str | None) -> str | None:
    if not aid:
        return None
    aid = aid.strip().split("/abs/")[-1]
    return re.sub(r"v\d+$", "", aid)


async def _get(client: httpx.AsyncClient, url: str, params: dict, headers: dict | None = None, tries: int = 4) -> httpx.Response:
    delay = 2.0
    for attempt in range(tries):
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.TransportError:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            await asyncio.sleep(float(resp.headers.get("retry-after", delay)))
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")


async def search_semantic_scholar(client: httpx.AsyncClient, query: str, limit: int, year_from: int | None, api_key: str | None) -> list[Paper]:
    params = {"query": query, "limit": min(limit, 100), "fields": S2_FIELDS}
    if year_from:
        params["year"] = f"{year_from}-"
    headers = {"x-api-key": api_key} if api_key else None
    resp = await _get(client, "https://api.semanticscholar.org/graph/v1/paper/search", params, headers)
    papers = []
    for item in resp.json().get("data") or []:
        if not item.get("title"):
            continue
        ext = item.get("externalIds") or {}
        venue = item.get("venue") or (item.get("journal") or {}).get("name") or None
        types = item.get("publicationTypes") or []
        arxiv_only = (not venue or "arxiv" in venue.lower())
        peer = True if (("JournalArticle" in types or "Conference" in types) and not arxiv_only) else (False if arxiv_only and ext.get("ArXiv") else None)
        pdfs = []
        if (item.get("openAccessPdf") or {}).get("url"):
            pdfs.append(item["openAccessPdf"]["url"])
        papers.append(Paper(
            title=item["title"].strip(),
            authors=[a.get("name", "") for a in item.get("authors") or []][:12],
            year=item.get("year"),
            venue=venue,
            peer_reviewed=peer,
            citation_count=item.get("citationCount"),
            abstract=item.get("abstract"),
            doi=(ext.get("DOI") or None),
            arxiv_id=_norm_arxiv(ext.get("ArXiv")),
            s2_id=item.get("paperId"),
            url=item.get("url"),
            pdf_urls=pdfs,
            sources=["semantic_scholar"],
        ))
    return papers


async def search_arxiv(client: httpx.AsyncClient, query: str, limit: int) -> list[Paper]:
    words = [w for w in re.findall(r"[A-Za-z0-9\-]+", query) if w.lower() not in _STOP][:5]
    if not words:
        return []
    params = {"search_query": " AND ".join(f"all:{w}" for w in words), "max_results": limit, "sortBy": "relevance"}
    resp = await _get(client, "https://export.arxiv.org/api/query", params)
    root = ET.fromstring(resp.text)
    papers = []
    for entry in root.findall("a:entry", ARXIV_NS):
        title = " ".join((entry.findtext("a:title", "", ARXIV_NS) or "").split())
        if not title:
            continue
        aid = _norm_arxiv(entry.findtext("a:id", "", ARXIV_NS))
        journal_ref = entry.findtext("arxiv:journal_ref", None, ARXIV_NS)
        published = entry.findtext("a:published", "", ARXIV_NS)
        doi = entry.findtext("arxiv:doi", None, ARXIV_NS)
        papers.append(Paper(
            title=title,
            authors=[a.findtext("a:name", "", ARXIV_NS) for a in entry.findall("a:author", ARXIV_NS)][:12],
            year=int(published[:4]) if published[:4].isdigit() else None,
            venue=journal_ref or "arXiv",
            peer_reviewed=True if journal_ref else False,
            abstract=" ".join((entry.findtext("a:summary", "", ARXIV_NS) or "").split()),
            doi=doi,
            arxiv_id=aid,
            url=f"https://arxiv.org/abs/{aid}" if aid else None,
            pdf_urls=[f"https://arxiv.org/pdf/{aid}"] if aid else [],
            sources=["arxiv"],
        ))
    return papers


def _openalex_abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    positions = [(pos, word) for word, poss in inverted.items() for pos in poss]
    return " ".join(word for _, word in sorted(positions))


async def search_openalex(client: httpx.AsyncClient, query: str, limit: int, year_from: int | None, mailto: str | None) -> list[Paper]:
    params = {"search": query, "per-page": min(limit, 50)}
    if year_from:
        params["filter"] = f"from_publication_date:{year_from}-01-01"
    if mailto:
        params["mailto"] = mailto
    resp = await _get(client, "https://api.openalex.org/works", params)
    papers = []
    for w in resp.json().get("results") or []:
        title = w.get("title") or w.get("display_name")
        if not title:
            continue
        primary = w.get("primary_location") or {}
        source = primary.get("source") or {}
        venue = source.get("display_name")
        is_repo = source.get("type") == "repository" or (venue or "").lower().startswith("arxiv")
        peer = False if (w.get("type") == "preprint" or is_repo) else (True if source.get("type") in ("journal", "conference") else None)
        arxiv_id = None
        pdfs = []
        for loc in w.get("locations") or []:
            landing = loc.get("landing_page_url") or ""
            if "arxiv.org/abs/" in landing:
                arxiv_id = _norm_arxiv(landing)
            if loc.get("pdf_url"):
                pdfs.append(loc["pdf_url"])
        best = (w.get("best_oa_location") or {}).get("pdf_url")
        if best and best not in pdfs:
            pdfs.insert(0, best)
        doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
        papers.append(Paper(
            title=" ".join(title.split()),
            authors=[(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []][:12],
            year=w.get("publication_year"),
            venue=venue,
            peer_reviewed=peer,
            citation_count=w.get("cited_by_count"),
            abstract=_openalex_abstract(w.get("abstract_inverted_index")),
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=w.get("id"),
            url=w.get("doi") or w.get("id"),
            pdf_urls=pdfs,
            sources=["openalex"],
        ))
    return papers


def merge(papers: list[Paper]) -> list[Paper]:
    """按 DOI、arXiv 编号、标题合并同一篇论文，字段互相补全。"""
    merged: list[Paper] = []
    index: dict[str, int] = {}
    for p in papers:
        keys = [k for k in (
            f"doi:{p.doi.lower()}" if p.doi else None,
            f"arxiv:{p.arxiv_id}" if p.arxiv_id else None,
            f"title:{_norm_title(p.title)}" if len(_norm_title(p.title)) > 15 else None,
        ) if k]
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            p = p.model_copy(update={"query_hits": max(p.query_hits, 1)})
            merged.append(p)
            hit = len(merged) - 1
        else:
            old = merged[hit]
            data = old.model_dump()
            for field_name, value in p.model_dump().items():
                if field_name in ("sources", "pdf_urls"):
                    data[field_name] = list(dict.fromkeys(data[field_name] + value))
                elif field_name == "citation_count":
                    data[field_name] = max(filter(None, [data[field_name], value]), default=None)
                elif field_name == "peer_reviewed":
                    data[field_name] = True if True in (data[field_name], value) else (data[field_name] if data[field_name] is not None else value)
                elif field_name == "abstract":
                    if value and len(value) > len(data[field_name] or ""):
                        data[field_name] = value
                elif field_name == "venue":
                    # 同一篇论文既有 arXiv 版又有正式发表版时，出处写正式发表的那个
                    if value and p.peer_reviewed and (not data[field_name] or "arxiv" in data[field_name].lower()):
                        data[field_name] = value
                elif data.get(field_name) in (None, "", []):
                    data[field_name] = value
            data["query_hits"] = old.query_hits + 1
            merged[hit] = Paper(**data)
        for k in keys:
            index[k] = hit
    return merged


def rank(papers: list[Paper]) -> list[Paper]:
    """粗排：被越多检索式、越多来源命中的越靠前，引用数只做轻微加权。"""
    def score(p: Paper) -> float:
        return p.query_hits * 2 + len(p.sources) + math.log1p(p.citation_count or 0) * 0.5 + (1 if p.abstract else 0)
    return sorted(papers, key=score, reverse=True)


async def search_all(queries: list[str], sources: list[str], per_query: int, year_from: int | None,
                     s2_key: str | None, mailto: str | None, log) -> list[Paper]:
    found: list[Paper] = []
    disabled: set[str] = set()
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for q in queries:
            for src in sources:
                if src in disabled:
                    continue
                try:
                    if src == "semantic_scholar":
                        got = await search_semantic_scholar(client, q, per_query, year_from, s2_key)
                        await asyncio.sleep(0.2 if s2_key else 1.2)
                    elif src == "arxiv":
                        got = await search_arxiv(client, q, per_query)
                        await asyncio.sleep(3)  # arXiv 要求两次请求间隔 3 秒
                    elif src == "openalex":
                        got = await search_openalex(client, q, per_query, year_from, mailto)
                    else:
                        log(f"不认识的检索源 {src}，跳过")
                        continue
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code
                    log(f"  {src} 检索失败（{q}）：HTTP {code}")
                    if code == 429:
                        disabled.add(src)
                        hint = "，在配置里填上 S2_API_KEY 可以避免" if src == "semantic_scholar" else ""
                        log(f"  {src} 限流了，这次后面的检索跳过它{hint}")
                    continue
                except (httpx.HTTPError, ET.ParseError, ValueError) as e:
                    log(f"  {src} 检索失败（{q}）：{type(e).__name__} {str(e)[:120]}")
                    continue
                log(f"  {src}：{q} → {len(got)} 篇")
                found.extend(got)
    return rank(merge(found))
