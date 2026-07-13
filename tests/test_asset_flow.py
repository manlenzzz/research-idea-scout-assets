from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from idea_scout.export_assets import flatten_asset
from idea_scout.extract_assets import extract_assets
from idea_scout.import_bestpaper_manifest import build_records
from idea_scout.ingest_pdf import ingest_one
from idea_scout.io_utils import write_csv
from idea_scout.verify_code import verify_one
from web.import_jsonl import import_asset_rows


def sample_paper() -> dict:
    return {
        "title": "Subspace Editing for Controllable Representation Learning",
        "abstract": "We propose a method to discover linear subspaces and intervene on representations.",
        "venue": "ExampleConf",
        "year": 2026,
        "url": "https://example.org/paper1",
    }


def test_extract_asset_has_code_and_pdf_fields() -> None:
    assets = extract_assets([sample_paper()], profile_name="test_profile")
    assert len(assets) == 1
    asset = assets[0]
    assert asset["asset_id"]
    assert asset["challenge"]
    assert asset["solution_pattern"]
    assert asset["code"]["status"] == "missing"
    assert asset["pdf"]["status"] == "missing"
    assert "asset_score" in asset["scores"]


def test_code_verification_marks_missing_code() -> None:
    asset = extract_assets([sample_paper()])[0]
    checked = verify_one(asset, offline=True)
    assert checked["code"]["status"] == "missing"
    assert checked["code"]["runnable_status"] == "not_attempted"
    assert checked["scores"]["code_readiness"] == 0.0


def test_pdf_ingest_fallback_adds_evidence(tmp_path: Path) -> None:
    asset = extract_assets([sample_paper()])[0]
    checked = ingest_one(asset, output_dir=tmp_path)
    assert checked["pdf"]["status"] == "missing"
    assert checked["pdf"]["extracted_sections"]["method"]
    assert checked["scores"]["evidence_strength"] >= 3.0



def test_pdf_ingest_reuses_existing_text_path(tmp_path: Path) -> None:
    text_path = tmp_path / "paper.txt"
    text_path.write_text(
        "Abstract\nWe solve sparse tracking.\n\nMethod\nUse a reusable transformer module.\n\nExperiments\nIt works.",
        encoding="utf-8",
    )
    paper = sample_paper() | {"text_path": str(text_path)}
    asset = extract_assets([paper])[0]
    checked = ingest_one(asset, output_dir=tmp_path / "ingest")
    assert checked["pdf"]["status"] == "parsed"
    assert checked["pdf"]["text_path"] == str(text_path)
    assert checked["pdf"]["extracted_sections"]["method"].startswith("Use a reusable")


def test_bestpaper_manifest_builds_records_from_existing_text(tmp_path: Path) -> None:
    root = tmp_path / "bestpaper"
    text_dir = root / "cvpr" / "text" / "2025"
    pdf_dir = root / "cvpr" / "2025"
    text_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "sample-paper.pdf").write_bytes(b"%PDF-1.4")
    (text_dir / "sample-paper.txt").write_text(
        "Sample Paper\n\nAbstract\nWe present a method for robust ideas. Code is at https://github.com/acme/sample.",
        encoding="utf-8",
    )
    manifest = root / "manifest.csv"
    manifest.write_text(
        "venue,year,title,status,reason,pdf_url,relpath\n"
        "cvpr,2025,Sample Paper,downloaded,,https://example.org/sample.pdf,cvpr/2025/sample-paper.pdf\n",
        encoding="utf-8",
    )

    records = build_records(manifest, root, min_year=2016, max_year=2025)
    assert len(records) == 1
    record = records[0]
    assert record["title"] == "Sample Paper"
    assert record["venue"] == "CVPR"
    assert record["pdf_url"].startswith("file://")
    assert record["text_path"] == str(text_dir / "sample-paper.txt")
    assert record["code_url"] == "https://github.com/acme/sample"
    assert "robust ideas" in record["abstract"]

