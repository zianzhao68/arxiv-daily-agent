from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from src.models import AnalysisResult, ArxivPaper
from src.report_generator import generate_daily_report
from src import rich_reading as rr


def _make_paper(arxiv_id: str, title: str = "Test Paper") -> ArxivPaper:
    now = datetime.now(timezone.utc)
    return ArxivPaper(
        arxiv_id=arxiv_id,
        version=1,
        title=title,
        abstract="This paper studies robot policies with world models.",
        authors=["Alice", "Bob"],
        categories=["cs.RO"],
        primary_category="cs.RO",
        published=now,
        updated=now,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}v1",
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
    )


def test_select_rich_reading_papers_prioritizes_core_then_hot():
    core_low = _make_paper("2601.00001", "Core Low")
    core_high = _make_paper("2601.00002", "Core High")
    hot_peripheral = _make_paper("2601.00003", "Hot Peripheral")
    cold_peripheral = _make_paper("2601.00004", "Cold Peripheral")
    analyses = {
        "2601.00001": AnalysisResult(weighted_score=2.0),
        "2601.00002": AnalysisResult(weighted_score=4.8),
        "2601.00003": AnalysisResult(weighted_score=4.9),
        "2601.00004": AnalysisResult(weighted_score=3.9),
    }

    selected = rr.select_rich_reading_papers(
        core_papers=[core_low, core_high],
        peripheral_papers=[hot_peripheral, cold_peripheral],
        analyses=analyses,
        hot_threshold=4.0,
        max_papers=2,
    )

    assert [paper.arxiv_id for paper in selected] == ["2601.00002", "2601.00001"]


def test_parse_figure_selection_and_fallback(tmp_path):
    img = tmp_path / "figure-000-arch.png"
    img.write_bytes(b"x" * 9000)
    candidates = [
        rr.FigureCandidate(
            filename=img.name,
            path=img,
            source="tex",
            original_name="arch.png",
            size_bytes=img.stat().st_size,
        )
    ]
    raw = json.dumps({
        "figures": [
            {
                "filename": img.name,
                "priority": "high",
                "reason": "architecture overview",
                "description": "method diagram",
                "suggested_section": "核心 Insight",
            }
        ]
    })

    selected = rr.parse_figure_selection(raw, candidates, max_selected=4)
    assert len(selected) == 1
    assert selected[0].priority == "high"

    assert rr.parse_figure_selection("not json", candidates, max_selected=4) == []
    fallback = rr.fallback_select_figures(candidates, max_selected=1)
    assert fallback[0].filename == img.name
    assert fallback[0].priority == "medium"


def test_filter_candidates_skips_small_files(tmp_path):
    small = tmp_path / "small.png"
    large = tmp_path / "large.png"
    small.write_bytes(b"x" * 100)
    large.write_bytes(b"x" * 9000)
    candidates = [
        rr.FigureCandidate("small.png", small, "tex", "small.png", small.stat().st_size),
        rr.FigureCandidate("large.png", large, "tex", "large.png", large.stat().st_size),
    ]

    filtered = rr._filter_candidates(candidates, min_figure_bytes=8 * 1024)
    assert [candidate.filename for candidate in filtered] == ["large.png"]


def test_normalize_github_markdown_converts_math_blocks():
    note = (
        "# Title ## 方法与公式\n"
        "正文\n"
        "$$\n"
        "\\mathcal{L}=\\left\\|x\\right\\|_2^2\n"
        "\\tag{1}\n"
        "$$\n"
        "其中 $t$ 是时间。"
    )

    normalized = rr._normalize_github_markdown(note)

    assert "$$" not in normalized
    assert "```math\n\\mathcal{L}=\\left\\|x\\right\\|_2^2\n\\tag{1}\n```" in normalized
    assert "# Title \n\n## 方法与公式" in normalized


