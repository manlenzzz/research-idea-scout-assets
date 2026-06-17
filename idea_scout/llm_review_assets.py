from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from .assets import compute_asset_score, read_assets, utc_now, write_assets
from .io_utils import clean_text, write_jsonl


Runner = Callable[[str, int], str]

VALID_VERDICTS = {"accept", "weak", "reject"}
VALID_CODE_ASSESSMENTS = {"official", "community", "missing", "unknown"}


def clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        x = float(value)
    except Exception:
        return default
    return min(high, max(low, x))


def as_str_list(value: Any, max_items: int = 6, max_chars: int = 240) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = clean_text(item, max_chars)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def source_paper(asset: Dict[str, Any]) -> Dict[str, Any]:
    papers = asset.get("source_papers") if isinstance(asset.get("source_papers"), list) else []
    first = papers[0] if papers and isinstance(papers[0], dict) else {}
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
    return {
        "title": clean_text(first.get("title") or raw.get("title"), 500),
        "abstract": clean_text(first.get("abstract") or raw.get("abstract"), 3000),
        "venue": clean_text(first.get("venue") or raw.get("venue")),
        "year": first.get("year") or raw.get("year") or "",
        "url": clean_text(first.get("url") or raw.get("url") or raw.get("pdf_url"), 500),
    }


def pdf_sections(asset: Dict[str, Any]) -> Dict[str, str]:
    pdf = asset.get("pdf") if isinstance(asset.get("pdf"), dict) else {}
    sections = pdf.get("extracted_sections") if isinstance(pdf.get("extracted_sections"), dict) else {}
    insight = asset.get("insight") if isinstance(asset.get("insight"), dict) else {}
    return {
        "method": clean_text(sections.get("method") or insight.get("method_section_excerpt"), 2400),
        "experiments": clean_text(sections.get("experiments"), 1000),
        "limitations": clean_text(sections.get("limitations"), 1000),
        "introduction": clean_text(insight.get("introduction_excerpt"), 1400),
    }


def evidence_package(asset: Dict[str, Any]) -> Dict[str, Any]:
    code = asset.get("code") if isinstance(asset.get("code"), dict) else {}
    pdf = asset.get("pdf") if isinstance(asset.get("pdf"), dict) else {}
    insight = asset.get("insight") if isinstance(asset.get("insight"), dict) else {}
    return {
        "asset_id": asset.get("asset_id", ""),
        "paper": source_paper(asset),
        "current_card": {
            "challenge": clean_text(asset.get("challenge"), 700),
            "method": clean_text(asset.get("solution_pattern") or asset.get("mechanism"), 700),
            "reusable_insight": clean_text(insight.get("reusable_insight"), 700),
            "why_it_is_hard": clean_text(asset.get("why_it_is_hard"), 500),
        },
        "code": {
            "status": clean_text(code.get("status")),
            "url": clean_text(code.get("url"), 500),
            "discovery_source": clean_text(code.get("discovery_source")),
            "discovery_confidence": clean_text(code.get("discovery_confidence")),
            "homepage_url": clean_text(code.get("homepage_url"), 500),
        },
        "pdf": {
            "status": clean_text(pdf.get("status")),
            "sections": pdf_sections(asset),
        },
    }


