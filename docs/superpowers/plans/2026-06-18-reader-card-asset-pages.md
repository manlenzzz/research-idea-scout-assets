# Reader Card Asset Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add balanced reader cards and selected paper figures to IdeaScout asset pages and batch-generate them for the authoritative asset store.

**Architecture:** Store readable explanations in `reader_card` and selected visual evidence in `figures` on each asset JSON object. Portal renders those optional fields above the existing technical asset fields. A batch CLI enhances each `assets.jsonl` independently so tmux sessions can process batches in parallel.

**Tech Stack:** Python JSONL processing, FastAPI/Jinja portal, Poppler CLI tools (`pdftotext`, `pdftoppm`) for figure page rendering, pytest.

---

### Task 1: Reader Card Data Layer

**Files:**
- Create: `idea_scout/reader_cards.py`
- Test: `tests/test_reader_cards.py`

- [x] Write tests for fallback reader card generation and sanitization.
- [x] Implement `make_fallback_reader_card()` and `sanitize_reader_card()`.
- [x] Verify with `rtk pytest tests/test_reader_cards.py -q`.

### Task 2: Asset Figure Selection

**Files:**
- Create: `idea_scout/asset_figures.py`
- Test: `tests/test_asset_figures.py`

- [x] Write tests for page scoring and asset-store-relative paths.
- [x] Implement PDF source detection, page text scoring, page rendering, and figure attachment.
- [x] Verify with `rtk pytest tests/test_asset_figures.py -q`.

### Task 3: Portal Rendering

**Files:**
- Modify: `web/app/main.py`
- Modify: `web/app/templates/asset_detail.html`
- Modify: `web/app/static/style.css`
- Test: `tests/test_asset_flow.py`

- [x] Write tests requiring Reader Card rendering and `/asset-files/...` serving.
- [x] Add asset file URL helper and path traversal protection.
- [x] Render `reader_card` and `figures` before existing technical fields.
- [x] Verify with targeted Portal tests.

### Task 4: Batch CLI

**Files:**
- Create: `idea_scout/enhance_reader_cards.py`
- Create: `scripts/enhance_reader_cards.py`
- Test: `tests/test_reader_cards.py`

- [x] Implement batch enhancement with snapshot, temp output, atomic replace, and run manifest.
- [x] Support `--with-figures`, `--limit`, `--overwrite-reader`, and `--overwrite-figures`.
- [x] Verify targeted tests.

### Task 5: Store Processing

**Commands:**

```bash
tmux new-session -d -s reader_bestpaper 'cd /vePFS-Mindverse/user/intern/zhouch/papers/research-idea-scout-assets && rtk python scripts/enhance_reader_cards.py --store /vePFS-Mindverse/user/intern/zhouch/asset_store --batch bestpaper --with-figures 2>&1 | tee /vePFS-Mindverse/user/intern/zhouch/asset_store/bestpaper/reader_card_tmux.log'
tmux new-session -d -s reader_acl 'cd /vePFS-Mindverse/user/intern/zhouch/papers/research-idea-scout-assets && rtk python scripts/enhance_reader_cards.py --store /vePFS-Mindverse/user/intern/zhouch/asset_store --batch high_impact_acl --with-figures 2>&1 | tee /vePFS-Mindverse/user/intern/zhouch/asset_store/high_impact_acl/reader_card_tmux.log'
tmux new-session -d -s reader_cvf 'cd /vePFS-Mindverse/user/intern/zhouch/papers/research-idea-scout-assets && rtk python scripts/enhance_reader_cards.py --store /vePFS-Mindverse/user/intern/zhouch/asset_store --batch high_impact_cvf --with-figures 2>&1 | tee /vePFS-Mindverse/user/intern/zhouch/asset_store/high_impact_cvf/reader_card_tmux.log'
tmux new-session -d -s reader_ml 'cd /vePFS-Mindverse/user/intern/zhouch/papers/research-idea-scout-assets && rtk python scripts/enhance_reader_cards.py --store /vePFS-Mindverse/user/intern/zhouch/asset_store --batch high_impact_ml --with-figures 2>&1 | tee /vePFS-Mindverse/user/intern/zhouch/asset_store/high_impact_ml/reader_card_tmux.log'
```

- [ ] Start tmux sessions per batch.
- [ ] Poll sessions and logs until all complete.
- [ ] Rebuild Portal DB from enhanced store.
- [ ] Run targeted and full tests.
