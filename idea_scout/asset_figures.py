from __future__ import annotations

import hashlib
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable

from .io_utils import clean_text


DEFAULT_FIGURE_TERMS = [
    "figure",
    "fig.",
    "overview",
    "framework",
    "architecture",
    "method",
    "pipeline",
    "posterior",
    "flow",
    "jacobian",
    "algorithm",
    "model",
]


def slugify(value: str, fallback: str = "asset") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:80] or fallback


def source_pdf(asset: Dict[str, Any]) -> tuple[str, str]:
    papers = asset.get("source_papers") if isinstance(asset.get("source_papers"), list) else []
    first = papers[0] if papers and isinstance(papers[0], dict) else {}
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    pdf = asset.get("pdf") if isinstance(asset.get("pdf"), dict) else {}
    local = clean_text(first.get("local_pdf_path") or raw.get("local_pdf_path"))
    url = clean_text(first.get("pdf_url") or first.get("url") or raw.get("pdf_url") or pdf.get("url"), 1000)
    return local, url


def query_terms_for_asset(asset: Dict[str, Any]) -> list[str]:
    review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else {}
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    raw_review = raw.get("llm_review") if isinstance(raw.get("llm_review"), dict) else {}
    text = " ".join(
        [
            clean_text(asset.get("challenge")),
            clean_text(asset.get("solution_pattern")),
            clean_text(asset.get("mechanism")),
            clean_text(review.get("reusable_insight") or raw_review.get("reusable_insight")),
        ]
    ).lower()
    terms = list(DEFAULT_FIGURE_TERMS)
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text):
        if token.lower() not in terms:
            terms.append(token.lower())
        if len(terms) >= 32:
            break
    return terms


def best_figure_page(pages: Dict[int, str], query_terms: Iterable[str] | None = None) -> int:
    terms = [t.lower() for t in (query_terms or DEFAULT_FIGURE_TERMS) if t]
    best_page = 0
    best_score = 0
    for page, text in pages.items():
        lower = text.lower()
        score = 0
        if "figure" in lower or "fig." in lower:
            score += 8
        caption_match = re.search(r"\b(?:figure|fig\.)\s*(\d+)\s*[:.]", lower)
        if caption_match:
            score += 16
            if caption_match.group(1) == "1":
                score += 20
        if "table" in lower and "figure" not in lower:
            score -= 4
        for term in terms:
            if term in lower:
                score += 2 if term not in {"figure", "fig."} else 1
        if page <= 2:
            score += 2
        if score > best_score:
            best_score = score
            best_page = page
    return best_page


def page_visual_score(image_path: Path) -> float:
    try:
        from PIL import Image, ImageStat
    except Exception:
        return 0.0
    try:
        with Image.open(image_path).convert("L") as img:
            img.thumbnail((260, 260))
            pixels = list(img.getdata())
            if not pixels:
                return 0.0
            dark = sum(1 for value in pixels if value < 245) / len(pixels)
            variance = ImageStat.Stat(img).var[0] if pixels else 0.0
    except Exception:
        return 0.0
    return dark * 100.0 + min(variance / 60.0, 20.0)


def best_visual_figure_page(
    pdf_path: Path,
    pages: Dict[int, str],
    preview_dir: Path,
    query_terms: Iterable[str] | None = None,
    timeout: int = 60,
) -> int:
    terms = [t.lower() for t in (query_terms or DEFAULT_FIGURE_TERMS) if t]
    scored: list[tuple[float, float, int]] = []
    for page, text in pages.items():
        preview = render_page(pdf_path, page, preview_dir / f"page-{page}", dpi=45, timeout=timeout)
        if preview is None:
            continue
        lower = text.lower()
        text_score = 0.0
        if "figure" in lower or "fig." in lower:
            text_score += 12.0
        caption_match = re.search(r"\b(?:figure|fig\.)\s*(\d+)\s*[:.]", lower)
        if caption_match:
            text_score += 24.0
            if caption_match.group(1) == "1":
                text_score += 30.0
        if "table" in lower and "figure" not in lower:
            text_score -= 5.0
        for term in terms:
            if term in lower:
                text_score += 1.5 if term not in {"figure", "fig."} else 0.5
        if page <= 2:
            text_score += 1.0
        visual_score = page_visual_score(preview)
        scored.append((text_score, visual_score, page))
    if not scored:
        return best_figure_page(pages, query_terms=query_terms)
    scored.sort(key=lambda item: (-item[0], item[2], -item[1]))
    return scored[0][2]


def store_relative_path(path: Path, store: Path) -> str:
    try:
        return path.resolve().relative_to(store.resolve()).as_posix()
    except ValueError:
        return ""


def download_pdf(url: str, dest: Path, timeout: int = 30) -> Path | None:
    if not url.startswith(("http://", "https://")):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "research-idea-scout-assets"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if not data.startswith(b"%PDF"):
        return None
    dest.write_bytes(data)
    return dest


def pdf_page_text(pdf_path: Path, max_pages: int = 8, timeout: int = 30) -> Dict[int, str]:
    pages: Dict[int, str] = {}
    for page in range(1, max_pages + 1):
        try:
            completed = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), str(pdf_path), "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except Exception:
            continue
        text = clean_text(completed.stdout, 5000)
        if text:
            pages[page] = text
    return pages


