from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from .models import ArxivPaper

logger = logging.getLogger(__name__)


async def download_pdf(
    paper: ArxivPaper,
    output_dir: Path,
    sem: asyncio.Semaphore,
) -> Path | None:
    dest = output_dir / f"{paper.arxiv_id}.pdf"
    if dest.exists():
        logger.debug("PDF already exists: %s", dest)
        return dest

    async with sem:
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get(paper.pdf_url)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                logger.info("Downloaded PDF: %s (%.1f MB)", paper.arxiv_id, len(resp.content) / 1e6)
                return dest
        except Exception:
            logger.warning("Failed to download PDF for %s", paper.arxiv_id, exc_info=True)
            return None


async def download_all_pdfs(
    papers: list[ArxivPaper],
    output_dir: Path,
    max_concurrent: int = 5,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(max_concurrent)

    tasks = [download_pdf(paper, output_dir, sem) for paper in papers]
    results = await asyncio.gather(*tasks)

    downloaded: dict[str, Path] = {}
    for paper, path in zip(papers, results):
        if path is not None:
            downloaded[paper.arxiv_id] = path

    logger.info("PDFs downloaded: %d/%d", len(downloaded), len(papers))
    return downloaded
