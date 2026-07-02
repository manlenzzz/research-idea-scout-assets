from __future__ import annotations

import json

from idea_scout.reader_cards import infer_key_terms, make_fallback_reader_card, sanitize_reader_card
from idea_scout.enhance_reader_cards import enhance_assets


def sample_asset() -> dict:
    return {
        "asset_id": "iaf-001",
        "challenge": "高维潜变量后验中，对角高斯近似表达力不足。",
        "solution_pattern": "构造 inverse autoregressive flow，用自回归网络输出门控变换。",
        "why_it_is_hard": "三角 Jacobian 使 log determinant 可简单累加。",
        "transferable_to": ["VAE 后验增强", "高维变分推断"],
        "non_transferable_parts": ["MNIST/CIFAR 架构", "MADE 层数"],
        "source_papers": [
            {
                "title": "Improved Variational Inference with Inverse Autoregressive Flow",
                "venue": "NEURIPS",
                "year": 2016,
                "abstract": "IAF scales well to high-dimensional latent spaces.",
            }
        ],
        "llm_review": {
            "reusable_insight": "当需要复杂后验且仍要 tractable density 时，可以用三角 Jacobian 的自回归 flow。",
            "method": "堆叠 inverse autoregressive flow。",
            "why_it_works": "自回归依赖增加相关性，三角 Jacobian 降低密度计算成本。",
            "transfer_targets": ["normalizing flow 近似分布"],
            "non_transferable_parts": ["精确 likelihood 结果"],
        },
    }


def test_fallback_reader_card_preserves_intuition_and_technical_summary() -> None:
    card = make_fallback_reader_card(sample_asset())

    assert card["short_title"].startswith("IAF")
    assert "对角高斯" in card["intuition"]
    assert "三角 Jacobian" in card["technical_summary"]
    assert card["mechanism_steps"]
    assert card["key_terms"]
    assert "VAE 后验增强" in card["transfer_rule"]


def test_sanitize_reader_card_limits_lists_and_fills_required_fields() -> None:
    raw = {
        "short_title": "  IAF reader card  ",
        "intuition": "x" * 1200,
        "mechanism_steps": ["a", "b", "c", "d", "e"],
        "key_terms": [
            {"term": "triangular Jacobian", "plain": "cheap determinant"},
            {"term": "", "plain": "ignored"},
            "not a dict",
        ],
        "technical_summary": "",
    }

    card = sanitize_reader_card(raw, sample_asset())

    assert card["short_title"] == "IAF reader card"
    assert len(card["intuition"]) < 1100
    assert card["mechanism_steps"] == ["a", "b", "c", "d"]
    assert card["key_terms"] == [{"term": "triangular Jacobian", "plain": "cheap determinant"}]
    assert card["technical_summary"]


def test_enhance_assets_adds_reader_card_without_requiring_figures(tmp_path) -> None:
    assets, stats = enhance_assets([sample_asset()], store=tmp_path, batch="demo")

    assert stats == {"enhanced": 1, "figures": 0, "skipped_by_limit": 0}
    assert assets[0]["reader_card"]["short_title"].startswith("IAF")
    assert assets[0]["updated_at"]


def test_infer_key_terms_handles_feature_maps_without_graph_false_positive() -> None:
    asset = {
        "solution_pattern": "Dense connectivity concatenates all preceding feature-maps with a small growth rate.",
        "challenge": "网络加深时前层特征图难以传播到后层。",
        "llm_review": {
            "reusable_insight": "用密集连接复用特征。",
            "why_it_works": "feature reuse shortens gradient paths.",
        },
    }

    terms = infer_key_terms(asset)
    names = [x["term"] for x in terms]

    assert "dense connectivity" in names
    assert "feature reuse" in names
    assert "structure graph" not in names
