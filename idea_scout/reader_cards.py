from __future__ import annotations

from typing import Any, Dict, List

from .io_utils import clean_text


READER_CARD_FIELDS = [
    "short_title",
    "intuition",
    "why_old_way_fails",
    "mechanism_steps",
    "key_terms",
    "transfer_rule",
    "misuse_warning",
    "technical_summary",
]


def _review(asset: Dict[str, Any]) -> Dict[str, Any]:
    review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else {}
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    raw_review = raw.get("llm_review") if isinstance(raw.get("llm_review"), dict) else {}
    return review or raw_review or {}


def _source(asset: Dict[str, Any]) -> Dict[str, Any]:
    papers = asset.get("source_papers") if isinstance(asset.get("source_papers"), list) else []
    first = papers[0] if papers and isinstance(papers[0], dict) else {}
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    return {
        "title": clean_text(first.get("title") or raw.get("title"), 240),
        "abstract": clean_text(first.get("abstract") or raw.get("abstract"), 900),
        "venue": clean_text(first.get("venue") or raw.get("venue")),
        "year": first.get("year") or raw.get("year") or "",
    }


def _list_text(value: Any, max_items: int = 4, max_chars: int = 120) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = clean_text(item, max_chars)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _term(term: str, plain: str) -> Dict[str, str]:
    return {"term": term, "plain": plain}


def infer_short_title(asset: Dict[str, Any]) -> str:
    source = _source(asset)
    text = " ".join(
        [
            source["title"],
            clean_text(asset.get("challenge")),
            clean_text(asset.get("solution_pattern")),
            clean_text(_review(asset).get("reusable_insight")),
        ]
    ).lower()
    if "inverse autoregressive flow" in text or "iaf" in text:
        return "IAF: 让 VAE 后验更灵活但仍可算密度"
    if "dense" in text and "connect" in text:
        return "DenseNet: 用密集连接复用特征"
    if "taskonomy" in text:
        return "Taskonomy: 先学任务关系再迁移"
    if source["title"]:
        return source["title"][:80]
    return clean_text(_review(asset).get("reusable_insight") or asset.get("challenge"), 80) or "Reader card"


def infer_key_terms(asset: Dict[str, Any]) -> List[Dict[str, str]]:
    solution = clean_text(asset.get("solution_pattern"))
    challenge = clean_text(asset.get("challenge"))
    why_hard = clean_text(asset.get("why_it_is_hard"))
    insight = clean_text(_review(asset).get("reusable_insight"))
    why_works = clean_text(_review(asset).get("why_it_works"))
    blob = " ".join(
        [
            challenge,
            solution,
            why_hard,
            insight,
            why_works,
        ]
    ).lower()
    terms: List[Dict[str, str]] = []
    if "dense" in blob and "connect" in blob:
        terms.append(_term("dense connectivity", "让每层直接接收所有前层特征，缩短梯度路径并复用已有表示。"))
    if "growth rate" in blob or "growth" in blob or "增长率" in blob:
        terms.append(_term("growth rate", "控制每层新增通道数，用较小增量换取更深的特征复用。"))
    if "feature" in blob and ("reuse" in blob or "复用" in blob):
        terms.append(_term("feature reuse", "后层直接使用前层已经学到的特征，减少重复学习。"))
    if "jacobian" in blob:
        terms.append(_term("triangular Jacobian", "让 log determinant 变成对角项求和，避免高维矩阵行列式。"))
    if "autoregressive" in blob or "自回归" in blob:
        terms.append(_term("autoregressive network", "按有序依赖生成每一维的变换参数，用来表达维度间相关性。"))
    if "log-density" in blob or "density" in blob or "密度" in blob:
        terms.append(_term("log-density", "变换后某个潜变量取值的概率密度对数，很多生成模型训练目标需要它。"))
    if "token" in blob:
        terms.append(_term("token matching", "把局部块或元素当作可检索单元，在共享空间里寻找对应关系。"))
    graph_signal = any(x in blob for x in ["graph", "knowledge graph", "图结构", "关系图", "图神经", "graph neural"])
    feature_map_only = any(x in blob for x in ["特征图", "feature-map", "feature map"])
    if graph_signal and not feature_map_only:
        terms.append(_term("structure graph", "把任务、样本或变量之间的关系显式建成图，便于复用关系而不是只复用参数。"))
    unique: List[Dict[str, str]] = []
    seen = set()
    for item in terms:
        if item["term"] in seen:
            continue
        unique.append(item)
        seen.add(item["term"])
    return unique[:5]


