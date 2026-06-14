<div align="center">

# 🧭 IdeaScout

### Profile-Guided Cross-Domain Research Idea Discovery with LLMs

<p>
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/Status-v0.1.0-orange" alt="Status">
  <img src="https://img.shields.io/badge/LLM-Codex%20CLI-purple" alt="LLM">
</p>

**Discover cross-domain ideas that transfer to your own research.**

</div>

---

## ✨ What is IdeaScout?

**IdeaScout is a profile-guided toolkit for discovering transferable research ideas from large paper collections.**

Instead of only finding papers that already match your topic, IdeaScout helps you answer a more useful research question:

> **Can the core idea of this paper transfer to my own research problem?**

You define your own **research profile** — including your target tasks, preferred mechanisms, negative filters, and scoring criteria. IdeaScout then:

- filters a large paper collection into promising candidates,
- asks an LLM to infer each paper’s **core idea**,
- scores whether that idea is **transferable** to your research direction,
- and exports a ranked reading list for deeper study.

It is designed for researchers who want to mine ideas from **other fields**, not just their own.

---

## 🎯 Why IdeaScout?

Traditional paper search is often too narrow:

- keyword search finds papers **similar in topic**, but not necessarily **useful in mechanism**;
- reading thousands of papers manually is too slow;
- many of the best ideas come from **other domains** with different terminology.

IdeaScout is useful when you want to:

- 🔍 discover **cross-domain transferable ideas**
- 🧠 search for **mechanisms**, not just topics
- ⚙️ customize screening for **your own research profile**
- 📊 rank papers by **transferability, novelty, and feasibility**
- 🔁 run large-scale LLM scoring jobs with **resume** and **auto-retry**

---

## 🧩 At a glance

| Step | What it does |
|------|---------------|
| **1. Define a profile** | Describe your research task, preferred mechanisms, negative filters, and scoring dimensions. |
| **2. Filter candidates** | Quickly prune a large paper collection using rule-based heuristics. |
| **3. Score with an LLM** | Ask Codex to infer each paper’s idea and judge whether it transfers to your task. |
| **4. Export top papers** | Produce ranked CSV / JSONL outputs for reading, analysis, or portal integration. |

---

## 🧠 Core idea

IdeaScout separates idea discovery into **two stages**:

### 1) Rule-based candidate filtering

A fast filtering stage that keeps papers likely to contain useful transferable ideas.

It uses:

- profile keywords,
- preferred mechanisms,
- negative filters,
- and lightweight heuristic scoring.

### 2) LLM-based idea scoring

A slower but more meaningful scoring stage.

For each candidate paper, the LLM:

- reads the **title** and **abstract**,
- infers the paper’s **core idea**,
- identifies the **transferable mechanism**,
- and scores how well the idea fits **your research profile**.

---

## 🏗️ How the pipeline works

```text
Large paper collection (JSONL)
        ↓
Research profile (YAML)
        ↓
Rule-based candidate filtering
        ↓
Candidate papers
        ↓
LLM idea scoring (Codex CLI)
        ↓
Ranked idea list
        ↓
Top papers for reading / CSV / JSONL / portal
```

---

## 📦 Features

- ✅ Profile-guided idea discovery
- ✅ Cross-domain paper screening
- ✅ Rule-based candidate filtering
- ✅ LLM-based idea inference and scoring
- ✅ Resume support for long-running jobs
- ✅ Auto-retry when quota is hit
- ✅ Export to CSV / JSONL
- ✅ Portal-ready output preparation
- ✅ Example profiles for different research directions

---

## 📁 Repository structure

```text
idea-scout/
├── README.md
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── profile_template.yaml
│   ├── profile_speechprivacy_accent_example.yaml
│   └── profile_cv_domain_adaptation_example.yaml
├── examples/
│   └── example_input.jsonl
├── scripts/
│   ├── filter_candidates.py
│   ├── score_with_codex.py
│   ├── run_autoretry.py
│   ├── export_rankings.py
│   ├── prepare_portal_ready.py
│   └── check_progress.py
└── idea_scout/
    ├── __init__.py
    ├── io_utils.py
    ├── profile.py
    ├── filter_candidates.py
    ├── codex_idea_score.py
    ├── run_autoretry.py
    ├── export_rankings.py
    ├── prepare_portal_ready.py
    └── check_progress.py
```

