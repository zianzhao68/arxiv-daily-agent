from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import httpx

from .config import DATA_DIR, load_prompt
from .llm_client import call_llm
from .models import AnalysisResult, ArxivPaper

logger = logging.getLogger(__name__)

EPRINT_URL = "https://arxiv.org/e-print/{id}"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".eps", ".svg"}
DIRECT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_MIN_FIGURE_BYTES = 8 * 1024
DEFAULT_MAX_VISION_IMAGE_BYTES = 3 * 1024 * 1024


@dataclass
class FigureCandidate:
    filename: str
    path: Path
    source: str
    original_name: str
    size_bytes: int
    width: int = 0
    height: int = 0

    def to_manifest(self) -> dict:
        return {
            "filename": self.filename,
            "source": self.source,
            "original_name": self.original_name,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class SelectedFigure:
    filename: str
    priority: str
    reason: str
    description: str = ""
    suggested_section: str = ""

    def to_manifest(self) -> dict:
        return {
            "filename": self.filename,
            "priority": self.priority,
            "reason": self.reason,
            "description": self.description,
            "suggested_section": self.suggested_section,
        }


@dataclass
class RichReadingResult:
    arxiv_id: str
    data_path: str
    report_link: str
    selected_figures: list[SelectedFigure]
    generated_at: str

    def selected_figure_entries(self) -> list[dict]:
        return [fig.to_manifest() for fig in self.selected_figures]


def select_rich_reading_papers(
    core_papers: list[ArxivPaper],
    peripheral_papers: list[ArxivPaper],
    analyses: dict[str, AnalysisResult],
    hot_threshold: float,
    max_papers: int,
    scope: str = "core_and_hot",
) -> list[ArxivPaper]:
    if max_papers <= 0:
        return []

    def score(paper: ArxivPaper) -> float:
        return analyses.get(paper.arxiv_id, AnalysisResult()).weighted_score

    core_sorted = sorted(core_papers, key=score, reverse=True)
    peripheral_sorted = sorted(peripheral_papers, key=score, reverse=True)

    if scope == "core_only":
        selected = core_sorted
    elif scope == "all_relevant":
        selected = sorted(core_papers + peripheral_papers, key=score, reverse=True)
    else:
        hot_peripheral = [
            paper for paper in peripheral_sorted if score(paper) >= hot_threshold
        ]
        selected = core_sorted + hot_peripheral

    return selected[:max_papers]


def parse_figure_selection(
    raw: str,
    candidates: list[FigureCandidate],
    max_selected: int,
) -> list[SelectedFigure]:
    text = raw.strip()
    for prefix in ("```json", "```"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Figure selection response is not valid JSON")
        return []

    if isinstance(data, dict):
        items = data.get("figures", [])
    else:
        items = data
    if not isinstance(items, list):
        return []

    candidate_names = {candidate.filename for candidate in candidates}
    selected: list[SelectedFigure] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename", "")).strip()
        if filename not in candidate_names:
            continue
        priority = str(item.get("priority", "")).strip().lower()
        if priority in ("high", "red", "core", "高", "高优先级", "🔴"):
            priority = "high"
        elif priority in ("medium", "yellow", "mid", "中", "中优先级", "🟡"):
            priority = "medium"
        elif priority in ("low", "skip", "低", "低优先级", "⚪"):
            continue
        else:
            continue
        selected.append(
            SelectedFigure(
                filename=filename,
                priority=priority,
                reason=str(item.get("reason", "")).strip(),
                description=str(item.get("description", "")).strip(),
                suggested_section=str(item.get("suggested_section", "")).strip(),
            )
        )
        if len(selected) >= max_selected:
            break

    return selected


def fallback_select_figures(
    candidates: list[FigureCandidate],
    max_selected: int,
) -> list[SelectedFigure]:
    selected: list[SelectedFigure] = []
    for candidate in candidates[:max_selected]:
        selected.append(
            SelectedFigure(
                filename=candidate.filename,
                priority="medium",
                reason="Fallback selection because visual JSON selection failed.",
                description=f"Candidate extracted from {candidate.source}.",
                suggested_section="关键图表解读",
            )
        )
    return selected


async def generate_rich_readings(
    date_str: str,
    core_papers: list[ArxivPaper],
    peripheral_papers: list[ArxivPaper],
    analyses: dict[str, AnalysisResult],
    deep_research_reports: dict[str, str],
    pdf_paths: dict[str, Path],
    paper_texts: dict[str, tuple[str, str]],
    config: dict,
    api_key: str,
) -> dict[str, RichReadingResult]:
    rich_config = config.get("rich_reading", {})
    if not rich_config.get("enabled", False):
        return {}

    hot_threshold = config.get("scoring", {}).get("hot_threshold", 4.0)
    selected_papers = select_rich_reading_papers(
        core_papers=core_papers,
        peripheral_papers=peripheral_papers,
        analyses=analyses,
        hot_threshold=hot_threshold,
        max_papers=int(rich_config.get("max_papers_per_day", 5)),
        scope=str(rich_config.get("scope", "core_and_hot")),
    )
    if not selected_papers:
        logger.info("Rich reading skipped: no selected papers")
        return {}

    model_config = config.get("models", {}).get("figure_analysis") or config["models"]["deep_analysis"]
    output_root = _resolve_storage_dir(str(rich_config.get("storage_dir", "data/rich_readings")))
    output_root = output_root / date_str
    output_root.mkdir(parents=True, exist_ok=True)

    logger.info("Rich reading selected papers: %s", ", ".join(p.arxiv_id for p in selected_papers))

    results: dict[str, RichReadingResult] = {}
    for paper in selected_papers:
        try:
            result = await generate_rich_reading_for_paper(
                paper=paper,
                analysis=analyses.get(paper.arxiv_id, AnalysisResult()),
                deep_research=deep_research_reports.get(paper.arxiv_id, ""),
                pdf_path=pdf_paths.get(paper.arxiv_id),
                paper_text=paper_texts.get(paper.arxiv_id),
                output_dir=output_root / paper.arxiv_id,
                rich_config=rich_config,
                model_config=model_config,
                api_key=api_key,
            )
        except Exception:
            logger.exception("Rich reading failed for %s", paper.arxiv_id)
            continue
        results[paper.arxiv_id] = result

    return results


async def generate_rich_reading_for_paper(
    paper: ArxivPaper,
    analysis: AnalysisResult,
    deep_research: str,
    pdf_path: Path | None,
    paper_text: tuple[str, str] | None,
    output_dir: Path,
    rich_config: dict,
    model_config: dict,
    api_key: str,
) -> RichReadingResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"rich-reading-{paper.arxiv_id}-") as tmp:
        work_dir = Path(tmp)
        candidates = await extract_figure_candidates(
            paper=paper,
            pdf_path=pdf_path,
            work_dir=work_dir,
            max_candidates=int(rich_config.get("max_candidate_figures", 12)),
            min_figure_bytes=int(rich_config.get("min_figure_kb", 8)) * 1024,
        )
        selected = await select_figures_with_llm(
            candidates=candidates,
            paper=paper,
            model_config=model_config,
            api_key=api_key,
            max_selected=int(rich_config.get("max_selected_figures", 4)),
            max_image_bytes=int(rich_config.get("max_vision_image_mb", 3)) * 1024 * 1024,
        )
        copied_figures = _copy_selected_figures(candidates, selected, figures_dir)
        selected = [fig for fig in selected if fig.filename in copied_figures]

    note = await generate_rich_note(
        paper=paper,
        analysis=analysis,
        deep_research=deep_research,
        paper_text=paper_text,
        selected_figures=selected,
        figures_dir=figures_dir,
        model_config=model_config,
        api_key=api_key,
    )
    note = _normalize_github_markdown(note)
    note = _sanitize_note_markdown(note, output_dir)
    note = _ensure_selected_figures_present(note, selected)
    readme_path = output_dir / "README.md"
    readme_path.write_text(note, encoding="utf-8")

    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "generated_at": generated_at,
        "candidates": [candidate.to_manifest() for candidate in candidates],
        "selected_figures": [fig.to_manifest() for fig in selected],
    }
    (figures_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    data_path, report_link = _data_and_report_paths(readme_path)
    logger.info("Rich reading saved for %s: %s", paper.arxiv_id, readme_path)
    return RichReadingResult(
        arxiv_id=paper.arxiv_id,
        data_path=data_path,
        report_link=report_link,
        selected_figures=selected,
        generated_at=generated_at,
    )


async def extract_figure_candidates(
    paper: ArxivPaper,
    pdf_path: Path | None,
    work_dir: Path,
    max_candidates: int,
    min_figure_bytes: int = DEFAULT_MIN_FIGURE_BYTES,
) -> list[FigureCandidate]:
    candidates_dir = work_dir / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[FigureCandidate] = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=90) as client:
        try:
            response = await client.get(EPRINT_URL.format(id=paper.arxiv_id))
            response.raise_for_status()
            source_candidates = _extract_source_image_candidates(
                blob=response.content,
                candidates_dir=candidates_dir,
                max_candidates=max_candidates,
                min_figure_bytes=min_figure_bytes,
            )
            candidates.extend(source_candidates)
        except Exception as exc:
            logger.info("TeX/source figure extraction skipped for %s: %s", paper.arxiv_id, exc)

    if len(candidates) < max_candidates and pdf_path and pdf_path.exists():
        remaining = max_candidates - len(candidates)
        candidates.extend(
            _extract_pdf_image_candidates(
                pdf_path=pdf_path,
                candidates_dir=candidates_dir,
                start_index=len(candidates),
                max_candidates=remaining,
                min_figure_bytes=min_figure_bytes,
            )
        )

    filtered = _filter_candidates(candidates, min_figure_bytes)
    logger.info("Figure candidates for %s: %d", paper.arxiv_id, len(filtered))
    return filtered[:max_candidates]


