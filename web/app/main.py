from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import parse_qs
from posixpath import basename, dirname, normpath, relpath

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from idea_scout.storage import resolve_asset_store_root

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR.parent
ASSET_STORE_ROOT = resolve_asset_store_root()
DEFAULT_DB = ASSET_STORE_ROOT / "portal.db"
DB_PATH = Path(os.environ.get("IDEASCOUT_PORTAL_DB", str(DEFAULT_DB))).resolve()


@asynccontextmanager
async def lifespan(_: FastAPI):
    warm_asset_base_cache()
    warm_home_overview_cache()
    yield


app = FastAPI(title="IdeaScout Portal", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

ASSET_BASE_COLUMNS = (
    "id",
    "asset_id",
    "asset_type",
    "profile_name",
    "challenge",
    "solution_pattern",
    "mechanism",
    "why_it_is_hard",
    "code_status",
    "code_url",
    "pdf_status",
    "pdf_url",
    "asset_score",
    "evidence_strength",
    "code_readiness",
    "source_title",
    "source_venue",
    "source_year",
    "raw_json",
)
_ASSET_BASE_CACHE: Tuple[Dict[str, Any], ...] | None = None
_ASSET_BASE_BY_ID: Dict[str, Dict[str, Any]] | None = None
_ASSET_BASE_LOCK = Lock()
_HOME_OVERVIEW_CACHE: Dict[str, Any] | None = None
_HOME_OVERVIEW_LOCK = Lock()


def rel_url(path: str, depth: int = 0) -> str:
    normalized = str(path or "").lstrip("/")
    prefix = "../" * max(depth, 0)
    if normalized:
        return f"{prefix}{normalized}"
    return prefix or "."


templates.env.globals["rel_url"] = rel_url


def app_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "/"
    if raw.startswith(("http://", "https://")):
        return raw
    normalized = normpath("/" + raw.lstrip("/"))
    return normalized if normalized.startswith("/") else f"/{normalized}"


def relative_redirect_url(path: str, current_path: str, fallback: str = "/") -> str:
    raw = str(path or "").strip()
    if not raw or raw.startswith(("http://", "https://", "//")):
        raw = fallback
    target = app_path(raw)
    if target.startswith(("http://", "https://", "//")):
        target = app_path(fallback)
    current = app_path(current_path)
    current_dir = dirname(current) or "/"
    clean_target = target.rstrip("/") or "/"
    clean_current_dir = current_dir.rstrip("/") or "/"
    if clean_target == clean_current_dir and clean_target != "/":
        return f"../{basename(clean_target)}"
    return relpath(clean_target, start=clean_current_dir)


def static_asset(path: str, depth: int = 0) -> str:
    normalized = path.lstrip("/")
    file_path = APP_DIR / "static" / normalized
    version = int(file_path.stat().st_mtime) if file_path.exists() else 0
    return f"{rel_url(f'static/{normalized}', depth)}?v={version}"


templates.env.globals["static_asset"] = static_asset


def asset_file_url(path: str, depth: int = 0) -> str:
    normalized = str(path or "").replace("\\", "/").lstrip("/")
    return rel_url(f"asset-files/{normalized}", depth) if normalized else ""


templates.env.globals["asset_file_url"] = asset_file_url


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                abstract TEXT,
                venue TEXT,
                year INTEGER,
                url TEXT,
                authors TEXT,
                priority TEXT,
                profile_name TEXT,
                idea_core TEXT,
                transferable_mechanism TEXT,
                fit_reason TEXT,
                risk_or_limitation TEXT,
                rank_score REAL,
                score_overall_fit REAL,
                score_theory_novelty REAL,
                scores_json TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_rank ON articles(rank_score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_year ON articles(year DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_priority ON articles(priority)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT UNIQUE,
                asset_type TEXT,
                profile_name TEXT,
                challenge TEXT,
                solution_pattern TEXT,
                mechanism TEXT,
                why_it_is_hard TEXT,
                code_status TEXT,
                code_url TEXT,
                pdf_status TEXT,
                pdf_url TEXT,
                asset_score REAL,
                evidence_strength REAL,
                code_readiness REAL,
                source_title TEXT,
                source_venue TEXT,
                source_year INTEGER,
                raw_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_score ON assets(asset_score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_code_status ON assets(code_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_pdf_status ON assets(pdf_status)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_user_state (
                asset_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unseen',
                starred INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                completed_on TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_user_state_status ON asset_user_state(status)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_tags (
                asset_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (asset_id, tag)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag)")


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    obj = dict(row)
    for key in ["scores_json", "raw_json"]:
        if obj.get(key):
            try:
                obj[key.replace("_json", "")] = json.loads(obj[key])
            except Exception:
                obj[key.replace("_json", "")] = {}
        else:
            obj[key.replace("_json", "")] = {}
    return obj


def safe_asset_file_path(rel_path: str) -> Path | None:
    if not rel_path or rel_path.startswith(("/", "\\")):
        return None
    candidate = (ASSET_STORE_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(ASSET_STORE_ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def fetch_all_articles() -> List[Dict[str, Any]]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM articles").fetchall()
    return [row_to_dict(r) for r in rows]


def clear_asset_base_cache() -> None:
    global _ASSET_BASE_CACHE, _ASSET_BASE_BY_ID, _HOME_OVERVIEW_CACHE
    with _ASSET_BASE_LOCK:
        _ASSET_BASE_CACHE = None
        _ASSET_BASE_BY_ID = None
    with _HOME_OVERVIEW_LOCK:
        _HOME_OVERVIEW_CACHE = None


def warm_asset_base_cache() -> Tuple[Dict[str, Any], ...]:
    global _ASSET_BASE_CACHE, _ASSET_BASE_BY_ID
    if _ASSET_BASE_CACHE is not None:
        return _ASSET_BASE_CACHE
    with _ASSET_BASE_LOCK:
        if _ASSET_BASE_CACHE is None:
            ensure_db()
            columns = ", ".join(ASSET_BASE_COLUMNS)
            with connect() as conn:
                rows = conn.execute(f"SELECT {columns} FROM assets").fetchall()
            assets = []
            for row in rows:
                asset = row_to_dict(row)
                asset["cluster"] = infer_asset_cluster(asset)
                assets.append(asset)
            _ASSET_BASE_CACHE = tuple(assets)
            _ASSET_BASE_BY_ID = {
                str(asset.get("asset_id") or ""): asset
                for asset in _ASSET_BASE_CACHE
            }
    return _ASSET_BASE_CACHE


def fetch_asset_user_fields() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]]]:
    with connect() as conn:
        states = {
            row["asset_id"]: dict(row)
            for row in conn.execute(
                "SELECT asset_id, status, starred, updated_at, completed_on FROM asset_user_state"
            )
        }
        tag_rows = conn.execute(
            "SELECT asset_id, tag FROM asset_tags ORDER BY created_at, tag"
        ).fetchall()
    tags: Dict[str, List[str]] = {}
    for row in tag_rows:
        tags.setdefault(row["asset_id"], []).append(row["tag"])
    return states, tags



def fetch_all_assets() -> List[Dict[str, Any]]:
    base_assets = warm_asset_base_cache()
    states, tags = fetch_asset_user_fields()
    assets = []
    for base_asset in base_assets:
        asset = dict(base_asset)
        attach_asset_user_fields(asset, states=states, tags=tags)
        assets.append(asset)
    return assets


def fetch_asset(asset_id: str) -> Dict[str, Any] | None:
    warm_asset_base_cache()
    assert _ASSET_BASE_BY_ID is not None
    base_asset = _ASSET_BASE_BY_ID.get(asset_id)
    if base_asset is None:
        return None
    return attach_asset_user_fields(dict(base_asset))


def fetch_asset_navigation(limit: int = 40) -> List[Dict[str, Any]]:
    assets = sorted(
        warm_asset_base_cache(),
        key=lambda asset: score_value(asset, "asset_score"),
        reverse=True,
    )[:limit]
    return [dict(asset) for asset in assets]


def attach_asset_user_fields(
    asset: Dict[str, Any],
    states: Dict[str, Dict[str, Any]] | None = None,
    tags: Dict[str, List[str]] | None = None,
) -> Dict[str, Any]:
    asset_id = asset.get("asset_id")
    if states is None or tags is None:
        ensure_db()
        with connect() as conn:
            state_row = conn.execute(
                "SELECT asset_id, status, starred, updated_at, completed_on FROM asset_user_state WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            tag_rows = conn.execute("SELECT tag FROM asset_tags WHERE asset_id = ? ORDER BY created_at, tag", (asset_id,)).fetchall()
        states = {asset_id: dict(state_row)} if state_row else {}
        tags = {asset_id: [row["tag"] for row in tag_rows]}
    asset["user_state"] = states.get(asset_id, {"status": "unseen", "starred": 0, "completed_on": ""})
    asset["tags"] = tags.get(asset_id, [])
    asset.setdefault("cluster", infer_asset_cluster(asset))
    return asset


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def asset_text_for_cluster(asset: Dict[str, Any]) -> str:
    raw = asset.get("raw") or {}
    review = raw.get("llm_review") if isinstance(raw.get("llm_review"), dict) else {}
    reader = raw.get("reader_card") if isinstance(raw.get("reader_card"), dict) else {}
    terms = reader.get("key_terms") if isinstance(reader.get("key_terms"), list) else []
    term_text = " ".join(str(t.get("term") or "") for t in terms if isinstance(t, dict))
    parts = [
        asset.get("challenge"),
        asset.get("solution_pattern"),
        asset.get("mechanism"),
        asset.get("source_title"),
        review.get("method"),
        review.get("reusable_insight"),
        review.get("why_it_works"),
        review.get("transfer_targets"),
        term_text,
    ]
    return " ".join(str(x) for x in parts if x).lower()


def infer_asset_cluster(asset: Dict[str, Any]) -> str:
    text = asset_text_for_cluster(asset)
    rules = [
        ("Flow / posterior modeling", ["flow", "posterior", "variational", "jacobian", "density"]),
        ("Token matching / dense prediction", ["token matching", "dense prediction", "patch", "segmentation"]),
        ("Retrieval / memory augmentation", ["retrieval", "retrieve", "memory", "nearest", "index"]),
        ("Diffusion / generation control", ["diffusion", "denoising", "generation", "generative"]),
        ("Graph / structure reasoning", ["graph", "node", "edge", "structure"]),
        ("Attention / representation learning", ["attention", "transformer", "representation", "embedding"]),
        ("Optimization / training dynamics", ["optimization", "gradient", "training dynamics", "curriculum"]),
    ]
    for label, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return label
    return "General reusable method"


def collect_asset_clusters(assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for asset in assets:
        label = asset.get("cluster") or infer_asset_cluster(asset)
        counts[label] = counts.get(label, 0) + 1
    return [{"label": label, "count": counts[label]} for label in sorted(counts)]


def collect_asset_tags(assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for asset in assets:
        for tag in asset.get("tags") or []:
            counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": tag, "count": counts[tag]} for tag in sorted(counts)]


def set_asset_state(asset_id: str, status: str, tag: str = "") -> None:
    ensure_db()
    normalized = (status or "reading").strip().lower()
    if normalized not in {"unseen", "reading", "done", "skip", "starred"}:
        normalized = "reading"
    now = utc_now()
    completed_on = today_key() if normalized == "done" else None
    starred = 1 if normalized == "starred" else 0
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_user_state (asset_id, status, starred, updated_at, completed_on)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                status = excluded.status,
                starred = CASE WHEN excluded.starred = 1 THEN 1 ELSE asset_user_state.starred END,
                updated_at = excluded.updated_at,
                completed_on = excluded.completed_on
            """,
            (asset_id, normalized, starred, now, completed_on),
        )
        clean_tag = " ".join((tag or "").strip().split())
        if clean_tag:
            conn.execute(
                """
                INSERT OR IGNORE INTO asset_tags (asset_id, tag, created_at)
                VALUES (?, ?, ?)
                """,
                (asset_id, clean_tag, now),
            )


def score_value(article: Dict[str, Any], key: str) -> float:
    value = article.get(key)
    if value is None:
        raw = article.get("raw") or {}
        value = raw.get(key)
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def score_items(article: Dict[str, Any]) -> List[Tuple[str, float]]:
    raw = article.get("raw") or {}
    scores = article.get("scores") or {}
    items: List[Tuple[str, float]] = []

    for label, key in [
        ("Rank score", "rank_score"),
        ("Overall fit", "score_overall_fit"),
        ("Theory novelty", "score_theory_novelty"),
    ]:
        items.append((label, score_value(article, key)))

    for key, value in sorted(scores.items()):
        label = key.replace("_", " ").title()
        try:
            items.append((label, float(value or 0.0)))
        except Exception:
            items.append((label, 0.0))

    flat_keys = sorted(k for k in raw if k.startswith("score_") and k not in {"score_overall_fit", "score_theory_novelty"})
    seen = {name.lower() for name, _ in items}
    for key in flat_keys:
        label = key.replace("score_", "").replace("_", " ").title()
        if label.lower() in seen:
            continue
        items.append((label, score_value(article, key)))
    return items


def collect_dimension_stats(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bucket: Dict[str, List[float]] = {}
    for a in articles:
        raw = a.get("raw") or {}
        scores = a.get("scores") or {}
        for key, value in scores.items():
            try:
                bucket.setdefault(key, []).append(float(value or 0.0))
            except Exception:
                pass
        for key, value in raw.items():
            if not key.startswith("score_") or key in {"score_overall_fit", "score_theory_novelty"}:
                continue
            name = key.replace("score_", "")
            if name in bucket:
                continue
            try:
                bucket.setdefault(name, []).append(float(value or 0.0))
            except Exception:
                pass

    stats = []
    for key, vals in bucket.items():
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        high = sum(1 for v in vals if v >= 7.0)
        stats.append({"key": key, "label": key.replace("_", " ").title(), "avg": avg, "high": high})
    stats.sort(key=lambda x: (x["avg"], x["high"]), reverse=True)
    return stats[:12]


def _load_home_overview(base_assets: Tuple[Dict[str, Any], ...]) -> Dict[str, Any]:
    with connect() as conn:
        article_summary = conn.execute(
            """
            SELECT
                COUNT(*) AS article_total,
                COALESCE(SUM(CASE WHEN LOWER(COALESCE(priority, '')) = 'keep' THEN 1 ELSE 0 END), 0) AS keep_total,
                COALESCE(AVG(COALESCE(rank_score, 0.0)), 0.0) AS avg_rank,
                COALESCE(MAX(COALESCE(rank_score, 0.0)), 0.0) AS top_score
            FROM articles
            """
        ).fetchone()
        top_papers = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, title, venue, year, rank_score
                FROM articles
                ORDER BY COALESCE(rank_score, 0.0) DESC
                LIMIT 8
                """
            ).fetchall()
        ]
        score_rows = conn.execute(
            """
            SELECT scores_json
            FROM articles
            WHERE scores_json IS NOT NULL AND scores_json != ''
            """
        ).fetchall()

    dimension_articles: List[Dict[str, Any]] = []
    for row in score_rows:
        try:
            scores = json.loads(row["scores_json"])
        except (TypeError, json.JSONDecodeError):
            scores = {}
        dimension_articles.append(
            {"scores": scores if isinstance(scores, dict) else {}, "raw": {}}
        )

    accepted = 0
    weak = 0
    code_ready = 0
    for asset in base_assets:
        raw = asset.get("raw") or {}
        review = raw.get("llm_review") if isinstance(raw, dict) else None
        verdict = str(review.get("verdict") or "").lower() if isinstance(review, dict) else ""
        if verdict == "accept":
            accepted += 1
        elif verdict == "weak":
            weak += 1
        if (asset.get("code_status") or "") in {
            "repo_found",
            "open_source_verified",
        }:
            code_ready += 1

    return {
        "n": int(article_summary["article_total"]),
        "keep": int(article_summary["keep_total"]),
        "avg_rank": float(article_summary["avg_rank"]),
        "top_score": float(article_summary["top_score"]),
        "top_papers": top_papers,
        "dimensions": collect_dimension_stats(dimension_articles),
        "asset_total": len(base_assets),
        "asset_accepted": accepted,
        "asset_weak": weak,
        "asset_code_ready": code_ready,
    }


def warm_home_overview_cache() -> Dict[str, Any]:
    global _HOME_OVERVIEW_CACHE
    if _HOME_OVERVIEW_CACHE is not None:
        return dict(_HOME_OVERVIEW_CACHE)
    base_assets = warm_asset_base_cache()
    with _HOME_OVERVIEW_LOCK:
        if _HOME_OVERVIEW_CACHE is None:
            _HOME_OVERVIEW_CACHE = _load_home_overview(base_assets)
    return dict(_HOME_OVERVIEW_CACHE)


def fetch_home_overview() -> Dict[str, Any]:
    return warm_home_overview_cache()


def filter_articles(
    articles: List[Dict[str, Any]],
    q: str = "",
    priority: str = "",
    sort: str = "rank_score",
) -> List[Dict[str, Any]]:
    q = (q or "").strip().lower()
    priority = (priority or "").strip().lower()

    def text_blob(a: Dict[str, Any]) -> str:
        raw = a.get("raw") or {}
        parts = [
            a.get("title"), a.get("abstract"), a.get("venue"), a.get("year"),
            a.get("idea_core"), a.get("transferable_mechanism"), a.get("fit_reason"),
            a.get("risk_or_limitation"), raw.get("theory_bucket"), raw.get("theory_family"),
            raw.get("profile_name"), raw.get("url"),
        ]
        return " ".join(str(x) for x in parts if x).lower()

    out = []
    for a in articles:
        if q and q not in text_blob(a):
            continue
        if priority and (a.get("priority") or "").lower() != priority:
            continue
        out.append(a)

    if sort == "year":
        out.sort(key=lambda a: int(a.get("year") or 0), reverse=True)
    elif sort == "overall":
        out.sort(key=lambda a: score_value(a, "score_overall_fit"), reverse=True)
    elif sort == "novelty":
        out.sort(key=lambda a: score_value(a, "score_theory_novelty"), reverse=True)
    else:
        out.sort(key=lambda a: score_value(a, "rank_score"), reverse=True)
    return out


def filter_assets(
    assets: List[Dict[str, Any]],
    q: str = "",
    code_status: str = "",
    pdf_status: str = "",
    review_status: str = "reviewed",
    status: str = "",
    tag: str = "",
    cluster: str = "",
    sort: str = "asset_score",
) -> List[Dict[str, Any]]:
    q = (q or "").strip().lower()
    code_status = (code_status or "").strip().lower()
    pdf_status = (pdf_status or "").strip().lower()
    review_status = (review_status or "").strip().lower()
    status = (status or "").strip().lower()
    tag = (tag or "").strip()
    cluster = (cluster or "").strip()

    def text_blob(a: Dict[str, Any]) -> str:
        raw = a.get("raw") or {}
        review = raw.get("llm_review") if isinstance(raw.get("llm_review"), dict) else {}
        parts = [
            a.get("asset_id"), a.get("asset_type"), a.get("challenge"), a.get("solution_pattern"),
            a.get("mechanism"), a.get("why_it_is_hard"), a.get("source_title"), a.get("source_venue"),
            raw.get("limitations"), raw.get("evidence"), raw.get("transferable_to"),
            review.get("reusable_insight"), review.get("why_it_works"), review.get("transfer_targets"),
            review.get("non_transferable_parts"), review.get("evidence_quotes"), review.get("code_assessment"),
        ]
        return " ".join(str(x) for x in parts if x).lower()

    out = []
    for a in assets:
        raw = a.get("raw") or {}
        review = raw.get("llm_review") if isinstance(raw.get("llm_review"), dict) else {}
        verdict = (review.get("verdict") or "").lower()
        if q and q not in text_blob(a):
            continue
        if code_status and (a.get("code_status") or "").lower() != code_status:
            continue
        if pdf_status and (a.get("pdf_status") or "").lower() != pdf_status:
            continue
        if review_status in {"reviewed", "llm"} and not verdict:
            continue
        if review_status in {"accept", "weak", "reject", "failed"} and verdict != review_status:
            continue
        if review_status in {"not_reviewed", "unreviewed"} and verdict:
            continue
        asset_status = ((a.get("user_state") or {}).get("status") or "unseen").lower()
        if status and asset_status != status:
            continue
        if tag and tag not in (a.get("tags") or []):
            continue
        if cluster and (a.get("cluster") or "") != cluster:
            continue
        out.append(a)

    if sort == "code":
        out.sort(key=lambda a: score_value(a, "code_readiness"), reverse=True)
    elif sort == "evidence":
        out.sort(key=lambda a: score_value(a, "evidence_strength"), reverse=True)
    else:
        out.sort(key=lambda a: score_value(a, "asset_score"), reverse=True)
    return out


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    overview = fetch_home_overview()
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "request": request,
            **overview,
            "db_path": str(DB_PATH),
            "url_depth": 0,
        },
    )


@app.get("/articles", response_class=HTMLResponse)
def articles_page(
    request: Request,
    q: str = Query(""),
    priority: str = Query(""),
    sort: str = Query("rank_score"),
    limit: int = Query(100, ge=1, le=1000),
) -> HTMLResponse:
    all_articles = fetch_all_articles()
    filtered = filter_articles(all_articles, q=q, priority=priority, sort=sort)[:limit]
    return templates.TemplateResponse(
        request=request,
        name="articles.html",
        context={
            "request": request,
            "articles": filtered,
            "total": len(all_articles),
            "shown": len(filtered),
            "q": q,
            "priority": priority,
            "sort": sort,
            "limit": limit,
            "url_depth": 0,
        },
    )


@app.get("/assets", response_class=HTMLResponse)
def assets_page(
    request: Request,
    q: str = Query(""),
    code_status: str = Query(""),
    pdf_status: str = Query(""),
    review_status: str = Query("reviewed"),
    status: str = Query(""),
    tag: str = Query(""),
    cluster: str = Query(""),
    sort: str = Query("asset_score"),
    limit: int = Query(100, ge=1, le=1000),
) -> HTMLResponse:
    all_assets = fetch_all_assets()
    reviewed_total = sum(1 for a in all_assets if ((a.get("raw") or {}).get("llm_review") or {}).get("verdict"))
    filtered = filter_assets(
        all_assets,
        q=q,
        code_status=code_status,
        pdf_status=pdf_status,
        review_status=review_status,
        status=status,
        tag=tag,
        cluster=cluster,
        sort=sort,
    )[:limit]
    return templates.TemplateResponse(
        request=request,
        name="assets.html",
        context={
            "request": request,
            "assets": filtered,
            "total": len(all_assets),
            "reviewed_total": reviewed_total,
            "shown": len(filtered),
            "q": q,
            "code_status": code_status,
            "pdf_status": pdf_status,
            "review_status": review_status,
            "status": status,
            "tag": tag,
            "cluster": cluster,
            "clusters": collect_asset_clusters(all_assets),
            "tags": collect_asset_tags(all_assets),
            "sort": sort,
            "limit": limit,
            "url_depth": 0,
        },
    )


@app.get("/assets/today", response_class=HTMLResponse)
def assets_today(request: Request, goal: int = Query(5, ge=1, le=50)) -> HTMLResponse:
    all_assets = filter_assets(fetch_all_assets(), review_status="reviewed", sort="asset_score")
    today = today_key()
    done_today = [
        asset
        for asset in all_assets
        if (asset.get("user_state") or {}).get("status") == "done"
        and (asset.get("user_state") or {}).get("completed_on") == today
    ]
    queue = [
        asset
        for asset in all_assets
        if ((asset.get("user_state") or {}).get("status") or "unseen") not in {"done", "skip"}
    ][: max(goal - len(done_today), 1)]
    return templates.TemplateResponse(
        request=request,
        name="assets_today.html",
        context={
            "request": request,
            "assets": queue[:1],
            "queue": queue,
            "done_today": len(done_today),
            "goal": goal,
            "clusters": collect_asset_clusters(all_assets),
            "url_depth": 1,
        },
    )


@app.post("/assets/{asset_id}/state")
async def update_asset_state(request: Request, asset_id: str) -> RedirectResponse:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    form = {key: values[-1] for key, values in parse_qs(raw_body, keep_blank_values=True).items()}
    status = str(form.get("status") or "reading")
    tag = str(form.get("tag") or "")
    next_url = relative_redirect_url(str(form.get("next") or ""), request.url.path, fallback=f"/assets/{asset_id}")
    set_asset_state(asset_id, status=status, tag=tag)
    return RedirectResponse(next_url, status_code=303)


@app.get("/assets/{asset_id}", response_class=HTMLResponse)
def asset_detail(request: Request, asset_id: str) -> HTMLResponse:
    asset = fetch_asset(asset_id)
    if asset is None:
        return templates.TemplateResponse(
            request=request,
            name="asset_detail.html",
            context={"request": request, "asset": None, "asset_nav": [], "url_depth": 1},
            status_code=404,
        )
    asset_nav = fetch_asset_navigation()
    if not any(item.get("asset_id") == asset_id for item in asset_nav):
        asset_nav.insert(
            0,
            {
                "asset_id": asset.get("asset_id"),
                "asset_score": asset.get("asset_score"),
                "source_title": asset.get("source_title"),
                "source_venue": asset.get("source_venue"),
                "source_year": asset.get("source_year"),
                "raw": asset.get("raw") or {},
            },
        )
    return templates.TemplateResponse(
        request=request,
        name="asset_detail.html",
        context={"request": request, "asset": asset, "asset_nav": asset_nav, "url_depth": 1},
    )


@app.get("/asset-files/{rel_path:path}")
def asset_file(rel_path: str) -> Response:
    path = safe_asset_file_path(rel_path)
    if path is None:
        return Response("Not found", status_code=404)
    return FileResponse(path)


@app.get("/articles/{article_id}", response_class=HTMLResponse)
def article_detail(request: Request, article_id: int) -> HTMLResponse:
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        return templates.TemplateResponse(
            request=request,
            name="article_detail.html",
            context={"request": request, "article": None, "scores": [], "url_depth": 1},
            status_code=404,
        )
    article = row_to_dict(row)
    return templates.TemplateResponse(
        request=request,
        name="article_detail.html",
        context={
            "request": request,
            "article": article,
            "scores": score_items(article),
            "url_depth": 1,
        },
    )
