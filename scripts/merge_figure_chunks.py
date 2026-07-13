#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idea_scout.assets import read_assets, utc_now, write_assets
from idea_scout.storage import resolve_asset_store_argument


def load_chunk_results(paths: list[Path]) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            asset_id = row.get("asset_id")
            figures = row.get("figures") if isinstance(row.get("figures"), list) else []
            if asset_id and figures:
                results[asset_id] = [x for x in figures if isinstance(x, dict)][:1]
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge chunked figure extraction outputs into one batch assets.jsonl.")
    ap.add_argument("--store", default=None, help="Verified shared dataset store; defaults to IDEASCOUT_ASSET_STORE.")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--chunks-dir", required=True)
    args = ap.parse_args()

    store = resolve_asset_store_argument(args.store)
    batch_path = store / args.batch / "assets.jsonl"
    assets = read_assets(batch_path)
    chunk_paths = sorted(Path(args.chunks_dir).glob(f"{args.batch}.*.jsonl"))
    results = load_chunk_results(chunk_paths)

    merged = 0
    out = []
    for asset in assets:
        asset_id = asset.get("asset_id")
        if asset_id in results:
            asset = dict(asset)
            asset["figures"] = results[asset_id]
            asset["updated_at"] = utc_now()
            merged += 1
        out.append(asset)

    write_assets(batch_path, out)
    print(json.dumps({"batch": args.batch, "chunks": len(chunk_paths), "figures_merged": merged}, ensure_ascii=False))


if __name__ == "__main__":
    main()
