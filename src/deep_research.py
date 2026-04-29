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
    paper_text: tuple[str, str] | None = None,
) -> str:
    """Generate a 3-module scholarly analysis. Uses full paper text when available."""
    system_prompt = load_prompt("deep_research.txt")
    paper_content = _build_paper_content(paper)
    user_msg = (
        "请对以下论文进行【模式一：论文解读】，严格按照三个模块输出。\n\n"
        f"{paper_content}"
    )
    if paper_text and paper_text[1] != "abstract":
        text, source = paper_text
        user_msg += (
            f"\n\n## 论文全文 (source={source})\n"
            f"以下是论文完整正文。请基于此正文进行深度解读，引用具体方法、公式、实验数字，"
            f"不要仅基于摘要泛泛而谈。\n\n"
            f"{text}\n"
        )

    try:
        report = await call_llm(
            model=model_config["model_id"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=model_config.get("temperature", 0.2),
            api_key=api_key,
        )
        return report.strip()
    except Exception:
        logger.exception("DeepResearch failed for %s", paper.arxiv_id)
        return ""


async def generate_all_deep_research(
    papers: list[ArxivPaper],
    model_config: dict,
    api_key: str,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for paper in papers:
        report = await generate_deep_research(paper, model_config, api_key)
        if report:
            results[paper.arxiv_id] = report
    return results