async def select_figures_with_llm(
    candidates: list[FigureCandidate],
    paper: ArxivPaper,
    model_config: dict,
    api_key: str,
    max_selected: int,
    max_image_bytes: int = DEFAULT_MAX_VISION_IMAGE_BYTES,
) -> list[SelectedFigure]:
    if not candidates or max_selected <= 0:
        return []

    system_prompt = load_prompt("figure_selection.txt")
    user_content = _build_figure_selection_content(
        paper=paper,
        candidates=candidates,
        max_selected=max_selected,
        max_image_bytes=max_image_bytes,
    )
    try:
        raw = await call_llm(
            model=model_config["model_id"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=model_config.get("temperature", 0.1),
            api_key=api_key,
        )
        selected = parse_figure_selection(raw, candidates, max_selected)
    except Exception:
        logger.exception("Visual figure selection failed for %s", paper.arxiv_id)
        selected = []

    if not selected:
        selected = fallback_select_figures(candidates, max_selected)
    return selected


async def generate_rich_note(
    paper: ArxivPaper,
    analysis: AnalysisResult,
    deep_research: str,
    paper_text: tuple[str, str] | None,
    selected_figures: list[SelectedFigure],
    figures_dir: Path,
    model_config: dict,
    api_key: str,
) -> str:
    system_prompt = load_prompt("rich_reading.txt")
    user_content = _build_rich_note_content(
        paper=paper,
        analysis=analysis,
        deep_research=deep_research,
        paper_text=paper_text,
        selected_figures=selected_figures,
        figures_dir=figures_dir,
    )
    try:
        raw = await call_llm(
            model=model_config["model_id"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=model_config.get("temperature", 0.2),
            api_key=api_key,
        )
        return raw.strip()
    except Exception:
        logger.exception("Rich note generation failed for %s", paper.arxiv_id)
        return _fallback_rich_note(paper, analysis, deep_research, selected_figures)


def _resolve_storage_dir(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == DATA_DIR.name:
        return DATA_DIR.parent / path
    return DATA_DIR / path


def _data_and_report_paths(readme_path: Path) -> tuple[str, str]:
    try:
        data_rel = PurePosixPath(readme_path.relative_to(DATA_DIR).as_posix())
        return data_rel.as_posix(), (PurePosixPath("..") / data_rel).as_posix()
    except ValueError:
        value = readme_path.as_posix()
        return value, value


def _extract_source_image_candidates(
    blob: bytes,
    candidates_dir: Path,
    max_candidates: int,
    min_figure_bytes: int,
) -> list[FigureCandidate]:
    candidates: list[FigureCandidate] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            members = [
                member for member in tar.getmembers()
                if member.isfile() and Path(member.name).suffix.lower() in IMAGE_EXTENSIONS
            ]
            members.sort(key=lambda member: member.size, reverse=True)
            for member in members:
                if len(candidates) >= max_candidates:
                    break
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read()
                candidate = _materialize_candidate(
                    raw=raw,
                    original_name=Path(member.name).name,
                    source="tex",
                    candidates_dir=candidates_dir,
                    index=len(candidates),
                    min_figure_bytes=min_figure_bytes,
                )
                if candidate:
                    candidates.append(candidate)
    except tarfile.TarError:
        return []
    return candidates


def _extract_pdf_image_candidates(
    pdf_path: Path,
    candidates_dir: Path,
    start_index: int,
    max_candidates: int,
    min_figure_bytes: int,
) -> list[FigureCandidate]:
    try:
        import pymupdf
    except ImportError:
        logger.warning("pymupdf not installed; skipping PDF figure extraction")
        return []

    candidates: list[FigureCandidate] = []
    seen_xrefs: set[int] = set()
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        logger.warning("Failed to open PDF for figure extraction %s: %s", pdf_path, exc)
        return []

    try:
        for page_index, page in enumerate(doc):
            if len(candidates) >= max_candidates:
                break
            for image in page.get_images(full=True):
                if len(candidates) >= max_candidates:
                    break
                xref = image[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    base_image = doc.extract_image(xref)
                except Exception:
                    continue
                ext = base_image.get("ext", "png")
                raw = base_image.get("image", b"")
                candidate = _materialize_candidate(
                    raw=raw,
                    original_name=f"page-{page_index + 1}-xref-{xref}.{ext}",
                    source="pdf-image",
                    candidates_dir=candidates_dir,
                    index=start_index + len(candidates),
                    min_figure_bytes=min_figure_bytes,
                )
                if candidate:
                    candidates.append(candidate)

        if candidates:
            return candidates

        pages_to_render = min(len(doc), max_candidates, 6)
        for page_index in range(pages_to_render):
            page = doc[page_index]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            out_path = candidates_dir / f"figure-{start_index + len(candidates):03d}-page-{page_index + 1}.png"
            pix.save(out_path)
            candidate = _candidate_from_path(
                out_path,
                source="pdf-page",
                original_name=f"page-{page_index + 1}.png",
                min_figure_bytes=min_figure_bytes,
            )
            if candidate:
                candidates.append(candidate)
    finally:
        doc.close()

    return candidates


def _materialize_candidate(
    raw: bytes,
    original_name: str,
    source: str,
    candidates_dir: Path,
    index: int,
    min_figure_bytes: int,
) -> FigureCandidate | None:
    ext = Path(original_name).suffix.lower()
    slug = _slugify(Path(original_name).stem) or "figure"
    raw_path = candidates_dir / f"raw-{index:03d}{ext}"
    raw_path.write_bytes(raw)

    if ext in DIRECT_IMAGE_EXTENSIONS:
        out_path = candidates_dir / f"figure-{index:03d}-{slug}{ext}"
        shutil.copy2(raw_path, out_path)
    else:
        out_path = candidates_dir / f"figure-{index:03d}-{slug}.png"
        converted = _convert_to_png(raw_path, out_path)
        if not converted:
            raw_path.unlink(missing_ok=True)
            return None

    raw_path.unlink(missing_ok=True)
    return _candidate_from_path(out_path, source, original_name, min_figure_bytes)


def _candidate_from_path(
    path: Path,
    source: str,
    original_name: str,
    min_figure_bytes: int,
) -> FigureCandidate | None:
    if not path.exists() or path.stat().st_size < min_figure_bytes:
        path.unlink(missing_ok=True)
        return None
    width, height = _image_dimensions(path)
    if width and height and (width < 120 or height < 80):
        path.unlink(missing_ok=True)
        return None
    return FigureCandidate(
        filename=path.name,
        path=path,
        source=source,
        original_name=original_name,
        size_bytes=path.stat().st_size,
        width=width,
        height=height,
    )


def _filter_candidates(
    candidates: list[FigureCandidate],
    min_figure_bytes: int = DEFAULT_MIN_FIGURE_BYTES,
) -> list[FigureCandidate]:
    filtered = [
        candidate for candidate in candidates
        if candidate.path.exists() and candidate.path.stat().st_size >= min_figure_bytes
    ]
    filtered.sort(key=lambda candidate: candidate.size_bytes, reverse=True)
    return filtered


def _convert_to_png(src: Path, dest: Path) -> bool:
    if src.suffix.lower() == ".pdf":
        cmd = ["pdftoppm", "-png", "-r", "220", "-singlefile", str(src), str(dest.with_suffix(""))]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=45)
            return dest.exists()
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

    for executable in ("magick", "convert"):
        cmd = [executable, str(src), str(dest)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=45)
            return dest.exists()
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        import pymupdf
        doc = pymupdf.open(path)
        try:
            rect = doc[0].rect
            return int(rect.width), int(rect.height)
        finally:
            doc.close()
    except Exception:
        return 0, 0


def _build_figure_selection_content(
    paper: ArxivPaper,
    candidates: list[FigureCandidate],
    max_selected: int,
    max_image_bytes: int,
) -> list[dict]:
    manifest = [
        {
            "filename": candidate.filename,
            "source": candidate.source,
            "original_name": candidate.original_name,
            "size_kb": round(candidate.size_bytes / 1024, 1),
            "width": candidate.width,
            "height": candidate.height,
        }
        for candidate in candidates
    ]
    text = (
        f"Paper title: {paper.title}\n"
        f"arXiv ID: {paper.arxiv_id}\n"
        f"Abstract:\n{paper.abstract}\n\n"
        f"Select at most {max_selected} figures. Candidate metadata:\n"
        f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
    )
    content: list[dict] = [{"type": "text", "text": text}]
    for candidate in candidates:
        if candidate.size_bytes > max_image_bytes:
            content.append({
                "type": "text",
                "text": f"Image omitted from payload because it is too large: {candidate.filename}",
            })
            continue
        content.append({
            "type": "text",
            "text": f"Candidate filename: {candidate.filename}",
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_data_url(candidate.path)},
        })
    return content


def _build_rich_note_content(
    paper: ArxivPaper,
    analysis: AnalysisResult,
    deep_research: str,
    paper_text: tuple[str, str] | None,
    selected_figures: list[SelectedFigure],
    figures_dir: Path,
) -> list[dict] | str:
    figure_lines = []
    for figure in selected_figures:
        figure_lines.append(
            "- "
            f"filename={figure.filename}; priority={figure.priority}; "
            f"reason={figure.reason}; description={figure.description}; "
            f"markdown=![{figure.description or figure.filename}](figures/{figure.filename})"
        )

    text, source = paper_text or (paper.abstract, "abstract")
    user_text = (
        f"## Paper Metadata\n"
        f"Title: {paper.title}\n"
        f"Authors: {', '.join(paper.authors)}\n"
        f"arXiv ID: {paper.arxiv_id}\n"
        f"arXiv URL: {paper.abs_url}\n"
        f"PDF URL: {paper.pdf_url}\n"
        f"Categories: {', '.join(paper.categories)}\n\n"
        f"## Existing Daily Analysis\n"
        f"One-line summary: {analysis.one_line_summary}\n"
        f"Detailed summary:\n{analysis.detailed_summary}\n"
        f"Key terms: {', '.join(analysis.key_terms)}\n"
        f"Scores: novelty={analysis.novelty_score}, impact={analysis.impact_score}, "
        f"reproducibility={analysis.reproducibility_score}, focus={analysis.focus_relevance_score}, "
        f"weighted={analysis.weighted_score}\n\n"
        f"## Existing DeepResearch Draft\n"
        f"{deep_research or 'None'}\n\n"
        f"## Selected Figures\n"
        f"{chr(10).join(figure_lines) if figure_lines else 'No selected figures.'}\n\n"
        f"## Full Paper Text (source={source})\n"
        f"{text}\n"
    )
    if not selected_figures:
        return user_text

    content: list[dict] = [{"type": "text", "text": user_text}]
    for figure in selected_figures:
        figure_path = figures_dir / figure.filename
        if not figure_path.exists() or figure_path.stat().st_size > DEFAULT_MAX_VISION_IMAGE_BYTES:
            continue
        content.append({"type": "text", "text": f"Selected figure: {figure.filename}"})
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_data_url(figure_path)},
        })
    return content


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _copy_selected_figures(
    candidates: list[FigureCandidate],
    selected_figures: list[SelectedFigure],
    figures_dir: Path,
) -> set[str]:
    by_name = {candidate.filename: candidate for candidate in candidates}
    copied: set[str] = set()
    for figure in selected_figures:
        candidate = by_name.get(figure.filename)
        if not candidate:
            continue
        dest = figures_dir / candidate.filename
        shutil.copy2(candidate.path, dest)
        copied.add(candidate.filename)
    return copied


