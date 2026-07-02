from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .assets import read_assets, row_to_asset, stable_asset_id, utc_now, write_assets
from .discover_code import discover_one
from .ingest_pdf import extract_text, ingest_one
from .io_utils import clean_text, read_jsonl, write_jsonl
from .llm_review_assets import annotate_review_metadata, build_runner, provider_cache_dir, reviewer_identity, Runner, review_one
from .verify_code import verify_one


PRIMARY_VENUES = {
    "NEURIPS",
    "ICML",
    "ICLR",
    "CVPR",
    "ICCV",
    "ECCV",
    "ACL",
    "EMNLP",
    "NAACL",
    "JMLR",
    "AISTATS",
    "UAI",
    "CORL",
}

EXCEPTIONAL_VENUES = {
    "AAAI",
    "IJCAI",
    "KDD",
    "WEB",
    "WWW",
    "SIGIR",
}

OPENALEX_PRIMARY_SOURCES = {
    "NEURIPS": "S4306420609",
    "ICML": "S4306419644",
    "ICLR": "S4306419637",
    "JMLR": "S118988714",
    "AISTATS": "S4306419146",
    "UAI": "S4306421103",
    "CORL": "S4306506823",
}

OPENALEX_EXCEPTIONAL_SOURCES = {
    "AAAI": "S4210191458",
    "IJCAI": "S4306419999",
    "WEB": "S4306421067",
    "SIGIR": "S4306418959",
}

CVF_VENUES = ("CVPR", "ICCV")
ACL_EVENTS = {
    "ACL": "acl",
    "EMNLP": "emnlp",
    "NAACL": "naacl",
}
ACL_ACCEPTED_CODE_ASSESSMENTS = {"official", "community"}

SOURCE_GROUPS = {
    "ML": {"NEURIPS", "ICML", "ICLR"},
    "ML-EXTRA": {"JMLR", "AISTATS", "UAI", "CORL"},
    "CVF": {"CVPR", "ICCV"},
    "ACL": {"ACL", "EMNLP", "NAACL"},
    "NLP": {"ACL", "EMNLP", "NAACL"},
}

DEFAULT_USER_AGENT = "research-idea-scout-assets"
OPENALEX_SELECT = ",".join(
    [
        "id",
        "ids",
        "doi",
        "title",
        "publication_year",
        "cited_by_count",
        "abstract_inverted_index",
        "primary_location",
        "best_oa_location",
        "open_access",
        "authorships",
    ]
)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def norm_venue(value: Any) -> str:
    text = clean_text(value).upper()
    aliases = {
        "NIPS": "NEURIPS",
        "WWW": "WEB",
        "THE WEB CONFERENCE": "WEB",
    }
    return aliases.get(text, text)


def selected_sources(value: str) -> set[str]:
    value = clean_text(value or "primary")
    if not value or value.lower() == "primary":
        return set().union(*SOURCE_GROUPS.values())
    out: set[str] = set()
    for part in re.split(r"[,;\s]+", value):
        key = norm_venue(part)
        if not key:
            continue
        if key in SOURCE_GROUPS:
            out.update(SOURCE_GROUPS[key])
        else:
            out.add(key)
    return out


def citation_threshold(year: int, exceptional: bool = False) -> int:
    age = max(0, datetime.now(timezone.utc).year - int(year))
    if exceptional:
        if age >= 6:
            return 1800
        if age >= 3:
            return 900
        return 450
    if age >= 6:
        return 250
    if age >= 3:
        return 120
    if age >= 1:
        return 50
    return 20


def passes_impact_policy(record: Dict[str, Any], include_exceptional: bool = False) -> bool:
    venue = norm_venue(record.get("venue"))
    try:
        year = int(record.get("year") or record.get("publication_year") or 0)
    except Exception:
        year = 0
    citations = int(record.get("citation_count") or record.get("cited_by_count") or 0)

    if venue in PRIMARY_VENUES:
        return citations >= citation_threshold(year, exceptional=False)
    if include_exceptional and venue in EXCEPTIONAL_VENUES:
        return citations >= citation_threshold(year, exceptional=True)
    return False


def request_json(url: str, timeout: int) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_text(url: str, timeout: int, max_bytes: int = 20_000_000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks: List[bytes] = []
        total = 0
        while total < max_bytes:
            try:
                chunk = resp.read(min(64 * 1024, max_bytes - total))
            except (socket.timeout, TimeoutError):
                if chunks:
                    break
                raise
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks).decode("utf-8", errors="ignore")