def test_export_asset_csv_row(tmp_path: Path) -> None:
    asset = ingest_one(verify_one(extract_assets([sample_paper()])[0], offline=True), output_dir=tmp_path)
    row = flatten_asset(asset, 1)
    out = tmp_path / "assets.csv"
    write_csv(out, [row], list(row.keys()))
    text = out.read_text()
    assert "asset_id" in text
    assert "code_status" in text
    assert "pdf_status" in text


def test_portal_import_and_asset_routes(tmp_path: Path, monkeypatch) -> None:
    asset = ingest_one(verify_one(extract_assets([sample_paper()])[0], offline=True), output_dir=tmp_path)
    jsonl = tmp_path / "assets.jsonl"
    jsonl.write_text(json.dumps(asset, ensure_ascii=False) + "\n")
    db = tmp_path / "portal.db"
    assert import_asset_rows(jsonl, db) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from assets").fetchone()[0] == 1

    monkeypatch.setenv("IDEASCOUT_PORTAL_DB", str(db))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)
    assert client.get("/assets").status_code == 200
    assert client.get(f"/assets/{asset['asset_id']}").status_code == 200


def test_portal_defaults_to_asset_store_database(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "shared" / "dataset" / "portal-store"
    store.mkdir(parents=True)
    monkeypatch.setenv("IDEASCOUT_ASSET_STORE", str(store))
    monkeypatch.delenv("IDEASCOUT_PORTAL_DB", raising=False)

    import importlib
    import web.app.main as main

    importlib.reload(main)
    assert main.DB_PATH == store.resolve() / "portal.db"


def test_build_portal_from_store_imports_canonical_batches(tmp_path: Path) -> None:
    store = tmp_path / "store"
    for batch, title, score in [
        ("bestpaper", "Best Paper Asset", 8.0),
        ("high_impact_ml", "High Impact ML Asset", 9.0),
    ]:
        asset = reviewed_asset()
        asset["asset_id"] = f"{batch}-asset"
        asset["profile_name"] = batch
        asset["source_papers"][0]["title"] = title
        asset["scores"]["asset_score"] = score
        batch_dir = store / batch
        batch_dir.mkdir(parents=True)
        (batch_dir / "assets.jsonl").write_text(json.dumps(asset, ensure_ascii=False) + "\n", encoding="utf-8")

    script = Path(__file__).resolve().parents[1] / "scripts" / "build_portal_from_store.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--store",
            str(store),
            "--batches",
            "bestpaper,high_impact_ml",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(store / "portal.db") as conn:
        assert conn.execute("select count(*) from assets").fetchone()[0] == 2
        assert conn.execute("select count(*) from articles").fetchone()[0] == 2
        profiles = {
            row[0]
            for row in conn.execute("select profile_name from assets order by profile_name").fetchall()
        }
    assert profiles == {"bestpaper", "high_impact_ml"}


def test_homepage_prioritizes_daily_asset_workflow(tmp_path: Path, monkeypatch) -> None:
    asset = ingest_one(verify_one(extract_assets([sample_paper()])[0], offline=True), output_dir=tmp_path)
    jsonl = tmp_path / "assets.jsonl"
    jsonl.write_text(json.dumps(asset, ensure_ascii=False) + "\n")
    db = tmp_path / "portal.db"
    assert import_asset_rows(jsonl, db) == 1

    monkeypatch.setenv("IDEASCOUT_PORTAL_DB", str(db))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    html = client.get("/").text
    assert 'href="assets/today">Today</a>' in html
    assert 'href="assets">Asset Library</a>' in html
    assert 'href="/articles">Article Library</a>' not in html
    assert 'href="assets/today">Start Daily Queue</a>' in html
    assert 'href="articles">Source Papers</a>' in html
    assert "Open Article Library" not in html


def test_homepage_reports_asset_library_scope(tmp_path: Path, monkeypatch) -> None:
    asset = reviewed_asset()
    jsonl = tmp_path / "assets.jsonl"
    jsonl.write_text(json.dumps(asset, ensure_ascii=False) + "\n", encoding="utf-8")
    db = tmp_path / "portal.db"
    assert import_asset_rows(jsonl, db) == 1

    monkeypatch.setenv("IDEASCOUT_PORTAL_DB", str(db))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    html = client.get("/").text
    assert "Assets" in html
    assert "Accepted" in html
    assert "Weak" in html
    assert "Code Ready" in html
    assert "1" in html


def reviewed_asset() -> dict:
    asset = extract_assets([sample_paper()], profile_name="test_profile")[0]
    asset.update(
        {
            "challenge": "标注昂贵时，很难把新密集预测任务快速适配出来。",
            "solution_pattern": "用视觉 token 匹配，把图像和标签 patch token 放在同一匹配空间中做少样本适配。",
            "mechanism": "非参数 token matching 复用少量支持样本中的局部对应关系。",
            "why_it_is_hard": "密集预测任务输出空间不同，普通 few-shot 分类接口不能直接复用。",
            "transferable_to": ["医学分割", "机器人感知"],
            "non_transferable_parts": ["依赖 dense label 的任务接口", "大 ViT backbone 的显存成本"],
            "code": {"status": "repo_found", "url": "https://github.com/acme/vtm"},
            "pdf": {"status": "parsed", "url": "file:///tmp/paper.pdf"},
            "scores": {
                "asset_score": 8.5,
                "evidence_strength": 8.0,
                "code_readiness": 9.0,
                "transferability": 9.0,
                "implementation_feasibility": 8.0,
            },
            "insight": {
                "reusable_insight": "当任务标签空间不同但局部视觉对应可共享时，可以把输入和标签都 token 化后做少样本匹配。"
            },
            "llm_review": {
                "verdict": "accept",
                "asset_quality": 5,
                "challenge": "标注昂贵时，很难把新密集预测任务快速适配出来。",
                "method": "用视觉 token 匹配，把图像和标签 patch token 放在同一匹配空间中做少样本适配。",
                "reusable_insight": "当任务标签空间不同但局部视觉对应可共享时，可以把输入和标签都 token 化后做少样本匹配。",
                "why_it_works": "token matching 避免为每个新任务重新训练完整参数模型。",
                "transfer_targets": ["医学分割", "机器人感知"],
                "non_transferable_parts": ["依赖 dense label 的任务接口", "大 ViT backbone 的显存成本"],
                "evidence_quotes": ["learn any dense task", "non-parametric matching"],
                "code_assessment": "official",
                "review_notes": "方法机制明确，代码可用。",
                "confidence": 0.91,
            },
            "reader_card": {
                "short_title": "Token matching for dense tasks",
                "intuition": "密集预测任务可以先看局部 patch 是否能在支持样本中找到对应关系，而不是先训练一个新头。",
                "why_old_way_fails": "普通 few-shot 分类接口只输出类别，不能自然表达 dense label 的局部结构。",
                "mechanism_steps": [
                    "把输入图像和标签都拆成 patch tokens。",
                    "在共享 token 空间中做非参数匹配。",
                    "用支持样本中的局部对应关系生成目标任务输出。",
                ],
                "key_terms": [
                    {"term": "token matching", "plain": "把局部块当成可检索单元来找对应关系。"}
                ],
                "transfer_rule": "当任务之间共享局部视觉对应关系，但标签空间不同，可以尝试这种接口。",
                "misuse_warning": "如果目标没有局部对应结构，token matching 可能只是在做噪声匹配。",
                "technical_summary": "用视觉 token 匹配，把图像和标签 patch token 放在同一匹配空间中做少样本适配。",
            },
            "figures": [
                {
                    "kind": "important",
                    "path": str(Path("figures") / "vtm-asset" / "figure-1.png"),
                    "caption": "Figure 1: Overview of dense task token matching.",
                    "why_selected": "概览图直接展示输入 token、标签 token 和匹配流程。",
                }
            ],
            "source_papers": [
                {
                    "title": "Universal Few-shot Learning of Dense Prediction Tasks",
                    "venue": "ICLR",
                    "year": 2023,
                    "url": "https://example.org/paper",
                }
            ],
        }
    )
    return asset


def test_portal_asset_cards_use_reviewed_method_asset_format(tmp_path: Path, monkeypatch) -> None:
    asset = reviewed_asset()
    jsonl = tmp_path / "assets.jsonl"
    jsonl.write_text(json.dumps(asset, ensure_ascii=False) + "\n", encoding="utf-8")
    db = tmp_path / "portal.db"
    assert import_asset_rows(jsonl, db) == 1

    monkeypatch.setenv("IDEASCOUT_PORTAL_DB", str(db))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)
    html = client.get("/assets").text
    assert "static/style.css?v=" in html
    assert "Method Asset Library" in html
    assert "Challenge" in html
    assert "Method" in html
    assert "Reusable insight" in html
    assert "Why it works" in html
    assert "Transfer targets" in html
    assert "Boundary" in html
    assert "Code evidence" in html
    assert "official" in html
    assert "article-list" in html
    assert "article-row" in html
    assert 'class="article-row asset-card method-card copyable-card asset-overview-card"' in html
    assert 'class="article-row asset-card method-card" href=' not in html
    assert "selectable-text" in html
    assert "asset-overview-list" in html
    assert "asset-overview-card" in html
    assert "asset-rank-rail" in html
    assert "asset-card-shell" in html
    assert "asset-glance" in html
    assert "asset-reading-flow" in html
    assert "asset-score-strip" in html
    assert "method-context" in html
    assert "context-line challenge-line" in html
    assert "context-line logic-line" in html
    assert "evidence-strip" in html
    assert "asset-brief-grid" not in html
    assert "brief-block" not in html
    assert "asset-score-rail" not in html
    assert "asset-mini-evidence" not in html
    assert "asset-section method-section" in html
    assert "asset-card-top" in html
    assert "asset-score-panel" in html
    assert "score-metric primary-score" in html
    assert "asset-lead insight-section" in html
    assert "asset-lead-text" in html
    assert "asset-body-grid" not in html
    assert "asset-section method-section method-emphasis" in html
    assert "asset-evidence-grid" in html
    assert "Open details" in html
    assert f'href="assets/{asset["asset_id"]}"' in html
    assert "Asset 8.50" in html
    assert "Evidence 8.00" in html
    assert "Code 9.00" in html
    assert "LLM quality 5/5" in html
    assert "Problem:" not in html

    css = (Path(__file__).resolve().parents[1] / "web/app/static/style.css").read_text()
    assert ".asset-lead p" in css and "font-weight: 720" in css
    assert ".method-section p" in css and "font-size: 15px" in css
    assert ".method-context" in css and "border-top: 1px solid" in css
    assert ".context-line p" in css and "font-size: 14px" in css
    assert ".score-metric strong" in css and "font-variant-numeric: tabular-nums" in css
    assert ".asset-overview-list" in css and "box-shadow: none" in css
    assert ".asset-overview-card" in css and "grid-template-columns: 64px minmax(0, 1fr)" in css
    assert ".asset-card-shell" in css and "box-shadow: 0 20px 48px" in css
    assert ".asset-glance p" in css and "font-size: 18px" in css
    assert ".asset-score-strip" in css and "display: flex" in css
    assert ".evidence-strip" in css and "grid-template-columns: 1fr 1fr 1fr" in css

    detail = client.get(f"/assets/{asset['asset_id']}").text
    assert "Challenge -> Method -> Reusable Insight" in detail
    assert "asset-detail-shell" in detail
    assert "asset-reader-main" in detail
    assert "asset-side-panel" in detail
    assert "asset-neighbor-list" in detail
    assert "asset-switcher" not in detail
    assert "asset-switcher-item active" not in detail
    assert "Reader Card" in detail
    assert "reader-lead-grid" in detail
    assert "Token matching for dense tasks" in detail
    assert "密集预测任务可以先看局部 patch" in detail
    assert "Mechanism walkthrough" in detail
    assert "token matching" in detail
    assert "Paper figure" in detail
    assert "../asset-files/figures/vtm-asset/figure-1.png" in detail
    assert "data-figure-viewer-trigger" in detail
    assert "figure-lightbox" in detail
    assert "data-figure-zoom-in" in detail
    assert "Open original" in detail
    assert "概览图直接展示输入 token" in detail
    assert "asset-evidence-drawer" in detail
    assert "<summary>Evidence quotes</summary>" in detail
    assert "<summary>Raw pipeline evidence</summary>" in detail
    assert "Evidence quotes" in detail
    assert "Raw pipeline evidence" in detail

    assert ".asset-detail-shell" in css and "grid-template-columns: minmax(0, 1fr) 300px" in css
    assert ".asset-side-panel" in css and "position: sticky" in css
    assert ".asset-reader-main" in css and "max-width: 796px" in css
    assert ".reader-lead-grid-single" in css and "grid-template-columns: 1fr" in css
    assert ".asset-evidence-drawer details" in css