def render_page(pdf_path: Path, page: int, output_prefix: Path, dpi: int = 140, timeout: int = 60) -> Path | None:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-singlefile",
            "-f",
            str(page),
            "-l",
            str(page),
            "-r",
            str(dpi),
            str(pdf_path),
            str(output_prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        return None
    out = output_prefix.with_suffix(".png")
    return out if out.exists() else None


def crop_main_figure_from_page(page_image: Path, output_path: Path) -> Path | None:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(page_image).convert("RGB") as img:
            width, height = img.size
            if width < 40 or height < 40:
                return None
            gray = img.convert("L")
            scale = min(1.0, 460.0 / max(width, height))
            small_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            small = gray.resize(small_size)
            pixels = small.load()
            sw, sh = small.size
            visited: set[tuple[int, int]] = set()
            components: list[tuple[float, int, int, int, int]] = []
            min_area = max(24, int(sw * sh * 0.002))

            for y in range(sh):
                for x in range(sw):
                    if (x, y) in visited or pixels[x, y] >= 246:
                        continue
                    stack = [(x, y)]
                    visited.add((x, y))
                    min_x = max_x = x
                    min_y = max_y = y
                    count = 0
                    while stack:
                        cx, cy = stack.pop()
                        count += 1
                        min_x = min(min_x, cx)
                        max_x = max(max_x, cx)
                        min_y = min(min_y, cy)
                        max_y = max(max_y, cy)
                        for nx in (cx - 1, cx, cx + 1):
                            for ny in (cy - 1, cy, cy + 1):
                                if nx < 0 or ny < 0 or nx >= sw or ny >= sh or (nx, ny) in visited:
                                    continue
                                if pixels[nx, ny] >= 246:
                                    continue
                                visited.add((nx, ny))
                                stack.append((nx, ny))
                    box_w = max_x - min_x + 1
                    box_h = max_y - min_y + 1
                    if count < min_area or box_w < sw * 0.08 or box_h < sh * 0.03:
                        continue
                    center_y = (min_y + max_y) / 2
                    if center_y > sh * 0.72:
                        continue
                    components.append((count * box_w * box_h, min_x, min_y, max_x, max_y))

            if not components:
                return None
            components.sort(reverse=True)
            _, min_x, min_y, max_x, max_y = components[0]
            margin = max(8, int(min(sw, sh) * 0.025))
            crop = (
                max(0, int((min_x - margin) / scale)),
                max(0, int((min_y - margin) / scale)),
                min(width, int((max_x + margin) / scale)),
                min(height, int((max_y + margin) / scale)),
            )
            crop_w = crop[2] - crop[0]
            crop_h = crop[3] - crop[1]
            if crop_w < width * 0.22 or crop_h < height * 0.08:
                return None
            if crop_w > width * 0.92 and crop_h > height * 0.82:
                return None
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.crop(crop).save(output_path)
            return output_path
    except Exception:
        return None


def select_important_figure(
    asset: Dict[str, Any],
    store: Path,
    batch: str,
    timeout: int = 30,
    max_pages: int = 8,
    overwrite: bool = False,
) -> Dict[str, str] | None:
    if isinstance(asset.get("figures"), list) and asset["figures"] and not overwrite:
        return None

    local, url = source_pdf(asset)
    asset_id = clean_text(asset.get("asset_id")) or hashlib.sha1(str(asset).encode("utf-8")).hexdigest()[:10]
    slug = slugify(asset_id)
    work_dir = store / batch / "work" / "pdfs"
    pdf_path = Path(local) if local and Path(local).exists() else work_dir / f"{slug}.pdf"
    if not pdf_path.exists():
        try:
            if not download_pdf(url, pdf_path, timeout=timeout):
                return None
        except Exception:
            return None

    pages = pdf_page_text(pdf_path, max_pages=max_pages, timeout=timeout)
    page = best_visual_figure_page(
        pdf_path,
        pages,
        preview_dir=store / batch / "work" / "figure_previews" / slug,
        query_terms=query_terms_for_asset(asset),
        timeout=max(timeout, 60),
    )
    if not page:
        return None

    figure_dir = store / "figures" / batch / slug
    out_prefix = figure_dir / f"page-{page}"
    try:
        page_image_path = render_page(pdf_path, page, out_prefix, timeout=max(timeout, 60))
    except Exception:
        return None
    if page_image_path is None:
        return None
    cropped_path = crop_main_figure_from_page(page_image_path, figure_dir / f"figure-main-page-{page}.png")
    image_path = cropped_path or page_image_path
    figure_source = "pdf_figure_crop" if cropped_path else "pdf_page_render"

    rel = store_relative_path(image_path, store)
    if not rel:
        return None
    page_text = pages.get(page, "")
    caption_match = re.search(r"(Figure|Fig\.)\s*\d+[:.\s].{0,420}", page_text, re.I)
    caption = clean_text(caption_match.group(0) if caption_match else f"Selected page {page} from source paper", 520)
    return {
        "kind": "important",
        "path": rel,
        "page": str(page),
        "caption": caption,
        "why_selected": "This page was selected because its figure text overlaps with the asset mechanism keywords.",
        "source": figure_source,
    }


def attach_important_figure(
    asset: Dict[str, Any],
    store: Path,
    batch: str,
    timeout: int = 30,
    max_pages: int = 8,
    overwrite: bool = False,
) -> Dict[str, Any]:
    fig = select_important_figure(asset, store=store, batch=batch, timeout=timeout, max_pages=max_pages, overwrite=overwrite)
    if not fig:
        return asset
    out = dict(asset)
    figures = [fig]
    out["figures"] = figures[:2]
    return out
