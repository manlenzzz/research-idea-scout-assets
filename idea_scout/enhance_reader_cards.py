from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from .asset_figures import attach_important_figure
from .assets import read_assets, utc_now, write_assets
from .reader_cards import make_fallback_reader_card, sanitize_reader_card


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def enhance_one(
    asset: Dict[str, Any],
    store: Path,
    batch: str,
    with_figures: bool = False,
    figure_timeout: int = 30,
    max_pages: int = 8,
    overwrite_reader: bool = False,
    overwrite_figures: bool = False,
) -> Dict[str, Any]:
    out = dict(asset)
    existing = out.get("reader_card") if isinstance(out.get("reader_card"), dict) else None
    if overwrite_reader or not existing:
        out["reader_card"] = make_fallback_reader_card(out)
    else:
        out["reader_card"] = sanitize_reader_card(existing, out)

    if with_figures:
        out = attach_important_figure(
            out,
            store=store,
            batch=batch,
            timeout=figure_timeout,
            max_pages=max_pages,
            overwrite=overwrite_figures,
        )
    out["updated_at"] = utc_now()
    return out


def enhance_assets(
    assets: Iterable[Dict[str, Any]],
    store: Path,
    batch: str,
    with_figures: bool = False,
    figure_timeout: int = 30,
    max_pages: int = 8,
    overwrite_reader: bool = False,
    overwrite_figures: bool = False,
    limit: int = 0,
    progress: Callable[[int, Dict[str, int], Dict[str, Any]], None] | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    out: List[Dict[str, Any]] = []
    stats = {"enhanced": 0, "figures": 0, "skipped_by_limit": 0}
    for asset in assets:
        if limit and stats["enhanced"] >= limit:
            out.append(asset)
            stats["skipped_by_limit"] += 1
            continue
        before_figures = len(asset.get("figures") or []) if isinstance(asset.get("figures"), list) else 0
        enhanced = enhance_one(
            asset,
            store=store,
            batch=batch,
            with_figures=with_figures,
            figure_timeout=figure_timeout,
            max_pages=max_pages,
            overwrite_reader=overwrite_reader,
            overwrite_figures=overwrite_figures,
        )
        after_figures = len(enhanced.get("figures") or []) if isinstance(enhanced.get("figures"), list) else 0
        if after_figures > before_figures:
            stats["figures"] += 1
        out.append(enhanced)
        stats["enhanced"] += 1
        if progress:
            progress(stats["enhanced"], stats, enhanced)
    return out, stats


def snapshot_assets(path: Path, snapshot_dir: Path) -> Path:
    stamp = utc_stamp()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    dest = snapshot_dir / f"assets.before-reader-card.{stamp}.jsonl"
    shutil.copy2(path, dest)
    return dest


def atomic_write_assets(path: Path, assets: Iterable[Dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".reader.tmp")
    write_assets(tmp_path, assets)
    tmp_path.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Add reader_card and selected paper figures to asset JSONL files.")
    ap.add_argument("--store", default="/vePFS-Mindverse/user/intern/zhouch/asset_store")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--input", default="", help="Defaults to <store>/<batch>/assets.jsonl")
    ap.add_argument("--output", default="", help="Defaults to in-place atomic replacement.")
    ap.add_argument("--with-figures", action="store_true")
    ap.add_argument("--figure-timeout", type=int, default=30)
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--overwrite-reader", action="store_true")
    ap.add_argument("--overwrite-figures", action="store_true")
    ap.add_argument("--save-every", type=int, default=25, help="For in-place runs, atomically persist after this many processed assets.")
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args()

    store = Path(args.store)
    batch_dir = store / args.batch
    input_path = Path(args.input) if args.input else batch_dir / "assets.jsonl"
    output_path = Path(args.output) if args.output else input_path
    if not input_path.exists():
        raise SystemExit(f"assets file not found: {input_path}")

    snapshot = ""
    if not args.no_snapshot and output_path == input_path:
        snapshot = str(snapshot_assets(input_path, batch_dir / "snapshots"))

    assets = read_assets(input_path)
    progress_state = {"last_saved": 0, "total": len(assets)}

    def progress(done: int, stats: Dict[str, int], enhanced_asset: Dict[str, Any]) -> None:
        if not args.with_figures:
            return
        figure_count = len(enhanced_asset.get("figures") or []) if isinstance(enhanced_asset.get("figures"), list) else 0
        asset_id = enhanced_asset.get("asset_id") or "unknown"
        print(
            f"[{args.batch}] {done}/{progress_state['total']} "
            f"figures={stats['figures']} last={asset_id} last_figures={figure_count}",
            flush=True,
        )
        if output_path == input_path and args.save_every > 0 and done - progress_state["last_saved"] >= args.save_every:
            partial = enhanced_partial + assets[done:]
            atomic_write_assets(input_path, partial)
            progress_state["last_saved"] = done

    enhanced_partial: List[Dict[str, Any]] = []

    def capture_progress(done: int, stats: Dict[str, int], enhanced_asset: Dict[str, Any]) -> None:
        enhanced_partial.append(enhanced_asset)
        progress(done, stats, enhanced_asset)

    enhanced, stats = enhance_assets(
        assets,
        store=store,
        batch=args.batch,
        with_figures=args.with_figures,
        figure_timeout=args.figure_timeout,
        max_pages=args.max_pages,
        overwrite_reader=args.overwrite_reader,
        overwrite_figures=args.overwrite_figures,
        limit=args.limit,
        progress=capture_progress if output_path == input_path else None,
    )

    if output_path == input_path:
        atomic_write_assets(input_path, enhanced)
    else:
        write_assets(output_path, enhanced)

    manifest = {
        "batch": args.batch,
        "input": str(input_path),
        "output": str(output_path),
        "snapshot": snapshot,
        "with_figures": args.with_figures,
        "total": len(assets),
        **stats,
        "updated_at": utc_now(),
    }
    (batch_dir / "reader_card_run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