def request_text_with_curl(url: str, timeout: int, max_bytes: int = 2_000_000) -> str:
    cmd = [
        "curl",
        "-L",
        "--silent",
        "--show-error",
        "--max-time",
        str(timeout),
        "-A",
        DEFAULT_USER_AGENT,
        url,
    ]
    try:
        completed = subprocess.run(cmd, text=False, capture_output=True, timeout=timeout + 5)
        data = completed.stdout[:max_bytes]
        if data:
            return data.decode("utf-8", errors="ignore")
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="ignore")[-300:]
            raise RuntimeError(stderr or f"curl exited {completed.returncode}")
        return ""
    except subprocess.TimeoutExpired as e:
        data = (e.stdout or b"")[:max_bytes]
        if data:
            return data.decode("utf-8", errors="ignore")
        raise


def abstract_from_inverted_index(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: List[tuple[int, str]] = []
    for word, offsets in index.items():
        if not isinstance(offsets, list):
            continue
        for pos in offsets:
            try:
                positions.append((int(pos), str(word)))
            except Exception:
                continue
    return " ".join(word for _pos, word in sorted(positions))


def choose_pdf_url(work: Dict[str, Any]) -> str:
    locations = [
        work.get("best_oa_location") if isinstance(work.get("best_oa_location"), dict) else {},
        work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {},
    ]
    for loc in locations:
        pdf_url = clean_text(loc.get("pdf_url"))
        if pdf_url:
            return pdf_url
    for loc in locations:
        landing = clean_text(loc.get("landing_page_url"))
        if landing.lower().split("?", 1)[0].endswith(".pdf"):
            return landing
    open_access = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    oa_url = clean_text(open_access.get("oa_url"))
    if oa_url.lower().split("?", 1)[0].endswith(".pdf"):
        return oa_url
    return ""


def authors_from_openalex(work: Dict[str, Any], limit: int = 12) -> str:
    names: List[str] = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") if isinstance(authorship.get("author"), dict) else {}
        name = clean_text(author.get("display_name"))
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return "; ".join(names)


def openalex_work_id(work: Dict[str, Any]) -> str:
    raw = clean_text(work.get("id"))
    return raw.rstrip("/").rsplit("/", 1)[-1] if raw else stable_asset_id(json.dumps(work, sort_keys=True))


def openalex_work_to_record(work: Dict[str, Any], venue: str, tier: str) -> Dict[str, Any]:
    work_id = openalex_work_id(work)
    primary = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    best = work.get("best_oa_location") if isinstance(work.get("best_oa_location"), dict) else {}
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    pdf_url = choose_pdf_url(work)
    url = clean_text(primary.get("landing_page_url") or best.get("landing_page_url") or ids.get("doi") or work.get("doi") or work.get("id"))
    year = int(work.get("publication_year") or 0)
    citations = int(work.get("cited_by_count") or 0)
    return {
        "paper_id": f"openalex::{work_id}",
        "title": clean_text(work.get("title"), 500),
        "abstract": clean_text(abstract_from_inverted_index(work.get("abstract_inverted_index")), 4000),
        "venue": norm_venue(venue),
        "year": year,
        "authors": authors_from_openalex(work),
        "url": url,
        "pdf_url": pdf_url,
        "doi": clean_text(ids.get("doi") or work.get("doi")),
        "openalex_id": clean_text(work.get("id")),
        "citation_count": citations,
        "source": "openalex",
        "impact_tier": tier,
        "profile_name": "high_impact_2016_2025",
        "rank_score": min(10.0, max(0.0, citations / 250.0)),
        "score_overall_fit": min(10.0, 6.5 + citations / 1200.0),
        "score_theory_novelty": min(10.0, 5.5 + citations / 1500.0),
    }


def fetch_openalex_source_records(
    venue: str,
    source_id: str,
    min_year: int,
    max_year: int,
    per_venue: int,
    timeout: int,
    tier: str,
    include_exceptional: bool = False,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    page = 1
    per_page = min(200, max(1, per_venue * 2))
    while len(records) < per_venue and page <= 6:
        params = {
            "filter": (
                f"from_publication_date:{min_year}-01-01,"
                f"to_publication_date:{max_year}-12-31,"
                f"locations.source.id:{source_id}"
            ),
            "sort": "cited_by_count:desc",
            "per-page": str(per_page),
            "page": str(page),
            "select": OPENALEX_SELECT,
        }
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        data = request_json(url, timeout=timeout)
        rows = data.get("results") if isinstance(data.get("results"), list) else []
        if not rows:
            break
        for work in rows:
            if not isinstance(work, dict):
                continue
            record = openalex_work_to_record(work, venue=venue, tier=tier)
            if not record["title"] or not record["pdf_url"]:
                continue
            if passes_impact_policy(record, include_exceptional=include_exceptional):
                records.append(record)
            if len(records) >= per_venue:
                break
        page += 1
        time.sleep(0.2)
    return records


def match_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", clean_text(value).lower()) if len(token) >= 3}


def title_match_score(want: str, got: str) -> float:
    want_tokens = match_tokens(want)
    got_tokens = match_tokens(got)
    if not want_tokens or not got_tokens:
        return 0.0
    overlap = len(want_tokens & got_tokens)
    coverage = overlap / len(want_tokens)
    precision = overlap / len(got_tokens)
    return round(0.75 * coverage + 0.25 * precision, 4)


def work_source_display_name(work: Dict[str, Any]) -> str:
    primary = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
    return clean_text(source.get("display_name"))


def seed_to_record(seed: Dict[str, Any], work: Dict[str, Any] | None = None) -> Dict[str, Any]:
    seed = dict(seed)
    venue = norm_venue(seed.get("venue") or (work_source_display_name(work or {}) if work else "") or "CODE-SEED")
    if work:
        record = openalex_work_to_record(work, venue=venue, tier="code_seed_high_impact")
        record["source"] = "seed_openalex"
    else:
        record = {
            "paper_id": clean_text(seed.get("paper_id") or seed.get("id") or seed.get("doi") or seed.get("title")),
            "title": clean_text(seed.get("title"), 500),
            "abstract": clean_text(seed.get("abstract"), 4000),
            "venue": venue,
            "year": int(seed.get("year") or seed.get("publication_year") or 0),
            "authors": clean_text(seed.get("authors")),
            "url": clean_text(seed.get("url") or seed.get("paper_url") or seed.get("doi")),
            "pdf_url": clean_text(seed.get("pdf_url") or seed.get("pdf")),
            "doi": clean_text(seed.get("doi")),
            "citation_count": int(seed.get("citation_count") or seed.get("cited_by_count") or 0),
            "source": "seed",
            "impact_tier": "code_seed_high_impact",
            "profile_name": "high_impact_2016_2025",
        }

    for key in ["title", "abstract", "venue", "year", "authors", "url", "pdf_url", "doi", "citation_count"]:
        value = seed.get(key)
        if clean_text(value):
            record[key] = value
    for key in ["code_url", "github_url", "repo_url"]:
        value = clean_text(seed.get(key))
        if value:
            record["code_url"] = value
            break
    record["profile_name"] = "high_impact_2016_2025"
    citations = int(record.get("citation_count") or 0)
    record.setdefault("rank_score", min(10.0, max(0.0, citations / 250.0)))
    record.setdefault("score_overall_fit", min(10.0, 6.5 + citations / 1200.0))
    record.setdefault("score_theory_novelty", min(10.0, 5.5 + citations / 1500.0))
    return record


def passes_code_seed_policy(record: Dict[str, Any], min_citations: int) -> bool:
    code_url = clean_text(record.get("code_url") or record.get("github_url") or record.get("repo_url"))
    citations = int(record.get("citation_count") or record.get("cited_by_count") or 0)
    return (
        code_url.startswith("https://github.com/")
        and citations >= min_citations
        and bool(clean_text(record.get("title")))
        and bool(clean_text(record.get("pdf_url")))
    )


def fetch_openalex_title_work(title: str, timeout: int) -> Dict[str, Any] | None:
    if not clean_text(title):
        return None
    params = {
        "search": title,
        "per-page": "5",
        "select": OPENALEX_SELECT,
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = request_json(url, timeout=timeout)
    rows = data.get("results") if isinstance(data.get("results"), list) else []
    scored = []
    for work in rows:
        if not isinstance(work, dict):
            continue
        score = title_match_score(title, clean_text(work.get("title")))
        if score >= 0.70:
            scored.append((score, int(work.get("cited_by_count") or 0), work))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def fetch_seed_records(seed_file: str, timeout: int, min_citations: int, log_path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not seed_file:
        return records
    for index, seed in enumerate(read_jsonl(seed_file), 1):
        title = clean_text(seed.get("title"), 500)
        try:
            work = fetch_openalex_title_work(title, timeout=timeout)
        except Exception as e:
            append_jsonl(
                log_path,
                {"event": "seed_record_openalex_failed", "index": index, "title": title, "error": str(e), "at": utc_now()},
            )
            work = None

        record = seed_to_record(seed, work=work)
        if passes_code_seed_policy(record, min_citations=min_citations):
            records.append(record)
            append_jsonl(
                log_path,
                {
                    "event": "seed_record_done",
                    "index": index,
                    "title": record.get("title"),
                    "citations": record.get("citation_count"),
                    "at": utc_now(),
                },
            )
        else:
            append_jsonl(
                log_path,
                {
                    "event": "seed_record_skipped",
                    "index": index,
                    "title": title,
                    "reason": "seed_policy",
                    "citations": record.get("citation_count"),
                    "at": utc_now(),
                },
            )
    return records


def slugify(value: str, max_chars: int = 96) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_chars].strip("-") or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def absolute_url(url: str, base: str = "https://openaccess.thecvf.com") -> str:
    return urllib.parse.urljoin(base, html.unescape(url))


def parse_cvf_openaccess(html_text: str, venue: str, year: int, page_url: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    pattern = re.compile(
        r'<dt class="ptitle">\s*<br>\s*<a href="(?P<html>[^"]+)">(?P<title>.*?)</a>\s*</dt>\s*'
        r"<dd>(?P<authors>.*?)</dd>\s*"
        r"<dd>(?P<links>.*?)</dd>",
        flags=re.S | re.I,
    )
    for match in pattern.finditer(html_text):
        title = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", match.group("title"))), 500)
        authors = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", match.group("authors"))), 1000)
        authors = re.sub(r"\s*,\s*", "; ", authors)
        links = match.group("links")
        pdf_match = re.search(r'href="([^"]+\.pdf)"[^>]*>\s*pdf\s*</a>', links, flags=re.I)
        if not title or not pdf_match:
            continue
        html_url = absolute_url(match.group("html"), page_url)
        pdf_url = absolute_url(pdf_match.group(1), page_url)
        records.append(
            {
                "paper_id": f"cvf::{norm_venue(venue).lower()}::{year}::{slugify(title)}",
                "title": title,
                "venue": norm_venue(venue),
                "year": int(year),
                "authors": authors,
                "url": html_url,
                "pdf_url": pdf_url,
                "source": "cvf_openaccess",
                "impact_tier": "primary_unranked",
            }
        )
    return records


def cvf_page_candidates(venue: str, year: int) -> List[str]:
    code = f"{venue.upper()}{year}"
    return [
        f"https://openaccess.thecvf.com/{code}?day=all",
        f"https://openaccess.thecvf.com/{code}.py",
    ]


def fetch_cvf_records(venue: str, year: int, limit: int, timeout: int) -> List[Dict[str, Any]]:
    last_error = ""
    for page_url in cvf_page_candidates(venue, year):
        try:
            html_text = request_text_with_curl(page_url, timeout=timeout, max_bytes=1_500_000)
            records = parse_cvf_openaccess(html_text, venue=venue, year=year, page_url=page_url)
            if records:
                return records[:limit]
            records = []
            for day_url in parse_cvf_day_links(html_text, page_url):
                day_html = request_text_with_curl(day_url, timeout=timeout, max_bytes=1_500_000)
                records.extend(parse_cvf_openaccess(day_html, venue=venue, year=year, page_url=day_url))
                if len(records) >= limit:
                    return records[:limit]
            if records:
                return records[:limit]
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            continue
    if last_error:
        raise RuntimeError(last_error)
    return []


def parse_cvf_day_links(html_text: str, page_url: str) -> List[str]:
    links: List[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href="(?P<href>[^"]+\?day=[^"]+)"', html_text, flags=re.I):
        url = absolute_url(match.group("href"), page_url)
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def acl_allowed_volume_id(volume_id: str, venue: str) -> bool:
    volume_id = clean_text(volume_id).strip('"').lower()
    venue = norm_venue(venue).lower()
    excluded = ("findings", "short", "demo", "tutorial", "workshop", "industry", "student", "srw")
    if any(part in volume_id for part in excluded):
        return False
    if venue == "acl":
        return volume_id.endswith("acl-long") or volume_id.endswith("acl-main") or re.fullmatch(r"p\d{2}-1", volume_id) is not None
    if venue == "emnlp":
        return volume_id.endswith("emnlp-main") or re.fullmatch(r"d\d{2}-1", volume_id) is not None
    if venue == "naacl":
        return (
            volume_id.endswith("naacl-main")
            or volume_id.endswith("naacl-long")
            or re.fullmatch(r"n\d{2}-1", volume_id) is not None
        )
    return False


def parse_acl_authors(fragment: str) -> str:
    names: List[str] = []
    for match in re.finditer(r'<a[^>]+href=/people/[^>]+>(.*?)</a>', fragment, flags=re.S | re.I):
        name = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))), 200)
        if name and name not in names:
            names.append(name)
    return "; ".join(names[:16])


def parse_acl_anthology_event(html_text: str, venue: str, year: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    volume_re = re.compile(
        r'<div id=(?P<volume>[A-Za-z0-9][^>\s]+)>(?P<body>.*?)(?=<div id=[A-Za-z0-9][^>\s]+|\Z)',
        flags=re.S | re.I,
    )
    paper_re = re.compile(
        r'<a[^>]+href=(?P<pdf>(?:https://aclanthology\.org)?/(?:[0-9]{4}\.[^>"\s]+?|[A-Z]\d{2}-\d+)\.pdf|https://aclanthology\.org/(?:[0-9]{4}\.[^>"\s]+?|[A-Z]\d{2}-\d+)\.pdf)[^>]*>pdf\s*</a>'
        r'(?P<body>.*?)(?=<div class="d-sm-flex align-items-stretch mb-3"|</div>\s*</div>|<hr|\Z)',
        flags=re.S | re.I,
    )
    title_re = re.compile(
        r"<strong>\s*<a[^>]+href=(?P<href>[^>\s]+)[^>]*>(?P<title>.*?)</a>\s*</strong>",
        flags=re.S | re.I,
    )

    for volume in volume_re.finditer(html_text):
        volume_id = clean_text(volume.group("volume")).strip('"')
        if not acl_allowed_volume_id(volume_id, venue):
            continue
        for paper in paper_re.finditer(volume.group("body")):
            paper_body = paper.group("body")
            title_match = title_re.search(paper_body)
            if not title_match:
                continue
            title = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", title_match.group("title"))), 500)
            href = title_match.group("href").strip('"')
            url = urllib.parse.urljoin("https://aclanthology.org/", href)
            pdf_url = urllib.parse.urljoin("https://aclanthology.org/", paper.group("pdf").strip('"'))
            paper_key = pdf_url.rsplit("/", 1)[-1].removesuffix(".pdf")
            if (
                not title
                or paper_key.endswith(".0")
                or re.fullmatch(r"[A-Z]\d{2}-\d", paper_key)
                or re.fullmatch(r"[A-Z]\d{2}-\d000", paper_key)
            ):
                continue
            records.append(
                {
                    "paper_id": f"acl-anthology::{norm_venue(venue).lower()}::{year}::{paper_key}",
                    "title": title,
                    "venue": norm_venue(venue),
                    "year": int(year),
                    "authors": parse_acl_authors(paper_body),
                    "url": url,
                    "pdf_url": pdf_url,
                    "source": "acl_anthology",
                    "impact_tier": "primary_unranked",
                    "acl_volume_id": volume_id,
                    "acl_track": "main_long",
                }
            )
    return records


def acl_event_url(venue: str, year: int) -> str:
    slug = ACL_EVENTS[norm_venue(venue)]
    return f"https://aclanthology.org/events/{slug}-{year}/"


def fetch_acl_records(venue: str, year: int, limit: int, timeout: int) -> List[Dict[str, Any]]:
    html_text = request_text_with_curl(acl_event_url(venue, year), timeout=timeout, max_bytes=700_000)
    return parse_acl_anthology_event(html_text, venue=venue, year=year)[:limit]


def existing_title_keys(paths: Iterable[Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        for asset in read_jsonl(path):
            title = ""
            papers = asset.get("source_papers") if isinstance(asset.get("source_papers"), list) else []
            if papers and isinstance(papers[0], dict):
                title = clean_text(papers[0].get("title"))
            raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
            title = title or clean_text(raw.get("title"))
            if title:
                keys.add(slugify(title, max_chars=140))
    return keys


def existing_store_asset_paths(store: Path) -> List[Path]:
    return sorted(path for path in store.glob("*/assets.jsonl") if path.is_file())


def dedupe_records(records: Iterable[Dict[str, Any]], skip_titles: set[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        title_key = slugify(clean_text(record.get("title")), max_chars=140)
        id_key = clean_text(record.get("paper_id") or record.get("openalex_id") or record.get("pdf_url"))
        key = id_key or title_key
        if not key or key in seen or title_key in skip_titles:
            continue
        seen.add(key)
        out.append(record)
    return out


def safe_filename(record: Dict[str, Any]) -> str:
    seed = clean_text(record.get("paper_id") or record.get("title") or "paper")
    return slugify(seed, max_chars=120)


def download_pdf(url: str, pdf_path: Path, timeout: int, max_bytes: int = 80_000_000) -> str:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = 0
            with pdf_path.open("wb") as f:
                while True:
                    if time.monotonic() - started > timeout:
                        pdf_path.unlink(missing_ok=True)
                        return "download_timeout"
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        pdf_path.unlink(missing_ok=True)
                        return "downloaded_file_too_large"
                    f.write(chunk)
        if pdf_path.stat().st_size < 1024:
            pdf_path.unlink(missing_ok=True)
            return "downloaded_file_too_small"
        return ""
    except urllib.error.HTTPError as e:
        pdf_path.unlink(missing_ok=True)
        return f"http_{e.code}"
    except Exception as e:
        pdf_path.unlink(missing_ok=True)
        return f"{type(e).__name__}: {e}"


def extract_pdf_text_for_record(record: Dict[str, Any], work_dir: Path, timeout: int, delete_pdf: bool) -> Dict[str, Any]:
    out = dict(record)
    pdf_url = clean_text(record.get("pdf_url"))
    if not pdf_url:
        out["pdf_failure_reason"] = "missing_pdf_url"
        return out

    base = safe_filename(out)
    pdf_path = work_dir / "pdfs" / f"{base}.pdf"
    text_path = work_dir / "texts" / f"{base}.txt"

    if not text_path.exists():
        err = download_pdf(pdf_url, pdf_path, timeout=timeout)
        if err:
            out["pdf_failure_reason"] = f"download:{err}"
            return out
        err = extract_text(pdf_path, text_path, timeout=timeout)
        if err:
            out["pdf_failure_reason"] = f"extract:{err}"
            if delete_pdf and pdf_path.exists():
                pdf_path.unlink(missing_ok=True)
            return out
    if delete_pdf and pdf_path.exists():
        pdf_path.unlink(missing_ok=True)

    out["text_path"] = str(text_path)
    out["local_pdf_path"] = "" if delete_pdf else str(pdf_path)
    out["pdf_failure_reason"] = ""
    return out


def asset_has_repo(asset: Dict[str, Any]) -> bool:
    code = asset.get("code") if isinstance(asset.get("code"), dict) else {}
    return clean_text(code.get("url")).startswith("https://github.com/") and clean_text(code.get("status")) in {
        "repo_found",
        "open_source_verified",
    }


def acl_code_assessment(asset: Dict[str, Any]) -> str:
    review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else {}
    return clean_text(review.get("code_assessment")).lower()


def acl_code_evidence_ok(asset: Dict[str, Any]) -> bool:
    return asset_has_repo(asset) and acl_code_assessment(asset) in ACL_ACCEPTED_CODE_ASSESSMENTS


def is_acl_family_record(record: Dict[str, Any]) -> bool:
    return norm_venue(record.get("venue")) in ACL_EVENTS or clean_text(record.get("source")).lower() == "acl_anthology"


def review_asset_if_requested(
    asset: Dict[str, Any],
    provider: str,
    model: str,
    model_command: str,
    openai_base_url: str,
    timeout: int,
    cache_dir: Path,
    no_llm: bool,
) -> Dict[str, Any]:
    if no_llm:
        return asset
    reviewer_model = reviewer_identity(provider, model, model_command)
    runner = build_runner(provider, model, model_command, openai_base_url)
    reviewed = review_one(
        asset,
        runner=runner,
        timeout=timeout,
        cache_dir=provider_cache_dir(cache_dir, provider, reviewer_model),
        use_cache=True,
    )
    return annotate_review_metadata(reviewed, provider=provider, reviewer_model=reviewer_model)


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def load_existing_assets(path: Path) -> List[Dict[str, Any]]:
    return read_assets(path) if path.exists() else []


def write_manifest(batch_dir: Path, args: argparse.Namespace, stats: Dict[str, int], records_path: Path, assets_path: Path) -> None:
    manifest = {
        "batch": args.batch,
        "built_at": now_stamp(),
        "policy": "primary venues by default; AAAI/IJCAI/KDD/Web/SIGIR require --include-exceptional and higher citation thresholds",
        "primary_venues": sorted(PRIMARY_VENUES),
        "exceptional_venues": sorted(EXCEPTIONAL_VENUES),
        "min_year": args.min_year,
        "max_year": args.max_year,
        "stats": stats,
        "records_path": str(records_path),
        "assets_path": str(assets_path),
        "delete_pdfs": args.delete_pdfs,
        "no_llm": args.no_llm,
        "review_provider": args.review_provider,
        "review_model": args.review_model,
    }
    (batch_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_candidates(args: argparse.Namespace, batch_dir: Path, log_path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = fetch_seed_records(
        args.seed_file,
        timeout=args.timeout,
        min_citations=args.seed_min_citations,
        log_path=log_path,
    )
    wanted = selected_sources(args.sources)
    openalex_sources = dict(OPENALEX_PRIMARY_SOURCES)
    if args.include_exceptional:
        openalex_sources.update(OPENALEX_EXCEPTIONAL_SOURCES)

    per_venue = max(1, args.per_venue)
    for venue, source_id in openalex_sources.items():
        if venue not in wanted:
            continue
        try:
            tier = "exceptional" if venue in EXCEPTIONAL_VENUES else "primary"
            got = fetch_openalex_source_records(
                venue=venue,
                source_id=source_id,
                min_year=args.min_year,
                max_year=args.max_year,
                per_venue=per_venue,
                timeout=args.timeout,
                tier=tier,
                include_exceptional=args.include_exceptional,
            )
            records.extend(got)
            append_jsonl(log_path, {"event": "openalex_source_done", "venue": venue, "records": len(got), "at": utc_now()})
        except Exception as e:
            append_jsonl(log_path, {"event": "openalex_source_failed", "venue": venue, "error": str(e), "at": utc_now()})

    cvf_years = [y for y in range(args.min_year, args.max_year + 1) if y >= 2016]
    if args.cvf_per_year > 0:
        for venue in CVF_VENUES:
            if venue not in wanted:
                continue
            for year in cvf_years:
                if venue == "ICCV" and year % 2 == 0:
                    continue
                if venue == "CVPR" and year > 2025:
                    continue
                try:
                    got = fetch_cvf_records(venue, year, limit=args.cvf_per_year, timeout=args.timeout)
                    records.extend(got)
                    append_jsonl(log_path, {"event": "cvf_year_done", "venue": venue, "year": year, "records": len(got), "at": utc_now()})
                except Exception as e:
                    append_jsonl(
                        log_path,
                        {"event": "cvf_year_failed", "venue": venue, "year": year, "error": str(e), "at": utc_now()},
                    )

    acl_years = [y for y in range(args.min_year, args.max_year + 1) if y >= 2016]
    if args.acl_per_year > 0:
        for venue in ACL_EVENTS:
            if venue not in wanted:
                continue
            for year in acl_years:
                try:
                    got = fetch_acl_records(venue, year, limit=args.acl_per_year, timeout=args.timeout)
                    records.extend(got)
                    append_jsonl(log_path, {"event": "acl_year_done", "venue": venue, "year": year, "records": len(got), "at": utc_now()})
                except Exception as e:
                    append_jsonl(
                        log_path,
                        {"event": "acl_year_failed", "venue": venue, "year": year, "error": str(e), "at": utc_now()},
                    )

    return records


def process_records(args: argparse.Namespace, records: List[Dict[str, Any]], batch_dir: Path, log_path: Path) -> Dict[str, int]:
    records_path = batch_dir / "candidates.jsonl"
    accepted_records_path = batch_dir / "accepted_records.jsonl"
    rejected_path = batch_dir / "rejected_or_pending.jsonl"
    assets_path = batch_dir / "assets.jsonl"
    tmp_assets_path = batch_dir / "assets.tmp.jsonl"
    work_dir = batch_dir / "work"
    review_cache = batch_dir / "reviews"

    existing_assets = load_existing_assets(assets_path)
    assets_by_id = {clean_text(a.get("asset_id")): a for a in existing_assets if clean_text(a.get("asset_id"))}
    stats: Dict[str, int] = {
        "records": len(records),
        "processed": 0,
        "text_ready": 0,
        "code_found": 0,
        "code_verified": 0,
        "llm_accept_or_weak": 0,
        "acl_code_evidence_rejected": 0,
        "assets_written": len(assets_by_id),
        "rejected_or_pending": 0,
        "failed": 0,
    }

    write_jsonl(records_path, records)

    for record in records[: args.max_records or None]:
        stats["processed"] += 1
        append_jsonl(
            log_path,
            {
                "event": "record_started",
                "index": stats["processed"],
                "title": record.get("title"),
                "venue": record.get("venue"),
                "year": record.get("year"),
                "at": utc_now(),
            },
        )
        try:
            with_text = extract_pdf_text_for_record(record, work_dir=work_dir, timeout=args.timeout, delete_pdf=args.delete_pdfs)
            if not clean_text(with_text.get("text_path")):
                stats["rejected_or_pending"] += 1
                append_jsonl(rejected_path, with_text | {"reject_reason": "pdf_text_unavailable"})
                continue
            stats["text_ready"] += 1
            append_jsonl(accepted_records_path, with_text)

            asset = row_to_asset(with_text, profile_name="high_impact_2016_2025")
            asset = ingest_one(asset, output_dir=work_dir / "ingest", timeout=args.timeout, use_fallback=False)
            asset = discover_one(
                asset,
                timeout=args.timeout,
                use_github_search=not args.no_github_search,
                force=False,
            )
            if not asset_has_repo(asset):
                stats["rejected_or_pending"] += 1
                append_jsonl(rejected_path, asset | {"reject_reason": "repo_not_found"})
                continue
            stats["code_found"] += 1

            asset = verify_one(asset, timeout=args.timeout, offline=args.offline_github_verify)
            if asset_has_repo(asset):
                stats["code_verified"] += 1
            else:
                stats["rejected_or_pending"] += 1
                append_jsonl(rejected_path, asset | {"reject_reason": "repo_unverified"})
                continue

            asset = review_asset_if_requested(
                asset,
                provider=args.review_provider,
                model=args.review_model,
                model_command=args.model_command,
                openai_base_url=args.openai_base_url,
                timeout=args.llm_timeout,
                cache_dir=review_cache,
                no_llm=args.no_llm,
            )
            review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else {}
            verdict = clean_text(review.get("verdict")) if review else "not_reviewed"
            quality = int(review.get("asset_quality") or 0) if review else 0
            if is_acl_family_record(record) and not acl_code_evidence_ok(asset):
                stats["acl_code_evidence_rejected"] += 1
                stats["rejected_or_pending"] += 1
                assessment = acl_code_assessment(asset) or "missing"
                append_jsonl(rejected_path, asset | {"reject_reason": f"acl_code_assessment_{assessment}"})
                continue
            if args.no_llm or (verdict in {"accept", "weak"} and quality >= args.min_llm_quality):
                stats["llm_accept_or_weak"] += 1
                assets_by_id[clean_text(asset["asset_id"])] = asset
                write_assets(tmp_assets_path, assets_by_id.values())
                os.replace(tmp_assets_path, assets_path)
                stats["assets_written"] = len(assets_by_id)
                append_jsonl(log_path, {"event": "asset_written", "asset_id": asset.get("asset_id"), "title": record.get("title"), "at": utc_now()})
                if args.rebuild_portal:
                    append_assets_to_portal(
                        repo_root=Path(__file__).resolve().parents[1],
                        store=Path(args.store),
                        assets_path=assets_path,
                        log_path=log_path,
                        event_name="portal_append_incremental",
                    )
            else:
                stats["rejected_or_pending"] += 1
                append_jsonl(rejected_path, asset | {"reject_reason": f"llm_{verdict or 'missing'}_q{quality}"})
        except Exception as e:
            stats["failed"] += 1
            append_jsonl(log_path, {"event": "record_failed", "title": record.get("title"), "error": f"{type(e).__name__}: {e}", "at": utc_now()})
            continue

    write_manifest(batch_dir, args, stats, records_path, assets_path)
    return stats


def append_assets_to_portal(
    repo_root: Path,
    store: Path,
    assets_path: Path,
    log_path: Path,
    event_name: str = "portal_append_done",
) -> None:
    if not assets_path.exists():
        return
    cmd = [
        sys.executable,
        str(repo_root / "web" / "import_jsonl.py"),
        "--input",
        str(assets_path),
        "--db",
        str(store / "portal.db"),
        "--kind",
        "assets",
        "--append",
    ]
    completed = subprocess.run(cmd, cwd=str(repo_root), text=True, capture_output=True, timeout=120)
    append_jsonl(
        log_path,
        {
            "event": event_name,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
            "at": utc_now(),
        },
    )


def rebuild_portal_append(repo_root: Path, store: Path, assets_path: Path, log_path: Path) -> None:
    append_assets_to_portal(repo_root=repo_root, store=store, assets_path=assets_path, log_path=log_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest high-impact, code-backed research assets into the asset store.")
    parser.add_argument("--store", default="/vePFS-Mindverse/user/intern/zhouch/asset_store")
    parser.add_argument("--batch", default="high_impact")
    parser.add_argument("--sources", default="primary", help="Comma list or groups: primary, ml, cvf, acl/nlp, or venue names.")
    parser.add_argument("--min-year", type=int, default=2016)
    parser.add_argument("--max-year", type=int, default=2025)
    parser.add_argument("--per-venue", type=int, default=80)
    parser.add_argument("--cvf-per-year", type=int, default=80)
    parser.add_argument("--acl-per-year", type=int, default=80)
    parser.add_argument("--max-records", type=int, default=0, help="0 means all collected candidates.")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--llm-timeout", type=int, default=180)
    parser.add_argument("--review-provider", choices=["codex", "openai", "command"], default="codex")
    parser.add_argument("--review-model", default=os.environ.get("PAPERHUB_AGENT_MODEL", "gpt-5.5"))
    parser.add_argument("--openai-base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--model-command", default="claude -p", help="Only used with --review-provider command.")
    parser.add_argument("--include-exceptional", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-github-search", action="store_true")
    parser.add_argument("--offline-github-verify", action="store_true")
    parser.add_argument("--delete-pdfs", action="store_true", default=True)
    parser.add_argument("--keep-pdfs", action="store_false", dest="delete_pdfs")
    parser.add_argument("--min-llm-quality", type=int, default=3)
    parser.add_argument("--seed-file", default="", help="Optional JSONL of high-impact papers with known GitHub repos.")
    parser.add_argument("--seed-min-citations", type=int, default=250)
    parser.add_argument("--rebuild-portal", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    store = Path(args.store)
    batch_dir = store / args.batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    log_path = batch_dir / "run_events.jsonl"

    skip_titles = existing_title_keys(existing_store_asset_paths(store))
    append_jsonl(log_path, {"event": "run_started", "args": vars(args), "skip_titles": len(skip_titles), "at": utc_now()})
    candidates = collect_candidates(args, batch_dir=batch_dir, log_path=log_path)
    records = dedupe_records(candidates, skip_titles=skip_titles)
    stats = process_records(args, records=records, batch_dir=batch_dir, log_path=log_path)
    if args.rebuild_portal:
        rebuild_portal_append(repo_root=repo_root, store=store, assets_path=batch_dir / "assets.jsonl", log_path=log_path)
    append_jsonl(log_path, {"event": "run_finished", "stats": stats, "at": utc_now()})
    print(json.dumps({"batch_dir": str(batch_dir), "stats": stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
