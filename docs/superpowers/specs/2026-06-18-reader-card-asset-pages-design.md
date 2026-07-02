# Reader Card Asset Pages Design

## Goal

Make each research asset easier to read by adding a balanced reader-card layer before the existing technical card, and attach one high-value paper figure when a figure can be selected safely.

## Product Behavior

- The asset list left rail is for switching between different assets only.
- Within one asset detail page, navigation is lightweight section anchors: Intuition, Figure, Mechanism, Terms, Transfer, Technical.
- The default detail view starts with a reader card that explains the asset in research-note style, not a long story and not a compressed abstract.
- Existing fields remain visible: Challenge, Method, Reusable insight, Why it works, Transfer targets, Boundary, Code evidence, Evidence quotes, Raw pipeline evidence.
- Figures are selective. Each asset gets at most one important figure in the first implementation, with room for two later. The page must not dump every figure from a paper.

## Data Model

Assets may include:

```json
{
  "reader_card": {
    "short_title": "IAF: 让 VAE 后验更灵活但仍可算密度",
    "intuition": "...",
    "why_old_way_fails": "...",
    "mechanism_steps": ["...", "..."],
    "key_terms": [{"term": "triangular Jacobian", "plain": "..."}],
    "transfer_rule": "...",
    "misuse_warning": "...",
    "technical_summary": "..."
  },
  "figures": [
    {
      "kind": "important",
      "path": "figures/high_impact_ml/<asset>/page-2.png",
      "page": "2",
      "caption": "Figure 1: ...",
      "why_selected": "...",
      "source": "pdf_page_render"
    }
  ]
}
```

Figure paths are relative to `IDEASCOUT_ASSET_STORE`. Portal serves them through `/asset-files/<path>` with path traversal protection.

## Batch Processing

- Run `scripts/enhance_reader_cards.py --store <store> --batch <batch> --with-figures`.
- The script snapshots the current `assets.jsonl` before in-place replacement.
- It writes a temporary JSONL and atomically replaces `assets.jsonl`.
- It writes `<batch>/reader_card_run.json` with counts and snapshot path.
- Separate tmux sessions can process separate batches because each session writes only one batch directory plus shared `figures/<batch>/...` paths.

## Figure Selection

The first implementation renders a selected PDF page, not a precise crop. It selects a page by scoring figure/caption text and overlap with mechanism keywords. This is intentionally conservative: useful overview pages are better than broken automated crops.

## Acceptance Criteria

- Portal detail page shows Reader Card, mechanism steps, term explanations, figure if present, and technical summary.
- Asset figures are served only from inside the asset store.
- Existing assets without `reader_card` or `figures` still render with the old technical fields.
- Batch jobs can run per batch in parallel in tmux.
