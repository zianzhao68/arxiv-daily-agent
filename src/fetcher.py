from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import arxiv
import feedparser

from .models import ArxivPaper

logger = logging.getLogger(__name__)


def _strip_version(arxiv_id: str) -> tuple[str, int]:
    m = re.match(r"^(\d+\.\d+)(v(\d+))?$", arxiv_id)
    if m:
        return m.group(1), int(m.group(3)) if m.group(3) else 1
    return arxiv_id, 1


def _extract_id_from_url(url: str) -> str:
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", url)
    return m.group(1) if m else ""


def build_query(direction_config: dict) -> str:
    clauses = []

    if direction_config.get("title_keywords"):
        ti_parts = [f'ti:"{kw}"' for kw in direction_config["title_keywords"]]
        clauses.append(f'({" OR ".join(ti_parts)})')

    if direction_config.get("abstract_keywords"):
        abs_parts = [f'abs:"{kw}"' for kw in direction_config["abstract_keywords"]]
        clauses.append(f'({" OR ".join(abs_parts)})')

    for combo in direction_config.get("abstract_combos", []):
        combo_parts = [f'abs:"{term}"' for term in combo]
        clauses.append(f'({" AND ".join(combo_parts)})')

    keyword_query = " OR ".join(clauses)

    cats = direction_config.get("categories", [])
    if cats:
        cat_query = " OR ".join([f"cat:{c}" for c in cats])
        return f"({keyword_query}) AND ({cat_query})"

    return keyword_query


def _result_to_paper(result: arxiv.Result, direction_id: str) -> ArxivPaper:
    raw_id = result.entry_id.split("/")[-1]
    base_id, version = _strip_version(raw_id)
    return ArxivPaper(
        arxiv_id=base_id,
        version=version,
        title=result.title.replace("\n", " ").strip(),
        abstract=result.summary.replace("\n", " ").strip(),
        authors=[a.name for a in result.authors],
        categories=[c for c in result.categories],
        primary_category=result.primary_category,
        published=result.published,
        updated=result.updated,
        pdf_url=result.pdf_url,
        abs_url=result.entry_id,
        comment=result.comment,
        journal_ref=result.journal_ref,
        matched_direction=direction_id,
    )


def fetch_api(config: dict) -> dict[str, ArxivPaper]:
    papers: dict[str, ArxivPaper] = {}
    max_results = config["arxiv"].get("api_max_results_per_direction", 50)
    delay = config["arxiv"].get("api_delay_seconds", 3)

    for direction_id, direction_config in config["research_directions"].items():
        query = build_query(direction_config)
        logger.info("Fetching API for %s: %s", direction_id, query[:120])

        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.LastUpdatedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        count = 0
        for result in client.results(search):
            paper = _result_to_paper(result, direction_id)
            if paper.arxiv_id not in papers:
                papers[paper.arxiv_id] = paper
            count += 1

        logger.info("API returned %d papers for %s", count, direction_id)
        time.sleep(delay)

    return papers


def fetch_rss(config: dict) -> dict[str, dict]:
    categories = config["arxiv"].get("rss_categories", "cs.CV+cs.RO+cs.AI+cs.LG+cs.MA")
    url = f"https://rss.arxiv.org/rss/{categories}"
    logger.info("Fetching RSS: %s", url)

    feed = feedparser.parse(url)
    metadata: dict[str, dict] = {}

    for entry in feed.entries:
        arxiv_id = _extract_id_from_url(entry.get("link", ""))
        if not arxiv_id:
            continue

        announce_type = "new"
        for tag in entry.get("tags", []):
            term = tag.get("term", "")
            if term in ("new", "replace", "cross", "replace-cross"):
                announce_type = term
                break

        # feedparser also exposes arxiv-specific fields
        if hasattr(entry, "arxiv_announce_type"):
            announce_type = entry.arxiv_announce_type

        metadata[arxiv_id] = {
            "announce_type": announce_type,
            "pub_date": entry.get("published", ""),
        }

    logger.info("RSS returned %d entries", len(metadata))
    return metadata


def hybrid_fetch(config: dict) -> list[ArxivPaper]:
    api_papers = fetch_api(config)
    rss_metadata = fetch_rss(config)

    for arxiv_id, paper in api_papers.items():
        if arxiv_id in rss_metadata:
            paper.announce_type = rss_metadata[arxiv_id]["announce_type"]
            paper.rss_pub_date = rss_metadata[arxiv_id]["pub_date"]
        else:
            paper.announce_type = None
            paper.rss_pub_date = None

    candidates = []
    now = datetime.now(timezone.utc)
    staleness_hours = config["arxiv"].get("staleness_threshold_hours", 24)

    for paper in api_papers.values():
        if paper.announce_type in ("replace", "replace-cross"):
            continue

        if paper.announce_type in ("new", "cross"):
            candidates.append(paper)
            continue

        # Not in RSS — apply safety net
        updated = paper.updated
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = now - updated
        if age <= timedelta(hours=staleness_hours):
            candidates.append(paper)
            logger.info("Keeping API-only paper (age %s): %s", age, paper.arxiv_id)
        else:
            logger.debug("Discarding stale API-only paper (age %s): %s", age, paper.arxiv_id)

    logger.info("Hybrid fetch: %d API papers + %d RSS entries -> %d candidates",
                len(api_papers), len(rss_metadata), len(candidates))
    return candidates