def _sanitize_note_markdown(note: str, output_dir: Path) -> str:
    text = note.replace(str(output_dir), ".")
    lines = [
        line for line in text.splitlines()
        if ".temp_images" not in line and "/tmp/" not in line and "\\tmp\\" not in line
    ]
    return "\n".join(lines).strip() + "\n"


def _normalize_github_markdown(note: str) -> str:
    text = note.replace("\r\n", "\n").replace("\r", "\n")
    text = _normalize_heading_spacing(text)
    text = _normalize_math_macros(text)
    text = _convert_dollar_math_blocks_to_fenced(text)
    return text


def _normalize_heading_spacing(text: str) -> str:
    text = re.sub(r"(?<!\n)(#{1,6}\s+)", r"\n\n\1", text)
    text = re.sub(r"(?m)^(#{1,6}\s+.+)\n(?!\n)", r"\1\n\n", text)
    return text


def _normalize_math_macros(text: str) -> str:
    text = re.sub(r"\\textsc\{([^{}]+)\}", r"\\mathrm{\1}", text)
    text = text.replace(r"\big\Vert", r"\Vert")
    return text


def _convert_dollar_math_blocks_to_fenced(text: str) -> str:
    text = re.sub(
        r"\$\$\s*([^$\n]+?)\s*\$\$",
        lambda match: f"\n\n```math\n{match.group(1).strip()}\n```\n\n",
        text,
    )
    lines = text.splitlines()
    out: list[str] = []
    in_math = False

    for line in lines:
        stripped = line.strip()
        if stripped == "$$":
            if in_math:
                out.append("```")
                out.append("")
                in_math = False
            else:
                if out and out[-1].strip():
                    out.append("")
                out.append("```math")
                in_math = True
            continue

        single_line = re.fullmatch(r"\s*\$\$\s*(.+?)\s*\$\$\s*", line)
        if single_line and not in_math:
            if out and out[-1].strip():
                out.append("")
            out.append("```math")
            out.append(single_line.group(1))
            out.append("```")
            out.append("")
            continue

        out.append(line)

    if in_math:
        out.append("```")
        out.append("")

    return "\n".join(out).strip() + "\n"