def test_normalize_github_markdown_rewrites_fragile_math_macros():
    note = "$$\\mathbf{H}_{\\textsc{act\\_q}} + a\\big\\Vert b$$"

    normalized = rr._normalize_github_markdown(note)

    assert "\\textsc" not in normalized
    assert "\\mathrm{act\\_q}" in normalized
    assert "\\big\\Vert" not in normalized
    assert normalized.startswith("```math")


def test_report_contains_rich_reading_link():
    paper = _make_paper("2601.00001")
    analysis = AnalysisResult(
        one_line_summary="机器人策略图文精读",
        direction="embodied_ai",
        weighted_score=4.5,
    )
    report = generate_daily_report(
        "2026-05-08",
        core_papers=[paper],
        peripheral_papers=[],
        analyses={paper.arxiv_id: analysis},
        deep_research_reports={},
        config={"scoring": {"hot_threshold": 4.0}, "hjfy": {}},
        rich_reading_links={
            paper.arxiv_id: "../rich_readings/2026-05-08/2601.00001/README.md"
        },
    )

    assert "[图文精读](../rich_readings/2026-05-08/2601.00001/README.md)" in report


def test_generate_rich_reading_writes_note_and_manifest(monkeypatch, tmp_path):
    paper = _make_paper("2601.00001", "Rich Reading Paper")
    candidate_path = tmp_path / "figure-000-arch.png"
    candidate_path.write_bytes(b"\x89PNG\r\n" + b"x" * 9000)
    candidate = rr.FigureCandidate(
        filename=candidate_path.name,
        path=candidate_path,
        source="tex",
        original_name="arch.png",
        size_bytes=candidate_path.stat().st_size,
    )

    async def fake_extract_figure_candidates(*args, **kwargs):
        return [candidate]

    calls = []

    async def fake_call_llm(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return json.dumps({
                "figures": [
                    {
                        "filename": candidate.filename,
                        "priority": "high",
                        "reason": "核心架构图",
                        "description": "展示方法结构",
                        "suggested_section": "核心 Insight",
                    }
                ]
            })
        return (
            "# Rich Reading Paper\n\n"
            "## 核心 Insight\n\n"
            "![展示方法结构](figures/figure-000-arch.png)\n"
        )

    monkeypatch.setattr(rr, "extract_figure_candidates", fake_extract_figure_candidates)
    monkeypatch.setattr(rr, "call_llm", fake_call_llm)

    config = {
        "rich_reading": {
            "enabled": True,
            "scope": "core_and_hot",
            "max_papers_per_day": 5,
            "max_candidate_figures": 12,
            "max_selected_figures": 4,
            "storage_dir": str(tmp_path / "rich_readings"),
            "min_figure_kb": 8,
            "max_vision_image_mb": 3,
        },
        "models": {
            "deep_analysis": {"model_id": "test-model", "temperature": 0.1},
            "figure_analysis": {"model_id": "test-model", "temperature": 0.1},
        },
        "scoring": {"hot_threshold": 4.0},
    }

    results = asyncio.run(rr.generate_rich_readings(
        date_str="2026-05-08",
        core_papers=[paper],
        peripheral_papers=[],
        analyses={paper.arxiv_id: AnalysisResult(weighted_score=4.5)},
        deep_research_reports={paper.arxiv_id: "旧版深度分析"},
        pdf_paths={},
        paper_texts={paper.arxiv_id: ("Full paper text with equations.", "tex")},
        config=config,
        api_key="test-key",
    ))

    result = results[paper.arxiv_id]
    readme = tmp_path / "rich_readings" / "2026-05-08" / paper.arxiv_id / "README.md"
    manifest = readme.parent / "figures" / "manifest.json"
    note = readme.read_text(encoding="utf-8")

    assert result.selected_figures[0].filename == candidate.filename
    assert readme.exists()
    assert manifest.exists()
    assert "figures/figure-000-arch.png" in note
    assert ".temp_images" not in note
    assert "/tmp/" not in note
