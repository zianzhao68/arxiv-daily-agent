from __future__ import annotations

import asyncio
import gzip
import io
import logging
import re
import tarfile
from pathlib import Path

import httpx

from .models import ArxivPaper

logger = logging.getLogger(__name__)

EPRINT_URL = "https://arxiv.org/e-print/{id}"
MAX_CHARS = 120_000
TEX_DOC_PATTERN = re.compile(r"\\begin\{document\}.*?\\end\{document\}", re.DOTALL)
TEX_COMMENT_LINE = re.compile(r"(?<!\\)%[^\n]*")


def _strip_tex_noise(tex: str) -> str:
    tex = TEX_COMMENT_LINE.sub("", tex)
    return tex


def _tex_from_archive(blob: bytes) -> str | None:
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            tex_chunks: list[str] = []
            main_chunk: str | None = None
            for member in tar.getmembers():
                if not member.isfile() or not member.name.endswith(".tex"):
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                try:
                    content = f.read().decode("utf-8", errors="replace")
                except Exception:
                    continue
                if "\\begin{document}" in content:
                    main_chunk = content
                else:
                    tex_chunks.append(content)
        if main_chunk:
            doc_match = TEX_DOC_PATTERN.search(main_chunk)
            body = doc_match.group(0) if doc_match else main_chunk
            joined = "\n\n".join([body, *tex_chunks])
        elif tex_chunks:
            joined = "\n\n".join(tex_chunks)
        else:
            return None
        return _strip_tex_noise(joined)
    except (tarfile.TarError, OSError):
        return None


def _tex_from_gzip(blob: bytes) -> str | None:
    try:
        text = gzip.decompress(blob).decode("utf-8", errors="replace")
        if "\\begin{document}" in text or "\\section" in text:
            return _strip_tex_noise(text)
    except (OSError, UnicodeDecodeError):
        pass
    return None


async def _try_fetch_tex(arxiv_id: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(EPRINT_URL.format(id=arxiv_id), timeout=60)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("e-print fetch failed for %s: %s", arxiv_id, exc)
        return None

    blob = resp.content
    if blob[:2] == b"\x1f\x8b":
        text = _tex_from_archive(blob) or _tex_from_gzip(blob)
        if text:
            return text
    if blob[:5] == b"%PDF-":
        return None
    text = _tex_from_archive(blob)
    if text:
        return text
    return None


def _text_from_pdf(pdf_path: Path) -> str | None:
    try:
        import pymupdf
    except ImportError:
        logger.warning("pymupdf not installed; skipping PDF text extraction")
        return None
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        logger.warning("pymupdf open failed for %s: %s", pdf_path, exc)
        return None
    try:
        chunks = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    text = "\n".join(chunks).strip()
    return text or None


async def extract_paper_text(
    paper: ArxivPaper,
    pdf_path: Path | None,
    client: httpx.AsyncClient,
) -> tuple[str, str]:
    """Return (text, source). source ∈ {"tex","pdf","abstract"}."""
    tex = await _try_fetch_tex(paper.arxiv_id, client)
    if tex:
        if len(tex) > MAX_CHARS:
            tex = tex[:MAX_CHARS] + "\n\n[...truncated...]"
        return tex, "tex"

    if pdf_path and pdf_path.exists():
        text = await asyncio.to_thread(_text_from_pdf, pdf_path)
        if text:
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + "\n\n[...truncated...]"
            return text, "pdf"

    return paper.abstract, "abstract"


async def extract_all_paper_texts(
    papers: list[ArxivPaper],
    pdf_paths: dict[str, Path],
    max_concurrent: int = 5,
) -> dict[str, tuple[str, str]]:
    sem = asyncio.Semaphore(max_concurrent)
    results: dict[str, tuple[str, str]] = {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def _one(paper: ArxivPaper) -> None:
            async with sem:
                text, source = await extract_paper_text(paper, pdf_paths.get(paper.arxiv_id), client)
                results[paper.arxiv_id] = (text, source)
                logger.info("paper_text %s: source=%s, len=%d", paper.arxiv_id, source, len(text))

        await asyncio.gather(*[_one(p) for p in papers])

    summary = {"tex": 0, "pdf": 0, "abstract": 0}
    for _, src in results.values():
        summary[src] = summary.get(src, 0) + 1
    logger.info("paper_text summary: tex=%d pdf=%d abstract=%d",
                summary["tex"], summary["pdf"], summary["abstract"])
    return results