def make_fallback_reader_card(asset: Dict[str, Any]) -> Dict[str, Any]:
    review = _review(asset)
    source = _source(asset)
    challenge = clean_text(review.get("challenge") or asset.get("challenge"), 700)
    method = clean_text(review.get("method") or asset.get("solution_pattern") or asset.get("mechanism"), 900)
    insight = clean_text(review.get("reusable_insight"), 900)
    why = clean_text(review.get("why_it_works") or asset.get("why_it_is_hard"), 900)
    transfers = _list_text(review.get("transfer_targets"), max_items=5)
    for item in _list_text(asset.get("transferable_to"), max_items=5):
        if item not in transfers:
            transfers.append(item)
    transfers = transfers[:5]
    boundaries = _list_text(review.get("non_transferable_parts"), max_items=5)
    for item in _list_text(asset.get("non_transferable_parts"), max_items=5):
        if item not in boundaries:
            boundaries.append(item)
    boundaries = boundaries[:5]

    if insight:
        intuition = f"{insight} 具体来看，瓶颈是：{challenge}" if challenge else insight
    elif challenge and method:
        intuition = f"这个资产关注的瓶颈是：{challenge} 核心做法是：{method}"
    else:
        intuition = source["abstract"] or "证据不足，当前只能保留技术摘要。"

    if challenge:
        why_old_way_fails = challenge
    elif why:
        why_old_way_fails = why
    else:
        why_old_way_fails = "当前证据没有清楚说明旧方法失败点，需要回看论文。"

    mechanism_steps = []
    if challenge:
        mechanism_steps.append(f"先定位瓶颈：{challenge}")
    if method:
        mechanism_steps.append(f"再使用论文机制：{method}")
    if why:
        mechanism_steps.append(f"机制成立的原因：{why}")
    if transfers:
        mechanism_steps.append(f"迁移时优先检查：{'；'.join(transfers[:3])}")

    transfer_rule = "；".join(transfers) if transfers else "当目标问题和源论文共享相同瓶颈、表示结构或训练约束时，再考虑迁移。"
    misuse_warning = "；".join(boundaries) if boundaries else "不要直接迁移论文的具体数据集、超参数或 benchmark 数值。"
    technical_summary = " ".join(x for x in [method, why] if x) or insight or source["abstract"]

    return sanitize_reader_card(
        {
            "short_title": infer_short_title(asset),
            "intuition": intuition,
            "why_old_way_fails": why_old_way_fails,
            "mechanism_steps": mechanism_steps,
            "key_terms": infer_key_terms(asset),
            "transfer_rule": transfer_rule,
            "misuse_warning": misuse_warning,
            "technical_summary": technical_summary,
        },
        asset,
    )


def sanitize_reader_card(raw: Dict[str, Any] | None, asset: Dict[str, Any]) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    fallback = {
        "short_title": infer_short_title(asset),
        "intuition": clean_text(asset.get("challenge"), 900),
        "why_old_way_fails": clean_text(asset.get("why_it_is_hard"), 700),
        "mechanism_steps": [],
        "key_terms": [],
        "transfer_rule": "；".join(_list_text(asset.get("transferable_to"), max_items=5)),
        "misuse_warning": "；".join(_list_text(asset.get("non_transferable_parts"), max_items=5)),
        "technical_summary": clean_text(asset.get("solution_pattern") or asset.get("mechanism"), 900),
    }

    steps = _list_text(raw.get("mechanism_steps"), max_items=4, max_chars=260)
    if not steps:
        steps = _list_text(fallback["mechanism_steps"], max_items=4, max_chars=260)

    terms = []
    if isinstance(raw.get("key_terms"), list):
        for item in raw["key_terms"]:
            if not isinstance(item, dict):
                continue
            term = clean_text(item.get("term"), 80)
            plain = clean_text(item.get("plain") or item.get("meaning"), 220)
            if term and plain:
                terms.append({"term": term, "plain": plain})
            if len(terms) >= 6:
                break
    if not terms:
        terms = infer_key_terms(asset)

    out = {
        "short_title": clean_text(raw.get("short_title") or fallback["short_title"], 120),
        "intuition": clean_text(raw.get("intuition") or fallback["intuition"], 1000),
        "why_old_way_fails": clean_text(raw.get("why_old_way_fails") or fallback["why_old_way_fails"], 800),
        "mechanism_steps": steps,
        "key_terms": terms,
        "transfer_rule": clean_text(raw.get("transfer_rule") or fallback["transfer_rule"], 700),
        "misuse_warning": clean_text(raw.get("misuse_warning") or fallback["misuse_warning"], 700),
        "technical_summary": clean_text(raw.get("technical_summary") or fallback["technical_summary"], 1000),
    }
    if not out["intuition"]:
        out["intuition"] = "这个资产还没有足够证据生成直觉说明。"
    if not out["technical_summary"]:
        out["technical_summary"] = out["intuition"]
    return out
