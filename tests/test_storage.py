import os
import re
from pathlib import Path

import pytest

from idea_scout.storage import resolve_asset_store_path, resolve_asset_store_root


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_LOCAL_STORE = "/vePFS-Mindverse/user/intern/zhouch/asset_store"
PROJECT_LOCAL_DATA_PATTERN = re.compile(r"(?<![/A-Za-z0-9_])data/")


def test_resolver_uses_verified_1018_shared_dataset_root(tmp_path: Path) -> None:
    share = tmp_path / "vepfs-share"
    dataset = share / "dataset"
    dataset.mkdir(parents=True)

    resolved = resolve_asset_store_root(
        env={},
        shared_dataset_roots=(dataset,),
        is_mount=lambda path: path == share,
    )

    assert resolved == dataset / "research-idea-scout-assets"


def test_resolver_uses_verified_6688_override(tmp_path: Path) -> None:
    share = tmp_path / "mnt-data-share"
    dataset = share / "dataset"
    target = dataset / "custom-assets"
    dataset.mkdir(parents=True)

    resolved = resolve_asset_store_root(
        env={"IDEASCOUT_ASSET_STORE": str(target)},
        shared_dataset_roots=(dataset,),
        is_mount=lambda path: path == share,
    )

    assert resolved == target


def test_resolver_rejects_personal_workspace_override(tmp_path: Path) -> None:
    local_store = tmp_path / "user" / "asset_store"

    with pytest.raises(RuntimeError, match="verified shared dataset root"):
        resolve_asset_store_root(
            env={"IDEASCOUT_ASSET_STORE": str(local_store)},
            shared_dataset_roots=(),
            is_mount=lambda _path: False,
        )


def test_local_test_override_requires_pytest_marker(tmp_path: Path) -> None:
    local_store = tmp_path / "fixture-store"

    with pytest.raises(RuntimeError, match="verified shared dataset root"):
        resolve_asset_store_root(
            env={
                "IDEASCOUT_ASSET_STORE": str(local_store),
                "IDEASCOUT_TEST_ALLOW_LOCAL_STORE": "1",
            },
            shared_dataset_roots=(),
            is_mount=lambda _path: False,
        )


def test_resolver_has_no_mlp_local_fallback() -> None:
    with pytest.raises(RuntimeError, match="MLP/local storage is forbidden"):
        resolve_asset_store_root(
            env={},
            shared_dataset_roots=(),
            is_mount=lambda _path: False,
        )


def test_store_subpath_defaults_inside_verified_store() -> None:
    store = Path(os.environ["IDEASCOUT_ASSET_STORE"]).resolve()

    assert resolve_asset_store_path(None, "work/pdf_ingest") == store / "work/pdf_ingest"


def test_store_subpath_rejects_path_outside_verified_store(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="inside the verified asset store"):
        resolve_asset_store_path(str(tmp_path / "outside"), "work/pdf_ingest")


def test_runtime_and_operator_docs_do_not_hardcode_local_storage() -> None:
    checked_paths = (
        REPO_ROOT / "idea_scout",
        REPO_ROOT / "scripts",
        REPO_ROOT / "web",
        REPO_ROOT / "configs",
        REPO_ROOT / "README.md",
        REPO_ROOT / "DATA_LOCATION.md",
    )
    offenders = []
    for checked_path in checked_paths:
        files = checked_path.rglob("*") if checked_path.is_dir() else (checked_path,)
        for path in files:
            if "__pycache__" in path.parts:
                continue
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            forbidden = []
            if LEGACY_LOCAL_STORE in content:
                forbidden.append(LEGACY_LOCAL_STORE)
            if PROJECT_LOCAL_DATA_PATTERN.search(content):
                forbidden.append("project-local data/")
            if forbidden:
                offenders.append((str(path.relative_to(REPO_ROOT)), forbidden))

    assert offenders == []