---

## 🚀 Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/idea-scout.git
cd idea-scout

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

---

### 2. Log in to Codex

```bash
codex login --device-auth
printf 'Reply only OK\n' | codex exec -
```

If everything works, you should see:

```text
OK
```

---

### 3. Create your profile

Copy the template:

```bash
cp configs/profile_template.yaml configs/my_profile.yaml
```

Edit `configs/my_profile.yaml`.

Example:

```yaml
project_name: My Research Project

description: >
  I want to discover transferable ideas from cross-domain machine learning papers
  that may help my own research problem.

target_tasks:
  - name: Main task
    description: >
      Describe your core research problem here.

preferred_mechanisms:
  - latent representation editing
  - modular adapters
  - cross-modal alignment
  - controllable generation
  - concept erasure
  - temporal modeling

positive_keywords:
  - representation editing
  - disentanglement
  - subspace
  - latent direction
  - retrieval augmentation
  - routing
  - controllable generation

negative_keywords:
  - survey
  - benchmark only
  - dataset only
  - leaderboard
  - pure application

scoring_dimensions:
  - key: transferability_to_my_task
    name: Transferability to my task
    description: Whether the paper's core idea can be adapted to my research task.
    weight: 2.0

  - key: method_novelty
    name: Method novelty
    description: Whether the paper contains a genuinely interesting method or theory idea.
    weight: 1.2

  - key: implementation_feasibility
    name: Implementation feasibility
    description: Whether the idea looks practical enough to implement or test.
    weight: 1.0
```

---

## 🔎 Step 1: Filter candidate papers

IdeaScout expects a JSONL file where each line is one paper.

Minimal input format:

```json
{"title":"A paper title","abstract":"The abstract text.","venue":"ICLR","year":2025,"url":"https://example.com"}
```

Run rule-based filtering:

```bash
python scripts/filter_candidates.py \
  --input examples/example_input.jsonl \
  --profile configs/my_profile.yaml \
  --output-keep data/candidates.jsonl \
  --output-reject data/rejected.jsonl \
  --output-summary reports/filter_summary.json \
  --target-total 2000 \
  --min-score 1.0
```

Outputs:

- `data/candidates.jsonl`
- `data/rejected.jsonl`
- `reports/filter_summary.json`

---

## 🤖 Step 2: Score papers with Codex

Before running a large job, test a single paper first:

```bash
python -u scripts/score_with_codex.py \
  --input data/candidates.jsonl \
  --profile configs/my_profile.yaml \
  --output data/test_scores.jsonl \
  --failures-output data/test_failures.jsonl \
  --top-k 1 \
  --max-new-items 1 \
  --codex-cmd "codex exec"
```

If the test works, run the full job:

```bash
nohup python -u scripts/run_autoretry.py \
  --input data/candidates.jsonl \
  --profile configs/my_profile.yaml \
  --output data/idea_scores.jsonl \
  --failures-output data/idea_score_failures.jsonl \
  --top-k 2000 \
  --codex-cmd "codex exec" \
  --batch-size 1 \
  --sleep-between-rounds 2 \
  --sleep-on-quota 3600 \
  --sleep-on-error 600 \
  --timeout 900 \
  > logs/run_idea_scores_$(date +%F-%H%M%S).out 2>&1 &
```

---

## 📊 Step 3: Check progress

```bash
python scripts/check_progress.py \
  --output data/idea_scores.jsonl \
  --target-total 2000
```

Or monitor continuously:

```bash
watch -n 30 'python scripts/check_progress.py --output data/idea_scores.jsonl --target-total 2000'
```

To inspect the latest log:

