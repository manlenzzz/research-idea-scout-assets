from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


KNOWN_SHARED_DATASET_ROOTS = (
    Path("/vePFS-Mindverse/share/dataset"),
    Path("/mnt/data/share/dataset"),
)
DEFAULT_ASSET_STORE_NAME = "research-idea-scout-assets"


def resolve_asset_store_root(
    *,
    env: Optional[Mapping[str, str]] = None,
    shared_dataset_roots: Optional[Sequence[Path]] = None,
    is_mount: Optional[Callable[[Path], bool]] = None,
) -> Path:
    environment = os.environ if env is None else env
    roots = KNOWN_SHARED_DATASET_ROOTS if shared_dataset_roots is None else shared_dataset_roots
    mount_checker = os.path.ismount if is_mount is None else is_mount
    verified_roots = tuple(
        root.resolve()
        for root in roots
        if root.is_dir() and mount_checker(root.resolve().parent)
    )

    configured = environment.get("IDEASCOUT_ASSET_STORE", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if any(_is_within(candidate, root) for root in verified_roots):
            return candidate
        if (
            environment.get("IDEASCOUT_TEST_ALLOW_LOCAL_STORE") == "1"
            and environment.get("PYTEST_CURRENT_TEST")
        ):
            return candidate
        raise RuntimeError(
            "IDEASCOUT_ASSET_STORE must be inside a verified shared dataset root; "
            "personal workspace and MLP/local storage are forbidden."
        )

    if verified_roots:
        return verified_roots[0] / DEFAULT_ASSET_STORE_NAME

    raise RuntimeError(
        "No verified shared dataset root is mounted. MLP/local storage is forbidden; "
        "run on endpoint 1018 or 6688 and set IDEASCOUT_ASSET_STORE explicitly."
    )


def resolve_asset_store_argument(configured: Optional[str]) -> Path:
    if not configured:
        return resolve_asset_store_root()
    environment = dict(os.environ)
    environment["IDEASCOUT_ASSET_STORE"] = configured
    return resolve_asset_store_root(env=environment)


def resolve_asset_store_path(configured: Optional[str], default_relative: str) -> Path:
    root = resolve_asset_store_root()
    candidate = (
        Path(configured).expanduser().resolve()
        if configured
        else (root / default_relative).resolve()
    )
    if not _is_within(candidate, root):
        raise RuntimeError(f"path must be inside the verified asset store: {root}")
    return candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root