def build_prompt(asset: Dict[str, Any]) -> str:
    package = evidence_package(asset)
    return (
        "你是一个严格的研究资产审核员。请只基于给定证据判断这个 paper 是否能沉淀成"
        "可复用的 research asset。\n"
        "\n"
        "任务：抽取更强的 challenge -> method -> reusable insight，并审核代码证据。\n"
        "规则：\n"
        "1. 不要编造。证据不足就给 weak 或 reject。\n"
        "2. challenge 必须是具体问题/瓶颈，不要写作者、年份、论文标题堆砌。\n"
        "3. method 必须是论文实际方法机制，不要只写 generic 'use the method'.\n"
        "4. reusable_insight 要写成跨任务可迁移的模式，最好是“当...时，可以...”。\n"
        "5. why_it_works 说明机制为什么能解决 challenge。\n"
        "6. 有开源代码才可认为实现证据较强；GitHub 搜索找到的非官方仓库标 community。\n"
        "7. evidence_quotes 只能放短证据片段，每条不超过 18 个英文词或 30 个中文字符。\n"
        "\n"
        "只能输出一个 JSON 对象，不要 markdown，不要解释。schema：\n"
        "{\n"
        '  "verdict": "accept|weak|reject",\n'
        '  "asset_quality": 1-5,\n'
        '  "challenge": "中文，具体 challenge",\n'
        '  "method": "中文，具体方法机制",\n'
        '  "reusable_insight": "中文，可迁移 insight",\n'
        '  "why_it_works": "中文，机制解释",\n'
        '  "transfer_targets": ["中文短语"],\n'
        '  "non_transferable_parts": ["中文短语"],\n'
        '  "evidence_quotes": ["短证据片段"],\n'
        '  "code_assessment": "official|community|missing|unknown",\n'
        '  "review_notes": "中文，指出是否值得作为资产",\n'
        '  "confidence": 0.0-1.0\n'
        "}\n"
        "\n"
        "证据包：\n"
        f"{json.dumps(package, ensure_ascii=False, indent=2)}"
    )


def extract_json_object(text: str) -> str:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S | re.I)
    if fence:
        return fence.group(1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def parse_review_json(text: str) -> Dict[str, Any]:
    obj = json.loads(extract_json_object(text))
    if not isinstance(obj, dict):
        raise ValueError("LLM review is not a JSON object")
    return obj


def sanitize_review(obj: Dict[str, Any]) -> Dict[str, Any]:
    verdict = clean_text(obj.get("verdict")).lower()
    if verdict not in VALID_VERDICTS:
        verdict = "weak"
    code_assessment = clean_text(obj.get("code_assessment")).lower()
    if code_assessment not in VALID_CODE_ASSESSMENTS:
        code_assessment = "unknown"
    return {
        "verdict": verdict,
        "asset_quality": int(clamp(obj.get("asset_quality"), 1, 5, 2)),
        "challenge": clean_text(obj.get("challenge"), 700),
        "method": clean_text(obj.get("method"), 700),
        "reusable_insight": clean_text(obj.get("reusable_insight"), 900),
        "why_it_works": clean_text(obj.get("why_it_works"), 900),
        "transfer_targets": as_str_list(obj.get("transfer_targets")),
        "non_transferable_parts": as_str_list(obj.get("non_transferable_parts")),
        "evidence_quotes": as_str_list(obj.get("evidence_quotes"), max_items=5, max_chars=160),
        "code_assessment": code_assessment,
        "review_notes": clean_text(obj.get("review_notes"), 900),
        "confidence": round(clamp(obj.get("confidence"), 0.0, 1.0, 0.0), 3),
    }


def default_claude_runner(command: str) -> Runner:
    argv = shlex.split(command)

    def run(prompt: str, timeout: int) -> str:
        completed = subprocess.run(
            [*argv, prompt],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout

    return run


def openai_chat_completions_url(base_url: str) -> str:
    base = clean_text(base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def default_openai_runner(model: str = "", base_url: str = "") -> Runner:
    model_name = clean_text(model or os.environ.get("PAPERHUB_AGENT_MODEL") or "gpt-5.5")
    endpoint = openai_chat_completions_url(base_url)

    def run(prompt: str, timeout: int) -> str:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a strict research asset reviewer. Return only one valid JSON object.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "research-idea-scout-assets",
            },
            method="POST",
        )
        raw = ""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="ignore")[-1000:]
                if e.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"OpenAI-compatible review call failed with HTTP {e.code}: {detail}") from e
        data = json.loads(raw)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise RuntimeError("OpenAI-compatible review response has no choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = clean_text(message.get("content") or first.get("text"), 20000)
        if not content:
            raise RuntimeError("OpenAI-compatible review response has empty content")
        return content

    return run


def default_codex_runner(model: str = "", extra_args: str = "") -> Runner:
    model_name = clean_text(model or os.environ.get("PAPERHUB_AGENT_MODEL") or "gpt-5.5")
    extra = shlex.split(extra_args or os.environ.get("IDEASCOUT_CODEX_EXTRA_ARGS", ""))

    def run(prompt: str, timeout: int) -> str:
        tmp_path = Path(tempfile.mkstemp(prefix="ideascout-codex-review-", suffix=".txt")[1])
        cmd = [
            "codex",
            "exec",
            "-m",
            model_name,
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "--output-last-message",
            str(tmp_path),
            *extra,
            prompt,
        ]
        try:
            completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
            final = tmp_path.read_text(encoding="utf-8") if tmp_path.exists() else ""
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "")[-2000:]
                raise RuntimeError(f"codex review failed with exit {completed.returncode}: {detail}")
            final = clean_text(final, 20000)
            if not final:
                raise RuntimeError("codex review produced an empty final message")
            return final
        finally:
            tmp_path.unlink(missing_ok=True)

    return run


