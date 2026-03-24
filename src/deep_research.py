from __future__ import annotations

import logging

from .config import load_prompt
from .llm_client import call_llm
from .models import ArxivPaper

logger = logging.getLogger(__name__)


def _build_paper_content(paper: ArxivPaper) -> str:
    return (
        f"Title: {paper.title}\n"
        f"Authors: {', '.join(paper.authors)}\n"
        f"Categories: {', '.join(paper.categories)}\n"
        f"Abstract:\n{paper.abstract}\n"
        f"Comment: {paper.comment or 'None'}\n"
        f"Journal Ref: {paper.journal_ref or 'None'}\n"
        f"PDF URL: {paper.pdf_url}"
    )


async def generate_deep_research(
    paper: ArxivPaper,
    model_config: dict,
    api_key: str,
    pdf_config: dict | None = None,
) -> str:
    """Generate a rich 3-module scholarly analysis for a single paper.

    When pdf_config is provided and enabled, sends the arXiv PDF URL directly
    to OpenRouter via the file content type + native engine, so Gemini reads
    the full paper (figures, formulas, tables included).

    Returns the Markdown report text (Chinese).
    """
    system_prompt = load_prompt("deep_research.txt")
    paper_content = _build_paper_content(paper)
    text_part = (
        "请对以下论文进行【模式一：论文解读】，严格按照三个模块输出。\n\n"
        f"{paper_content}"
    )

    use_pdf = pdf_config and pdf_config.get("download_enabled", False)
    plugins = None

    if use_pdf:
        user_content: list[dict] = [
            {"type": "text", "text": text_part},
            {"type": "file", "file": {
                "filename": f"{paper.arxiv_id}.pdf",
                "file_data": paper.pdf_url,
            }},
        ]
        engine = pdf_config.get("engine", "native")
        plugins = [{"id": "file-parser", "pdf": {"engine": engine}}]
        logger.info("DeepResearch with PDF for %s (engine=%s)", paper.arxiv_id, engine)
    else:
        user_content = text_part

    try:
        report = await call_llm(
            model=model_config["model_id"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=model_config.get("temperature", 0.2),
            api_key=api_key,
            plugins=plugins,
        )
        return report.strip()
    except Exception as exc:
        if use_pdf:
            logger.warning("DeepResearch with PDF failed for %s: %s. Retrying without PDF",
                           paper.arxiv_id, exc)
            try:
                report = await call_llm(
                    model=model_config["model_id"],
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text_part},
                    ],
                    temperature=model_config.get("temperature", 0.2),
                    api_key=api_key,
                )
                return report.strip()
            except Exception:
                logger.exception("DeepResearch fallback also failed for %s", paper.arxiv_id)
                return ""
        else:
            logger.exception("DeepResearch failed for %s", paper.arxiv_id)
            return ""


async def generate_all_deep_research(
    papers: list[ArxivPaper],
    model_config: dict,
    api_key: str,
) -> dict[str, str]:
    """Generate deep research reports for all papers.

    Returns {arxiv_id: markdown_report}.
    """
    results: dict[str, str] = {}
    for paper in papers:
        report = await generate_deep_research(paper, model_config, api_key)
        if report:
            results[paper.arxiv_id] = report
            logger.info("DeepResearch done for %s (%d chars)", paper.arxiv_id, len(report))
        else:
            logger.warning("DeepResearch empty for %s", paper.arxiv_id)
    return results
