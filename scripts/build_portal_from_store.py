#!/usr/bin/env python3
"""Rebuild the web portal DB from the authoritative asset store and record provenance.

After review runs write <store>/<batch>/assets.jsonl, this script:
  1. imports canonical asset batches into the portal SQLite DB (inside the store),
  2. writes MANIFEST.json files describing imported counts and provenance,
  3. freezes timestamped snapshots of each imported assets.jsonl under snapshots/.

The git repo stays data-free; everything lands on durable GPFS storage.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idea_scout.storage import resolve_asset_store_argument  # noqa: E402
from web.import_jsonl import import_asset_rows, import_rows  # noqa: E402


CANONICAL_BATCHES = (
    "bestpaper",
    "high_impact_ml",
    "high_impact_cvf",
    "high_impact_acl",
    "high_impact_expansion_20260624",
    "high_impact_codefirst_expansion_20260624",
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def load_jsonl(path: Path):
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def source_article_from_asset(asset: dict) -> dict | None:
    sources = asset.get("source_papers")
    if not isinstance(sources, list) or not sources:
        return None
    source = sources[0]
    if not isinstance(source, dict):
        return None
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else {}
    scores = asset.get("scores") if isinstance(asset.get("scores"), dict) else {}
    return {
        "title": source.get("title") or raw.get("title") or "",
        "abstract": source.get("abstract") or raw.get("abstract") or "",
        "venue": source.get("venue") or raw.get("venue") or "",
        "year": source.get("year") or raw.get("year") or 0,
        "url": source.get("url") or source.get("pdf_url") or raw.get("url") or raw.get("pdf_url") or "",
        "authors": source.get("authors") or raw.get("authors") or "",
        "priority": review.get("verdict") or "asset_source",
        "profile_name": asset.get("profile_name") or raw.get("profile_name") or "",
        "idea_core": review.get("reusable_insight") or asset.get("challenge") or "",
        "transferable_mechanism": review.get("method") or asset.get("mechanism") or asset.get("solution_pattern") or "",
        "fit_reason": review.get("why_it_works") or asset.get("why_it_is_hard") or "",
        "risk_or_limitation": "; ".join(str(x) for x in asset.get("non_transferable_parts") or []),
        "rank_score": scores.get("asset_score") or 0.0,
        "score_overall_fit": scores.get("transferability") or 0.0,
        "score_theory_novelty": scores.get("evidence_strength") or 0.0,
        "scores": scores,
    }


def write_source_articles(assets: list[dict], db_path: Path) -> int:
    articles = []
    seen = set()
    for asset in assets:
        article = source_article_from_asset(asset)
        if not article:
            continue
        key = (
            str(article.get("title") or "").strip().lower(),
            str(article.get("venue") or "").strip().lower(),
            str(article.get("year") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        articles.append(article)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as f:
        temp_path = Path(f.name)
        for article in articles:
            f.write(json.dumps(article, ensure_ascii=False) + "\n")
    try:
        return import_rows(temp_path, db_path, replace=True)
    finally:
        temp_path.unlink(missing_ok=True)


def parse_batches(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def summarize_batch(args: argparse.Namespace, batch: str, assets: list[dict], db_path: Path, n_assets: int, n_articles: int) -> dict:
    verdicts = Counter()
    quality = Counter()
    code = Counter()
    for a in assets:
        r = a.get("llm_review") or {}
        verdicts[r.get("verdict") or "not_reviewed"] += 1
        if r.get("asset_quality") is not None:
            quality[str(r.get("asset_quality"))] += 1
        code[(a.get("code") or {}).get("status") or "none"] += 1

    return {
        "batch": batch,
        "built_at": utc_stamp(),
        "model": args.model,
        "source_input": f"{batch}/assets.jsonl",
        "n_assets": len(assets),
        "n_assets_imported": n_assets,
        "n_articles_imported": n_articles,
        "verdicts": dict(verdicts),
        "asset_quality_hist": dict(quality),
        "code_status": dict(code),
        "portal_db": str(db_path),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=None, help="Verified shared dataset store; defaults to IDEASCOUT_ASSET_STORE.")
    ap.add_argument("--batch", default="", help="Single batch to import. Defaults to all canonical batches.")
    ap.add_argument(
        "--batches",
        default=",".join(CANONICAL_BATCHES),
        help="Comma-separated batches to import when --batch is not set.",
    )
    ap.add_argument("--papers", default="", help="Optional paper-level JSONL for the articles table.")
    ap.add_argument("--model", default="claude-opus-4-8 (claude -p)")
    args = ap.parse_args()

    store = resolve_asset_store_argument(args.store)
    db_path = store / "portal.db"
    batches = [args.batch] if args.batch else parse_batches(args.batches)
    if not batches:
        raise SystemExit("no batches selected")

    manifests = []
    all_assets = []
    total_assets = 0

    stamp = utc_stamp()
    for index, batch in enumerate(batches):
        batch_dir = store / batch
        assets_path = batch_dir / "assets.jsonl"
        if not assets_path.exists():
            raise SystemExit(f"assets file not found: {assets_path}")

        n_assets = import_asset_rows(assets_path, db_path, replace=(index == 0))
        total_assets += n_assets
        assets = load_jsonl(assets_path)
        all_assets.extend(assets)
        manifest = summarize_batch(args, batch, assets, db_path, n_assets, 0)
        manifest["built_at"] = stamp
        (batch_dir / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        snap_dir = batch_dir / "snapshots" / stamp
        snap_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets_path, snap_dir / "assets.jsonl")
        (snap_dir / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifests.append(manifest)

    n_articles = import_rows(Path(args.papers), db_path, replace=True) if args.papers else write_source_articles(all_assets, db_path)

    combined = {
        "asset_library": "canonical",
        "built_at": stamp,
        "batches": batches,
        "n_assets_imported": total_assets,
        "n_articles_imported": n_articles,
        "portal_db": str(db_path),
        "batch_manifests": manifests,
    }
    (store / "MANIFEST.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(combined, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