def _ensure_selected_figures_present(note: str, selected_figures: list[SelectedFigure]) -> str:
    missing = [
        figure for figure in selected_figures
        if f"figures/{figure.filename}" not in note
    ]
    if not missing:
        return note
    additions = ["", "## 关键图表解读"]
    for figure in missing:
        caption = figure.description or figure.reason or figure.filename
        additions.extend([
            "",
            f"![{caption}](figures/{figure.filename})",
            "",
            f"*{caption}*",
        ])
    return note.rstrip() + "\n" + "\n".join(additions) + "\n"


def _fallback_rich_note(
    paper: ArxivPaper,
    analysis: AnalysisResult,
    deep_research: str,
    selected_figures: list[SelectedFigure],
) -> str:
    figure_block = "\n\n".join(
        f"![{fig.description or fig.filename}](figures/{fig.filename})\n\n"
        f"*{fig.reason or fig.description or fig.filename}*"
        for fig in selected_figures
    ) or "暂无精选图表。"
    return f"""# {paper.title}

## 基本信息

- arXiv: [{paper.arxiv_id}]({paper.abs_url})
- Authors: {", ".join(paper.authors)}
- Categories: {", ".join(paper.categories)}

## 研究问题

{analysis.one_line_summary or paper.abstract}

## 任务与挑战

{analysis.detailed_summary or paper.abstract}

## 核心 Insight

这篇论文的核心价值需要结合原文继续复查。当前 fallback 笔记保留了日报分析、DeepResearch 草稿和已筛选图表，避免自动流程中断。

## 方法与公式

{deep_research or "DeepResearch 未生成；请回看论文正文中的方法章节与关键公式。"}

## 贡献拆解

- 关键术语：{", ".join(analysis.key_terms) if analysis.key_terms else "未提取"}
- 加权评分：{analysis.weighted_score}/5.0

## 关键图表解读

{figure_block}

## 实验与消融

请优先核对主结果表、消融实验和真实/仿真任务设置；若图表已选中，应结合上方图片逐项复查。

## 局限性

当前自动精读未能成功调用完整图文生成模型，因此局限性需要结合原文实验设置进一步人工确认。

## 个人研究判断

若该论文与 World Models assisting Embodied AI、VLA、robot policy 或 sim-to-real 强相关，建议进入人工精读队列。
"""


def _slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:48]
