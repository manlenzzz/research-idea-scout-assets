from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def verified_test_asset_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import idea_scout.storage as storage

    share = tmp_path / "shared"
    dataset = share / "dataset"
    store = dataset / "research-idea-scout-assets"
    store.mkdir(parents=True)

    real_is_mount = os.path.ismount
    monkeypatch.setattr(storage, "KNOWN_SHARED_DATASET_ROOTS", (dataset,))
    monkeypatch.setattr(
        storage.os.path,
        "ismount",
        lambda path: Path(path).resolve() == share.resolve() or real_is_mount(path),
    )
    monkeypatch.setenv("IDEASCOUT_ASSET_STORE", str(store))
    monkeypatch.setenv("IDEASCOUT_TEST_ALLOW_LOCAL_STORE", "1")
