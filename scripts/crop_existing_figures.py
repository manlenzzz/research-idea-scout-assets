#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idea_scout.asset_figures import crop_main_figure_from_page
from idea_scout.assets import read_assets, utc_now, write_assets


def cropped_path_for(page_path: Path) -> Path:
    match = re.search(r"page-(\d+)\.png$", page_path.name)
    page = match.group(1) if match else "1"
    return page_path.with_name(f"figure-main-page-{page}.png")


def crop_batch(store: Path, batch: str) -> dict[str, int]:
    path = store / batch / "assets.jsonl"
    assets = read_assets(path)
    updated = 0
    cropped = 0
    skipped = 0
    out = []
    for asset in assets:
        figures = asset.get("figures") if isinstance(asset.get("figures"), list) else []
        fig = figures[0] if figures and isinstance(figures[0], dict) else None
        rel = str(fig.get("path") or "") if fig else ""
        if not rel:
            skipped += 1
            out.append(asset)
            continue
        if Path(rel).name.startswith("figure-main-page-"):
            skipped += 1
            out.append(asset)
            continue
        page_path = store / rel
        if not page_path.exists():
            skipped += 1
            out.append(asset)
            continue
        target = cropped_path_for(page_path)
        result = crop_main_figure_from_page(page_path, target)
        if result is None:
            skipped += 1
            out.append(asset)
            continue
        next_asset = dict(asset)
        next_figures = [dict(fig)]
        next_figures[0]["path"] = result.resolve().relative_to(store.resolve()).as_posix()
        next_figures[0]["source"] = "pdf_figure_crop"
        next_asset["figures"] = next_figures
        next_asset["updated_at"] = utc_now()
        out.append(next_asset)
        updated += 1
        cropped += 1
    write_assets(path, out)
    return {"batch": batch, "assets": len(assets), "updated": updated, "cropped": cropped, "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser(description="Crop existing whole-page figure screenshots into tighter main-figure images.")
    ap.add_argument("--store", default="/vePFS-Mindverse/user/intern/zhouch/asset_store")
    ap.add_argument("--batch", action="append", required=True)
    args = ap.parse_args()

    store = Path(args.store)
    for batch in args.batch:
        print(json.dumps(crop_batch(store, batch), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
