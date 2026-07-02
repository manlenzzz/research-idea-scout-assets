from __future__ import annotations

from idea_scout.high_impact_harvest import (
    EXCEPTIONAL_VENUES,
    OPENALEX_EXCEPTIONAL_SOURCES,
    PRIMARY_VENUES,
    acl_code_evidence_ok,
    acl_allowed_volume_id,
    dedupe_records,
    download_pdf,
    existing_store_asset_paths,
    existing_title_keys,
    selected_sources,
    parse_acl_anthology_event,
    openalex_work_to_record,
    parse_cvf_openaccess,
    parse_cvf_day_links,
    passes_impact_policy,
    passes_code_seed_policy,
    fetch_seed_records,
    seed_to_record,
    title_match_score,
)
from idea_scout.llm_review_assets import annotate_review_metadata, build_runner, openai_chat_completions_url, provider_cache_dir


def test_secondary_venues_are_exceptional_not_primary() -> None:
    secondary = {"AAAI", "IJCAI", "KDD", "WEB", "SIGIR"}

    assert secondary.isdisjoint(PRIMARY_VENUES)
    assert secondary.issubset(EXCEPTIONAL_VENUES)


def test_exceptional_openalex_sources_cover_expanded_scope() -> None:
    assert {"AAAI", "IJCAI", "WEB", "SIGIR"}.issubset(OPENALEX_EXCEPTIONAL_SOURCES)


def test_exceptional_venues_need_explicit_flag_and_much_stronger_impact() -> None:
    ordinary_aaai = {"venue": "AAAI", "year": 2024, "citation_count": 120}
    huge_aaai = {"venue": "AAAI", "year": 2020, "citation_count": 2500}
    primary_2024 = {"venue": "ICLR", "year": 2024, "citation_count": 90}

    assert passes_impact_policy(ordinary_aaai) is False
    assert passes_impact_policy(ordinary_aaai, include_exceptional=True) is False
    assert passes_impact_policy(huge_aaai, include_exceptional=True) is True
    assert passes_impact_policy(primary_2024) is True


def test_ml_extra_sources_use_primary_impact_policy() -> None:
    for venue in ["JMLR", "AISTATS", "UAI", "CORL"]:
        assert passes_impact_policy({"venue": venue, "year": 2020, "citation_count": 260}) is True


def test_openalex_record_reconstructs_abstract_and_pdf_url() -> None:
    work = {
        "id": "https://openalex.org/W123",
        "title": "A Useful Method",
        "publication_year": 2020,
        "cited_by_count": 1234,
        "abstract_inverted_index": {"This": [0], "works": [1], "well": [2]},
        "ids": {"doi": "https://doi.org/10.48550/arxiv.2001.00001"},
        "primary_location": {
            "landing_page_url": "https://proceedings.mlr.press/v119/example/example.pdf",
            "pdf_url": None,
        },
        "best_oa_location": None,
        "authorships": [
            {"author": {"display_name": "Ada Lovelace"}},
            {"author": {"display_name": "Grace Hopper"}},
        ],
    }

    record = openalex_work_to_record(work, venue="ICML", tier="primary")

    assert record["paper_id"] == "openalex::W123"
    assert record["abstract"] == "This works well"
    assert record["pdf_url"] == "https://proceedings.mlr.press/v119/example/example.pdf"
    assert record["citation_count"] == 1234
    assert record["authors"] == "Ada Lovelace; Grace Hopper"


def test_title_match_score_accepts_close_matches() -> None:
    assert title_match_score("Denoising Diffusion Probabilistic Models", "Denoising Diffusion Probabilistic Models") == 1.0
    assert title_match_score("FixMatch: Simplifying Semi-Supervised Learning", "FixMatch Simplifying Semi Supervised Learning") > 0.9
    assert title_match_score("Denoising Diffusion Probabilistic Models", "A Survey of Diffusion") < 0.7