```bash
tail -f $(ls -t logs/run_idea_scores_*.out | head -1)
```

---

## 🏆 Step 4: Export top-ranked papers

```bash
python scripts/export_rankings.py \
  --input data/idea_scores.jsonl \
  --output data/top100_ideas.csv \
  --top-k 100
```

This gives you a ranked CSV that you can open in Excel, Numbers, or LibreOffice.

---

## 📤 Output format

Each scored paper contains the original metadata plus compact LLM-generated fields.

Example:

```json
{
  "title": "Representation Surgery for Concept Editing",
  "venue": "ICLR",
  "year": 2025,
  "is_suitable": true,
  "priority": "keep",
  "idea_core": "The paper identifies editable concept directions in neural representations.",
  "transferable_mechanism": "Subspace intervention can be reused for controlled representation editing.",
  "fit_reason": "The mechanism aligns well with the user-defined profile.",
  "risk_or_limitation": "The abstract does not show whether all constraints are preserved.",
  "score_overall_fit": 8.0,
  "score_theory_novelty": 7.0,
  "scores": {
    "transferability_to_my_task": 8.0,
    "method_novelty": 7.0,
    "implementation_feasibility": 6.0
  },
  "rank_score": 7.55
}
```

---

## 🧪 Example profiles

IdeaScout includes a few example profiles.

### 🎙️ Speech privacy + accent conversion

`configs/profile_speechprivacy_accent_example.yaml`

Looks for ideas related to:

- representation disentanglement
- selective attribute obfuscation
- accent conversion
- latent editing
- leakage control

### 🖼️ Computer vision domain adaptation

`configs/profile_cv_domain_adaptation_example.yaml`

Looks for ideas related to:

- domain generalization
- distribution shift
- test-time adaptation
- robust representations
- feature alignment

These are **examples only**. The intended usage is that each user creates **their own profile**.

---

## 🛠️ Troubleshooting

### Codex token invalidated

If you see errors like:

- `401 Unauthorized`
- `token_invalidated`
- `refresh_token_invalidated`
- `Your session has ended`

Run:

```bash
codex logout || true
codex login --device-auth
printf 'Reply only OK\n' | codex exec -
```

Then restart the same command.  
IdeaScout will resume from the existing output file.

---

### Quota or usage limit

If Codex hits a usage limit, the auto-retry runner will sleep and try again later.

Typical log message:

```text
[SLEEP_QUOTA] sleeping 3600s
```

Already processed papers are written to disk immediately, so progress is not lost.

---

### No visible log output

Use unbuffered Python:

```bash
python -u scripts/run_autoretry.py ...
```

For background jobs:

```bash
nohup python -u scripts/run_autoretry.py ... > logs/run.out 2>&1 &
```

---

## 🧭 Recommended workflow

A practical workflow for large paper collections is:

1. Collect papers from conference websites, OpenReview, DBLP, or Semantic Scholar.
2. Convert them into a JSONL file with title and abstract.
3. Write a research profile for your own task.
4. Run rule-based filtering to keep 1k–5k candidates.
5. Run LLM-based idea scoring.
6. Export the top 50–200 papers.
7. Read only the most promising papers in depth.
8. Use the highest-ranked ideas to design new methods or experiments.

---

## 🗺️ Roadmap

Planned future features:

- [ ] PDF full-text parsing
- [ ] OpenReview collectors
- [ ] Semantic Scholar integration
- [ ] Web portal for browsing scored papers
- [ ] Multi-profile comparison
- [ ] Multi-LLM backend support
- [ ] Mechanism-based clustering
- [ ] BibTeX export

---

## 🤝 Contributing

Contributions are welcome.

Good first contributions include:

- adding new example profiles,
- improving prompt templates,
- adding paper collectors,
- improving export and ranking tools,
- building a lightweight browsing UI.

---

## 📄 License

Released under the **MIT License**.

---


### 💡 One-line summary

**IdeaScout turns large paper collections into personalized ranked lists of transferable research ideas.**

</div>
