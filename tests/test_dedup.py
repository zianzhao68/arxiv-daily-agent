from datetime import datetime, timezone

from src.dedup import dedup
from src.models import ArxivPaper


def _make_paper(arxiv_id: str, title: str = "Test") -> ArxivPaper:
    now = datetime.now(timezone.utc)
    return ArxivPaper(
        arxiv_id=arxiv_id,
        version=1,
        title=title,
        abstract="abstract",
        authors=["Author"],
        categories=["cs.CV"],
        primary_category="cs.CV",
        published=now,
        updated=now,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}v1",
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
    )


def test_dedup_removes_known_papers():
    index = {
        "_meta": {"version": 1, "last_updated": "", "total_papers": 1},
        "2506.00001": {"title": "Old Paper"},
    }
    candidates = [_make_paper("2506.00001"), _make_paper("2506.00002")]
    result = dedup(candidates, index)
    assert len(result) == 1
    assert result[0].arxiv_id == "2506.00002"


def test_dedup_empty_index():
    index = {"_meta": {"version": 1, "last_updated": "", "total_papers": 0}}
    candidates = [_make_paper("2506.00001"), _make_paper("2506.00002")]
    result = dedup(candidates, index)
    assert len(result) == 2


def test_dedup_all_known():
    index = {
        "_meta": {"version": 1, "last_updated": "", "total_papers": 2},
        "2506.00001": {},
        "2506.00002": {},
    }
    candidates = [_make_paper("2506.00001"), _make_paper("2506.00002")]
    result = dedup(candidates, index)
    assert len(result) == 0