def test_seed_to_record_preserves_known_github_and_openalex_metadata() -> None:
    seed = {
        "title": "Denoising Diffusion Probabilistic Models",
        "venue": "NEURIPS",
        "year": 2020,
        "code_url": "https://github.com/hojonathanho/diffusion",
    }
    work = {
        "id": "https://openalex.org/W1",
        "title": "Denoising Diffusion Probabilistic Models",
        "publication_year": 2020,
        "cited_by_count": 5000,
        "abstract_inverted_index": {"Diffusion": [0], "models": [1]},
        "ids": {"doi": "https://doi.org/10.48550/arxiv.2006.11239"},
        "primary_location": {"landing_page_url": "https://arxiv.org/abs/2006.11239", "pdf_url": "https://arxiv.org/pdf/2006.11239"},
        "best_oa_location": None,
        "authorships": [],
    }

    record = seed_to_record(seed, work=work)

    assert record["paper_id"] == "openalex::W1"
    assert record["citation_count"] == 5000
    assert record["pdf_url"] == "https://arxiv.org/pdf/2006.11239"
    assert record["code_url"] == "https://github.com/hojonathanho/diffusion"
    assert passes_code_seed_policy(record, min_citations=250) is True


def test_code_seed_policy_requires_github_pdf_and_impact() -> None:
    valid = {
        "title": "A High Impact Code Paper",
        "pdf_url": "https://example.org/paper.pdf",
        "code_url": "https://github.com/example/project",
        "citation_count": 300,
    }

    assert passes_code_seed_policy(valid, min_citations=250) is True
    assert passes_code_seed_policy(valid | {"citation_count": 249}, min_citations=250) is False
    assert passes_code_seed_policy(valid | {"code_url": "https://gitlab.com/example/project"}, min_citations=250) is False
    assert passes_code_seed_policy(valid | {"pdf_url": ""}, min_citations=250) is False


def test_fetch_seed_records_falls_back_to_seed_metadata_when_openalex_fails(monkeypatch, tmp_path) -> None:
    seed_file = tmp_path / "seeds.jsonl"
    seed_file.write_text(
        '{"title":"A High Impact Code Paper","venue":"ICLR","year":2022,'
        '"pdf_url":"https://example.org/paper.pdf","code_url":"https://github.com/example/project",'
        '"cited_by_count":1200}\n',
        encoding="utf-8",
    )
    log_path = tmp_path / "events.jsonl"

    def fail_openalex(title: str, timeout: int):
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr("idea_scout.high_impact_harvest.fetch_openalex_title_work", fail_openalex)

    records = fetch_seed_records(str(seed_file), timeout=1, min_citations=250, log_path=log_path)

    assert len(records) == 1
    assert records[0]["title"] == "A High Impact Code Paper"
    assert records[0]["source"] == "seed"
    assert records[0]["pdf_url"] == "https://example.org/paper.pdf"
    assert records[0]["code_url"] == "https://github.com/example/project"
    assert "seed_record_openalex_failed" in log_path.read_text(encoding="utf-8")


