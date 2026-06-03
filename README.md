# Reconciliation Wrapper Model + GenAI Layer

A modular reconciliation engine. Each rule is a self-contained **mini model**;
a top-level **wrapper** orchestrates them. The original transactional foundation
(**R1**) is unchanged, and a **GenAI layer** has been added on top to deliver the
six integration opportunities (semantic matching, exception root-cause,
auto-journals, source detection, one-to-many discovery, anomaly detection).

> **Runs with zero credentials.** Every AI feature has a deterministic heuristic
> fallback, so the whole model is runnable and testable offline today. Set
> `OPENAI_API_KEY` and the same calls transparently upgrade to OpenAI
> (`ANTHROPIC_API_KEY` is also supported for Claude).

## Layout

| File          | Role |
|---------------|------|
| `base.py`     | Shared types: `ReconType`, `MatchStatus`, `MatchStrategy`, `DataSource`, `ReconResult`, `BaseReconModel` |
| `engines.py`  | Recon logic: `transactional_recon()` (unchanged) + new `semantic_recon()` and `one_to_many_recon()` |
| `rules.py`    | Per-rule mini models + registry. **R1** (exact), **R2** (semantic), **R3** (one-to-many) |
| `wrapper.py`  | `ReconWrapper` orchestrator, `enrich()` AI pipeline, interactive + AI-driven config builders |
| `ai/`         | The GenAI subpackage (see below) |
| `demo.py`     | Original R1 example over synthetic data (regression-stable) |
| `demo_genai.py` | Showcase of all GenAI features end to end |

## The `ai/` subpackage

| Module           | Feedback item | What it does |
|------------------|---------------|--------------|
| `ai/client.py`     | (infra)  | `OpenAIClient` + `AnthropicClient` (raw `urllib`, no SDK) + `OfflineClient`. `default_client()` picks OpenAI when `OPENAI_API_KEY` is set, else Anthropic when `ANTHROPIC_API_KEY` is set, else offline. Any API failure falls back silently. |
| `ai/embeddings.py` | #1       | `LocalEmbedder` — TF-IDF-weighted word + char-trigram vectors, L2-normalised, plus `cosine_sim_matrix()`. Drop-in slot for Voyage/OpenAI embeddings. |
| `ai/classify.py`   | #2       | `classify_breaks()` — root-cause category (TIMING / DUPLICATE / FX_RATE / …), per-break confidence, and audit commentary. |
| `ai/detect.py`     | #3       | `detect_sources()` — profiles file headers/samples, infers source type, key/amount/narration columns, and recommends a rule + strategy. |
| `ai/journal.py`    | #5       | `generate_journals()` — balanced double-entry Dr/Cr drafts with narrative + FS impact, ERP-ready. |
| `ai/anomaly.py`    | #6       | `detect_anomalies()` — robust modified-z-score outlier flags + recurring-pattern notes. |

One-to-many discovery (#4) lives in the engine layer (`engines.one_to_many_recon`).

## Matching strategies

Rules now declare a `matching_strategy` that selects the engine:

| Strategy      | Engine                  | Example rule |
|---------------|-------------------------|--------------|
| `exact`       | `transactional_recon()` | R1 |
| `semantic`    | `semantic_recon()`      | R2 (bank narration vs GL description) |
| `one_to_many` | `one_to_many_recon()`   | R3 (split settlements summing to one order) |

`ReconResult` was extended with `ai_commentary: str`, `confidence: float`,
`journals` and `anomalies` DataFrames — populated by the enrich pipeline.

## Web app (see it in your browser)

```bash
cd recon
python3 -m venv .venv && source .venv/bin/activate   # recommended (avoids NumPy clashes)
pip install -r requirements.txt
python3 -m streamlit run app.py
```

Then open the URL it prints (default http://localhost:8501). Pick a rule, use the
built-in sample data (or upload your own CSVs), map the key/amount columns, and
hit **Run**. Paste an OpenAI key in the sidebar to turn on AI commentary, draft
journals, and anomaly flags; with no key it runs the offline heuristics. The key
lives only in that session and is never written to disk.

## Run it (CLI)

```bash
python3 demo.py          # original R1, unchanged
python3 demo_genai.py    # full GenAI showcase (offline heuristics)

# Same showcase, upgraded to OpenAI:
export OPENAI_API_KEY=sk-...
python3 demo_genai.py

# (Claude is also supported:)
export ANTHROPIC_API_KEY=sk-ant-...
python3 demo_genai.py
```

Optional overrides: `RECON_LLM_MODEL` (default `gpt-4o-mini` for OpenAI,
`claude-sonnet-4-6` for Anthropic).

## Use programmatically

```python
from base import DataSource
from wrapper import ReconWrapper

sources = {
    "oms_pos": DataSource("oms_pos", oms_df,    "order_id", "wallet_amount_utilized"),
    "wallet":  DataSource("wallet",  wallet_df, "order_id", "transaction_amount"),
}

wrapper = ReconWrapper()

# Plain run (unchanged behaviour):
result = wrapper.run_rule("R1", sources)

# With the full AI layer (classification + anomalies + journals):
result = wrapper.run_rule("R1", sources, enrich=True)
print(result.ai_commentary)   # overall narrative
print(result.confidence)      # mean break confidence
result.breaks                 # now carries ai_category / ai_commentary
result.journals               # ERP-ready draft entries
result.anomalies              # outliers flagged
```

### Semantic matching (R2)

```python
sources = {
    "bank": DataSource("bank", bank_df, "ref", "Debit", narration_column="Narration"),
    "gl":   DataSource("gl",   gl_df,   "Journal ID", "Amount", narration_column="Description"),
}
result = wrapper.run_rule("R2", sources)   # matches by narration similarity
```

### AI-driven config (replaces manual rule selection)

```python
from wrapper import ReconWrapper
cfg = ReconWrapper().build_config_with_ai({"feed_one.csv": df1, "feed_two.csv": df2})
cfg["recommended_rule"], cfg["recommended_strategy"], cfg["ready_sources"]
```

## Plugging in a hosted embedder

`LocalEmbedder` is the dependency-free default. A hosted `OpenAIEmbedder`
(`text-embedding-3-small`, raw `urllib`) is included — opt in by passing it
explicitly:

```python
from ai.embeddings import OpenAIEmbedder
result = wrapper.run_rule("R2", sources, embedder=OpenAIEmbedder())
```

To swap in another hosted embedder (e.g. Voyage), implement the
`Embedder.embed(texts) -> np.ndarray` interface in `ai/embeddings.py` and
return L2-normalised vectors. Note that R2's `sim_threshold` is
embedder-dependent: the local embedder uses ~0.40 (well above its measured
~0.06 noise floor); a strong hosted embedder typically warrants ~0.8, so raise
the rule's threshold when you switch.

## Adding the next rule

1. In `rules.py`, add a class decorated with `@register`.
2. Set `rule_id`, `description`, `recon_type`, `recon_key`, `required_sources`,
   and `matching_strategy`.
3. In `run()`, map your roles onto the matching engine for that strategy.

A commented template is at the bottom of `rules.py`.
