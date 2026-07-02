#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idea_scout.asset_figures import attach_important_figure
from idea_scout.assets import read_assets


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract important figures for a slice of one asset batch.")
    ap.add_argument("--store", default="/vePFS-Mindverse/user/intern/zhouch/asset_store")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--max-pages", type=int, default=8)
    args = ap.parse_args()

    store = Path(args.store)
    assets = read_assets(store / args.batch / "assets.jsonl")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as fh:
        for index, asset in enumerate(assets[args.start : args.end], start=args.start):
            enhanced = attach_important_figure(
                asset,
                store=store,
                batch=args.batch,
                timeout=args.timeout,
                max_pages=args.max_pages,
                overwrite=True,
            )
            figures = enhanced.get("figures") if isinstance(enhanced.get("figures"), list) else []
            row = {
                "index": index,
                "asset_id": enhanced.get("asset_id"),
                "figures": figures,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(
                f"[{args.batch}:{args.start}-{args.end}] {index + 1}/{len(assets)} "
                f"asset={enhanced.get('asset_id')} figures={len(figures)}",
                flush=True,
            )


if __name__ == "__main__":
    main()