def test_download_pdf_enforces_total_timeout(monkeypatch, tmp_path) -> None:
    class SlowResponse:
        status = 200

        def __init__(self) -> None:
            self.remaining = 4

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, size: int) -> bytes:
            if self.remaining <= 0:
                return b""
            self.remaining -= 1
            return b"x" * 1024

    ticks = iter([0.0, 0.4, 0.9, 1.4, 1.9])

    monkeypatch.setattr("idea_scout.high_impact_harvest.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("idea_scout.high_impact_harvest.urllib.request.urlopen", lambda *_args, **_kwargs: SlowResponse())

    pdf_path = tmp_path / "slow.pdf"
    err = download_pdf("https://example.org/slow.pdf", pdf_path, timeout=1)

    assert err == "download_timeout"
    assert not pdf_path.exists()


def test_cvf_parser_extracts_title_authors_and_pdf() -> None:
    html = """
    <dt class="ptitle"><br><a href="/content/CVPR2024/html/X_A_Method_CVPR_2024_paper.html">A Method for Vision</a></dt>
    <dd>Ann Author, Bob Builder</dd>
    <dd>[<a href="/content/CVPR2024/papers/X_A_Method_CVPR_2024_paper.pdf">pdf</a>]</dd>
    """

    records = parse_cvf_openaccess(html, venue="CVPR", year=2024, page_url="https://openaccess.thecvf.com/CVPR2024?day=all")

    assert records == [
        {
            "paper_id": "cvf::cvpr::2024::a-method-for-vision",
            "title": "A Method for Vision",
            "venue": "CVPR",
            "year": 2024,
            "authors": "Ann Author; Bob Builder",
            "url": "https://openaccess.thecvf.com/content/CVPR2024/html/X_A_Method_CVPR_2024_paper.html",
            "pdf_url": "https://openaccess.thecvf.com/content/CVPR2024/papers/X_A_Method_CVPR_2024_paper.pdf",
            "source": "cvf_openaccess",
            "impact_tier": "primary_unranked",
        }
    ]


def test_cvf_parser_finds_older_day_pages() -> None:
    html = """
    <dd>[<a href="CVPR2018.py?day=2018-06-19">Day 1: 2018-06-19</a>]</dd>
    <dd>[<a href="CVPR2018.py?day=2018-06-20">Day 2: 2018-06-20</a>]</dd>
    """

    assert parse_cvf_day_links(html, "https://openaccess.thecvf.com/CVPR2018.py") == [
        "https://openaccess.thecvf.com/CVPR2018.py?day=2018-06-19",
        "https://openaccess.thecvf.com/CVPR2018.py?day=2018-06-20",
    ]


def test_acl_parser_only_keeps_main_or_long_volume_papers() -> None:
    html = """
    <div id=2024acl-long>
      <h4><a href=/volumes/2024.acl-long/>Proceedings Long Papers</a></h4>
      <div class="d-sm-flex align-items-stretch mb-3">
        <a class="badge text-bg-primary" href=https://aclanthology.org/2024.acl-long.1.pdf>pdf</a>
        <strong><a class=align-middle href=/2024.acl-long.1/>A Strong NLP Method</a></strong><br>
        <a href=/people/a/ann-author/>Ann Author</a>
        <a href=/people/b/bob-builder/>Bob Builder</a>
      </div>
    </div>
    <div id=2024findings-acl>
      <h4><a href=/volumes/2024.findings-acl/>Findings</a></h4>
      <div class="d-sm-flex align-items-stretch mb-3">
        <a class="badge text-bg-primary" href=https://aclanthology.org/2024.findings-acl.1.pdf>pdf</a>
        <strong><a class=align-middle href=/2024.findings-acl.1/>A Findings Paper</a></strong><br>
        <a href=/people/c/casey/>Casey</a>
      </div>
    </div>
    """

    records = parse_acl_anthology_event(html, venue="ACL", year=2024)

    assert records == [
        {
            "paper_id": "acl-anthology::acl::2024::2024.acl-long.1",
            "title": "A Strong NLP Method",
            "venue": "ACL",
            "year": 2024,
            "authors": "Ann Author; Bob Builder",
            "url": "https://aclanthology.org/2024.acl-long.1/",
            "pdf_url": "https://aclanthology.org/2024.acl-long.1.pdf",
            "source": "acl_anthology",
            "impact_tier": "primary_unranked",
            "acl_volume_id": "2024acl-long",
            "acl_track": "main_long",
        }
    ]


def test_acl_parser_keeps_older_p16_main_volume() -> None:
    html = """
    <div id=p16-1>
      <h4><a href=/volumes/P16-1/>Long Papers</a></h4>
      <div class="d-sm-flex align-items-stretch mb-3">
        <a class="badge text-bg-primary" href=https://aclanthology.org/P16-1001.pdf>pdf</a>
        <strong><a class=align-middle href=/P16-1001/>Neural Machine Translation Method</a></strong><br>
        <a href=/people/a/ann-author/>Ann Author</a>
      </div>
      <div class="d-sm-flex align-items-stretch mb-3">
        <a class="badge text-bg-primary" href=https://aclanthology.org/P16-1000.pdf>pdf</a>
        <strong><a class=align-middle href=/P16-1000/>Front Matter</a></strong><br>
      </div>
    </div>
    """

    records = parse_acl_anthology_event(html, venue="ACL", year=2016)

    assert records == [
        {
            "paper_id": "acl-anthology::acl::2016::P16-1001",
            "title": "Neural Machine Translation Method",
            "venue": "ACL",
            "year": 2016,
            "authors": "Ann Author",
            "url": "https://aclanthology.org/P16-1001/",
            "pdf_url": "https://aclanthology.org/P16-1001.pdf",
            "source": "acl_anthology",
            "impact_tier": "primary_unranked",
            "acl_volume_id": "p16-1",
            "acl_track": "main_long",
        }
    ]


def test_acl_family_volume_gate_keeps_only_main_or_long() -> None:
    assert acl_allowed_volume_id("2024.acl-long", "ACL") is True
    assert acl_allowed_volume_id("2024.acl-main", "ACL") is True
    assert acl_allowed_volume_id("2024.acl-short", "ACL") is False
    assert acl_allowed_volume_id("2024.findings-acl", "ACL") is False

    assert acl_allowed_volume_id("2024.emnlp-main", "EMNLP") is True
    assert acl_allowed_volume_id("2024.findings-emnlp", "EMNLP") is False
    assert acl_allowed_volume_id("D16-1", "EMNLP") is True
    assert acl_allowed_volume_id("D16-2", "EMNLP") is False

    assert acl_allowed_volume_id("2024.naacl-main", "NAACL") is True
    assert acl_allowed_volume_id("2024.naacl-long", "NAACL") is True
    assert acl_allowed_volume_id("2024.naacl-short", "NAACL") is False
    assert acl_allowed_volume_id("N16-1", "NAACL") is True
    assert acl_allowed_volume_id("N16-2", "NAACL") is False


def test_acl_code_evidence_requires_llm_reviewed_implementation() -> None:
    base = {
        "code": {
            "url": "https://github.com/example/project",
            "status": "repo_found",
        }
    }

    assert acl_code_evidence_ok(base | {"llm_review": {"code_assessment": "official"}}) is True
    assert acl_code_evidence_ok(base | {"llm_review": {"code_assessment": "community"}}) is True
    assert acl_code_evidence_ok(base | {"llm_review": {"code_assessment": "unknown"}}) is False
    assert acl_code_evidence_ok(base | {"llm_review": {"code_assessment": "missing"}}) is False
    assert acl_code_evidence_ok(base) is False


def test_selected_sources_normalizes_source_groups_and_venues() -> None:
    assert selected_sources("ml,acl,cvf") == {"NEURIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "CVPR", "ICCV"}
    assert selected_sources("acl,iclr") == {"ACL", "EMNLP", "NAACL", "ICLR"}
    assert selected_sources("ml-extra") == {"JMLR", "AISTATS", "UAI", "CORL"}


def test_openai_chat_completions_url_normalizes_base_url() -> None:
    assert openai_chat_completions_url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions"
    assert openai_chat_completions_url("https://proxy.example/v1") == "https://proxy.example/v1/chat/completions"


def test_review_metadata_and_cache_are_model_specific(tmp_path) -> None:
    asset = {"asset_id": "a1", "llm_review": {"verdict": "accept", "asset_quality": 5}}

    annotated = annotate_review_metadata(asset, provider="openai", reviewer_model="gpt-5.5")

    assert annotated["llm_review"]["review_provider"] == "openai"
    assert annotated["llm_review"]["review_model"] == "gpt-5.5"
    assert provider_cache_dir(tmp_path, "openai", "gpt-5.5") != provider_cache_dir(tmp_path, "command", "claude -p")


def test_codex_review_provider_builds_runner() -> None:
    runner = build_runner("codex", "gpt-5.5", "claude -p")

    assert callable(runner)


def test_existing_store_asset_paths_skip_all_batches(tmp_path) -> None:
    store = tmp_path / "store"
    for batch, title in [
        ("bestpaper", "Existing Best Paper"),
        ("high_impact_ml", "Existing ML Paper"),
        ("new_batch", "Existing New Batch Paper"),
    ]:
        batch_dir = store / batch
        batch_dir.mkdir(parents=True)
        (batch_dir / "assets.jsonl").write_text(
            '{"source_papers":[{"title":"%s"}]}\n' % title,
            encoding="utf-8",
        )

    paths = existing_store_asset_paths(store)
    skip_titles = existing_title_keys(paths)
    records = [
        {"paper_id": "new::1", "title": "Existing ML Paper"},
        {"paper_id": "new::2", "title": "Fresh Code Paper"},
    ]

    assert {p.parent.name for p in paths} == {"bestpaper", "high_impact_ml", "new_batch"}
    assert [r["title"] for r in dedupe_records(records, skip_titles)] == ["Fresh Code Paper"]
