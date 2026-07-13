#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idea_scout.assets import read_assets, write_assets  # noqa: E402
from idea_scout.high_impact_harvest import existing_store_asset_paths, existing_title_keys, slugify  # noqa: E402
from idea_scout.io_utils import clean_text  # noqa: E402
from idea_scout.storage import resolve_asset_store_argument  # noqa: E402


def parse_batches(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def asset_title(asset: dict) -> str:
    papers = asset.get("source_papers") if isinstance(asset.get("source_papers"), list) else []
    if papers and isinstance(papers[0], dict):
        title = clean_text(papers[0].get("title"))
        if title:
            return title
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    return clean_text(raw.get("title"))


def asset_key(asset: dict) -> str:
    return clean_text(asset.get("asset_id")) or slugify(asset_title(asset), max_chars=140)


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge selected store batches into one deduplicated asset batch.")
    ap.add_argument("--store", default=None, help="Verified shared dataset store; defaults to IDEASCOUT_ASSET_STORE.")
    ap.add_argument("--batches", required=True, help="Comma-separated input batch names.")
    ap.add_argument("--output-batch", required=True)
    ap.add_argument("--exclude-batches", default="", help="Existing batches to exclude by title; defaults to none.")
    args = ap.parse_args()

    store = resolve_asset_store_argument(args.store)
    out_dir = store / args.output_batch
    out_path = out_dir / "assets.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)

    exclude_paths = [store / batch / "assets.jsonl" for batch in parse_batches(args.exclude_batches)]
    exclude_titles = existing_title_keys(path for path in exclude_paths if path.exists())

    merged: dict[str, dict] = {}
    input_counts = Counter()
    skipped = Counter()
    for batch in parse_batches(args.batches):
        path = store / batch / "assets.jsonl"
        if not path.exists():
            skipped["missing_input"] += 1
            continue
        for asset in read_assets(path):
            input_counts[batch] += 1
            title_key = slugify(asset_title(asset), max_chars=140)
            if title_key in exclude_titles:
                skipped["excluded_existing_title"] += 1
                continue
            key = asset_key(asset)
            if not key or key in merged:
                skipped["duplicate"] += 1
                continue
            merged[key] = asset

    write_assets(out_path, merged.values())
    manifest = {
        "batch": args.output_batch,
        "input_batches": parse_batches(args.batches),
        "exclude_batches": parse_batches(args.exclude_batches),
        "input_counts": dict(input_counts),
        "skipped": dict(skipped),
        "n_assets": len(merged),
        "assets_path": str(out_path),
    }
    (out_dir / "MERGE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
