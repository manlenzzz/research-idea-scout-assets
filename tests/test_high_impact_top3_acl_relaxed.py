from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_harvester_module():
    path = Path(__file__).resolve().parents[1] / "tmp" / "high_impact_top3_acl_relaxed_20260626.py"
    spec = importlib.util.spec_from_file_location("high_impact_top3_acl_relaxed_20260626", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recover_acl_records_from_cache_reuses_prior_openalex_matches(tmp_path: Path) -> None:
    module = load_harvester_module()
    store = tmp_path / "store"
    source_batch = store / "high_impact_acl_expand"
    active_batch = store / "high_impact_top3_acl_relaxed_20260626"
    source_batch.mkdir(parents=True)
    active_batch.mkdir(parents=True)

    title = "A Highly Cited ACL Paper"
    seed = {
        "raw": {
            "paper_id": "acl::x1",
            "title": title,
            "venue": "ACL",
            "year": 2018,
            "authors": "Ada Lovelace",
            "url": "https://aclanthology.org/P18-1000/",
            "pdf_url": "https://aclanthology.org/P18-1000.pdf",
        }
    }
    (source_batch / "candidates.jsonl").write_text(json.dumps(seed) + "\n", encoding="utf-8")

    key = module.slugify(title, max_chars=140)
    work = {
        "id": "https://openalex.org/W123",
        "title": title,
        "publication_year": 2018,
        "cited_by_count": 300,
        "abstract_inverted_index": {"Useful": [0], "method": [1]},
        "primary_location": {
            "landing_page_url": "https://aclanthology.org/P18-1000/",
            "pdf_url": "https://aclanthology.org/P18-1000.pdf",
        },
        "best_oa_location": None,
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
    }
    (active_batch / "acl_openalex_cache.jsonl").write_text(
        json.dumps({"key": key, "title": title, "matched": True, "work": work}) + "\n",
        encoding="utf-8",
    )

    records = module.recover_acl_records_from_cache(
        store=store,
        batch_dir=active_batch,
        skip_titles=set(),
        log_path=active_batch / "run_events.jsonl",
    )

    assert len(records) == 1
    assert records[0]["title"] == title
    assert records[0]["venue"] == "ACL"
    assert records[0]["citation_count"] == 300
    assert records[0]["source"] == "acl_anthology_openalex_matched"