def build_runner(provider: str, model: str, model_command: str, openai_base_url: str = "") -> Runner:
    if provider == "openai":
        return default_openai_runner(model=model, base_url=openai_base_url)
    if provider == "codex":
        return default_codex_runner(model=model)
    if provider == "command":
        return default_claude_runner(model_command)
    raise ValueError(f"unsupported review provider: {provider}")


def reviewer_identity(provider: str, model: str, model_command: str) -> str:
    if provider in {"codex", "openai"}:
        return clean_text(model or os.environ.get("PAPERHUB_AGENT_MODEL") or "gpt-5.5")
    return clean_text(model_command)


def cache_component(value: str) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:96] or "default"


def provider_cache_dir(base_dir: str | Path, provider: str, reviewer_model: str) -> Path:
    return Path(base_dir) / cache_component(provider) / cache_component(reviewer_model)


def annotate_review_metadata(asset: Dict[str, Any], provider: str, reviewer_model: str) -> Dict[str, Any]:
    review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else None
    if not review:
        return asset
    out = dict(asset)
    out["llm_review"] = {
        **review,
        "review_provider": clean_text(provider),
        "review_model": clean_text(reviewer_model),
    }
    return out


def review_cache_key(asset: Dict[str, Any]) -> str:
    payload = json.dumps(evidence_package(asset), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{asset.get('asset_id') or 'asset'}-{digest}"


def load_cached_review(cache_dir: Path, key: str) -> Dict[str, Any] | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        cached = json.load(f)
    review = cached.get("review") if isinstance(cached, dict) else None
    return review if isinstance(review, dict) else None


def write_cached_review(cache_dir: Path, key: str, prompt: str, raw_response: str, review: Dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    path.write_text(
        json.dumps(
            {
                "reviewed_at": utc_now(),
                "prompt_sha1": hashlib.sha1(prompt.encode("utf-8")).hexdigest(),
                "raw_response": raw_response,
                "review": review,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def apply_review(asset: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(asset)
    llm_review = {
        **review,
        "reviewed_at": utc_now(),
        "reviewer": "llm",
    }
    out["llm_review"] = llm_review

    verdict = review["verdict"]
    quality = int(review["asset_quality"])
    accepted_enough = verdict in {"accept", "weak"} and quality >= 3

    if accepted_enough and review["challenge"] and review["method"]:
        out["challenge"] = review["challenge"]
        out["solution_pattern"] = review["method"]
        out["mechanism"] = review["method"]
        if review["why_it_works"]:
            out["why_it_is_hard"] = review["why_it_works"]
        if review["transfer_targets"]:
            out["transferable_to"] = review["transfer_targets"]
        if review["non_transferable_parts"]:
            out["non_transferable_parts"] = review["non_transferable_parts"]
        insight = out.get("insight") if isinstance(out.get("insight"), dict) else {}
        insight = dict(insight)
        insight.update(
            {
                "challenge": review["challenge"],
                "method": review["method"],
                "reusable_insight": review["reusable_insight"],
                "why_it_works": review["why_it_works"],
                "extraction_status": "llm_reviewed",
            }
        )
        out["insight"] = insight
        evidence = list(out.get("evidence") or [])
        if review["reusable_insight"]:
            evidence.insert(0, f"LLM reusable insight: {review['reusable_insight']}")
        if review["evidence_quotes"]:
            evidence.insert(1, "LLM evidence: " + " | ".join(review["evidence_quotes"]))
        out["evidence"] = evidence[:12]
    elif verdict == "reject":
        limitations = list(out.get("limitations") or [])
        limitations.insert(0, "LLM rejected asset: evidence is insufficient for a reusable challenge-method insight.")
        out["limitations"] = limitations[:12]

    scores = out.get("scores") if isinstance(out.get("scores"), dict) else {}
    out["scores"] = dict(scores)

    # The LLM verdict is authoritative. Map its judgement onto every scoring
    # dimension so a high-quality "accept" is not dragged down by stale
    # pipeline defaults (transferability / code_readiness used to be untouched
    # by the review, which made strong assets score as low as weak ones).
    code_assessment = review["code_assessment"]
    code_readiness = {"official": 9.0, "community": 6.0, "unknown": 2.0, "missing": 0.0}.get(
        code_assessment, 2.0
    )
    # asset_quality is the reviewer's 1-5 confidence that this is a reusable
    # asset; scale it to 0-10 and lift it when concrete transfer targets exist.
    transferability = float(quality) * 2.0
    if review["transfer_targets"]:
        transferability = min(10.0, transferability + 1.0)

    if verdict == "accept":
        out["scores"]["evidence_strength"] = max(float(out["scores"].get("evidence_strength", 0) or 0), 8.0)
        out["scores"]["implementation_feasibility"] = max(
            float(out["scores"].get("implementation_feasibility", 0) or 0),
            6.0 if code_assessment in {"official", "community"} else 3.0,
        )
        out["scores"]["transferability"] = max(transferability, 6.0)
        out["scores"]["code_readiness"] = code_readiness
    elif verdict == "weak":
        out["scores"]["evidence_strength"] = max(float(out["scores"].get("evidence_strength", 0) or 0), 5.0)
        out["scores"]["transferability"] = transferability
        out["scores"]["code_readiness"] = code_readiness
    else:
        out["scores"]["evidence_strength"] = min(float(out["scores"].get("evidence_strength", 0) or 0), 3.0)
        out["scores"]["implementation_feasibility"] = min(
            float(out["scores"].get("implementation_feasibility", 0) or 0),
            2.0,
        )
        out["scores"]["transferability"] = min(transferability, 3.0)
        out["scores"]["code_readiness"] = min(code_readiness, 2.0)

    out["scores"]["asset_score"] = compute_asset_score(out)
    out["updated_at"] = utc_now()
    return out


def review_one(
    asset: Dict[str, Any],
    runner: Runner,
    timeout: int = 120,
    cache_dir: str | Path | None = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    prompt = build_prompt(asset)
    cache_path = Path(cache_dir) if cache_dir else None
    key = review_cache_key(asset)
    if cache_path and use_cache:
        cached = load_cached_review(cache_path, key)
        if cached:
            return apply_review(asset, sanitize_review(cached))

    raw_response = runner(prompt, timeout)
    review = sanitize_review(parse_review_json(raw_response))
    if cache_path:
        write_cached_review(cache_path, key, prompt, raw_response, review)
    return apply_review(asset, review)


def should_review(asset: Dict[str, Any], only_code_status: str = "", skip_existing: bool = True) -> bool:
    if skip_existing and isinstance(asset.get("llm_review"), dict):
        return False
    if only_code_status:
        code = asset.get("code") if isinstance(asset.get("code"), dict) else {}
        if clean_text(code.get("status")) != only_code_status:
            return False
    return True


def mark_failed(asset: Dict[str, Any], error: Exception) -> Dict[str, Any]:
    out = dict(asset)
    out["llm_review"] = {
        "verdict": "failed",
        "reviewed_at": utc_now(),
        "reviewer": "llm",
        "failure_reason": clean_text(f"{type(error).__name__}: {error}", 500),
    }
    out["updated_at"] = utc_now()
    return out


def review_assets(
    assets: Iterable[Dict[str, Any]],
    runner: Runner,
    limit: int = 0,
    only_code_status: str = "",
    timeout: int = 120,
    cache_dir: str | Path | None = None,
    skip_existing: bool = True,
    fail_open: bool = True,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    out = []
    stats: Dict[str, int] = {"reviewed": 0, "skipped": 0, "failed": 0}
    for asset in assets:
        if limit and stats["reviewed"] >= limit:
            out.append(asset)
            stats["skipped"] += 1
            continue
        if not should_review(asset, only_code_status=only_code_status, skip_existing=skip_existing):
            out.append(asset)
            stats["skipped"] += 1
            continue
        try:
            reviewed = review_one(asset, runner=runner, timeout=timeout, cache_dir=cache_dir)
            out.append(reviewed)
            stats["reviewed"] += 1
        except Exception as e:
            if not fail_open:
                raise
            out.append(mark_failed(asset, e))
            stats["failed"] += 1
    return out, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Use an LLM to audit asset challenge -> method -> reusable insight cards.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-code-status", default="")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--cache-dir", default="data/llm_review_cache")
    ap.add_argument("--review-provider", choices=["codex", "openai", "command"], default="codex")
    ap.add_argument("--review-model", default=os.environ.get("PAPERHUB_AGENT_MODEL", "gpt-5.5"))
    ap.add_argument("--openai-base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    ap.add_argument("--model-command", default="claude -p", help="Only used with --review-provider command.")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--retry-existing", action="store_true")
    ap.add_argument("--prompt-preview", default="", help="Write prompts as JSONL instead of calling the model.")
    args = ap.parse_args()

    assets = read_assets(args.input)
    if args.prompt_preview:
        rows = [
            {"asset_id": asset.get("asset_id"), "prompt": build_prompt(asset)}
            for asset in assets
            if should_review(asset, args.only_code_status, skip_existing=not args.retry_existing)
        ]
        if args.limit:
            rows = rows[: args.limit]
        write_jsonl(args.prompt_preview, rows)
        print(json.dumps({"prompt_preview": args.prompt_preview, "rows": len(rows)}, indent=2))
        return

    reviewer_model = reviewer_identity(args.review_provider, args.review_model, args.model_command)
    runner = build_runner(args.review_provider, args.review_model, args.model_command, args.openai_base_url)
    reviewed, stats = review_assets(
        assets,
        runner=runner,
        limit=args.limit,
        only_code_status=args.only_code_status,
        timeout=args.timeout,
        cache_dir=None if args.no_cache else provider_cache_dir(args.cache_dir, args.review_provider, reviewer_model),
        skip_existing=not args.retry_existing,
    )
    reviewed = [annotate_review_metadata(asset, args.review_provider, reviewer_model) for asset in reviewed]
    write_assets(args.output, reviewed)
    verdicts: Dict[str, int] = {}
    for asset in reviewed:
        review = asset.get("llm_review") if isinstance(asset.get("llm_review"), dict) else {}
        verdict = clean_text(review.get("verdict")) or "not_reviewed"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
    print(json.dumps({"input": args.input, "output": args.output, **stats, "verdicts": verdicts}, indent=2))


if __name__ == "__main__":
    main()