def test_portal_serves_asset_files_from_store(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "shared" / "dataset" / "route-store"
    figure = store / "figures" / "vtm-asset" / "figure-1.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("IDEASCOUT_ASSET_STORE", str(store))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)
    resp = client.get("/asset-files/figures/vtm-asset/figure-1.png")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\n\x1a\n"

    blocked = client.get("/asset-files/../secret.txt")
    assert blocked.status_code in {404, 405}


def test_portal_links_are_safe_under_webide_path_prefix(tmp_path: Path, monkeypatch) -> None:
    asset = reviewed_asset()
    jsonl = tmp_path / "assets.jsonl"
    jsonl.write_text(json.dumps(asset, ensure_ascii=False) + "\n", encoding="utf-8")
    db = tmp_path / "portal.db"
    assert import_asset_rows(jsonl, db) == 1

    monkeypatch.setenv("IDEASCOUT_PORTAL_DB", str(db))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    today = client.get("/assets/today").text
    assert 'href="../assets">Full Library</a>' in today
    assert f'href="../assets/{asset["asset_id"]}">Read full asset</a>' in today
    assert f'action="../assets/{asset["asset_id"]}/state"' in today
    assert 'href="/assets/today"' not in today

    detail = client.get(f"/assets/{asset['asset_id']}").text
    assert 'href="../assets">Back to Assets</a>' in detail
    assert 'href="../assets">View all</a>' in detail
    assert 'href="../static/style.css?v=' in detail
    assert "../asset-files/figures/vtm-asset/figure-1.png" in detail


def test_asset_state_redirect_keeps_webide_path_prefix(tmp_path: Path, monkeypatch) -> None:
    asset = reviewed_asset()
    jsonl = tmp_path / "assets.jsonl"
    jsonl.write_text(json.dumps(asset, ensure_ascii=False) + "\n", encoding="utf-8")
    db = tmp_path / "portal.db"
    assert import_asset_rows(jsonl, db) == 1

    monkeypatch.setenv("IDEASCOUT_PORTAL_DB", str(db))
    import importlib
    import web.app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    response = client.post(
        f"/assets/{asset['asset_id']}/state",
        data={"status": "done", "tag": "read", "next": "/assets/today"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "../today"
    assert not response.headers["location"].startswith("/")

    with sqlite3.connect(db) as conn:
        state = conn.execute(
            "select status, completed_on from asset_user_state where asset_id = ?",
            (asset["asset_id"],),
        ).fetchone()
        tags = conn.execute("select tag from asset_tags where asset_id = ?", (asset["asset_id"],)).fetchall()
    assert state[0] == "done"
    assert state[1]
    assert [row[0] for row in tags] == ["read"]
