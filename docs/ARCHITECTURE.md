# Architecture — current state (Phase 0 baseline)

A one-page map of how data flows today, before the multi-mode build-out. This
reflects the code as it exists at the start of the build plan and is the
reference the later phases refactor against.

## High-level flow

```
 CSV upload / sample_data ──► DataSource(s) ──► ReconWrapper.run_rule(rule_id)
                                                      │
                                            rules.py mini-model
                                                      │  (selects an engine by matching_strategy)
                                                      ▼
                                      engines.py  ──►  ReconResult
                                                      │  (detail / breaks / summary)
                                          optional enrich(): ai/* passes
                                                      │
                                                      ▼
                                          app.py Streamlit tabs
```

## Components

| Layer | File | Responsibility |
|-------|------|----------------|
| Types | `base.py` | `DataSource` (a feed + key/amount/narration columns), `ReconResult` (detail, breaks, summary, + AI fields), enums `ReconType` / `MatchStrategy` / `MatchStatus`. |
| Engines | `engines.py` | The actual matching. `transactional_recon()` is the 1:1 exact-key join used by R1. Also `semantic_recon()` (R2) and `one_to_many_recon()` (R3). |
| Rules | `rules.py` | One small class per rule, registered in `RULE_REGISTRY`. Declares `rule_id`, `recon_key`, `required_sources`, `matching_strategy`, and in `run()` maps its roles onto an engine. |
| Orchestrator | `wrapper.py` | `ReconWrapper`: discovers rules from the registry, `run_rule()` executes one rule and optionally `enrich()`es it with the AI layer. |
| AI layer | `ai/` | `client.py` (OpenAI / Anthropic / Offline + `default_client()`), `classify.py`, `journal.py`, `anomaly.py`, `detect.py`, `embeddings.py`. Each AI call has a deterministic offline fallback. |
| Sample data | `samples.py` | Built-in synthetic feeds per rule (shared by the app and the tests). |
| UI | `app.py` | Streamlit front end: rule picker, per-feed upload + column mapping, run button, result tabs. |

## Inputs

A **`DataSource`** wraps one feed:

```python
DataSource(role, df, key_column, amount_column, narration_column=None)
```

- `role` — logical name the rule refers to it by (`oms_pos`, `wallet`, `bank`, `gl`).
- `normalized()` → a 2-column frame `[recon_key, <role>_amount]` with the key cast to a clean string.
- `normalized_with_narration()` → adds `<role>_narration` (used only by semantic matching).

In the UI, each rule declares its required roles (`RULE_UI` in `app.py`); the user
maps a key column and amount column per feed (plus narration for R2).

## The engine R1 uses: `transactional_recon()`

1. Normalize both sources to `[recon_key, <role>_amount]`.
2. Detect duplicate keys on each side (surfaced in the summary, not aggregated).
3. **Outer join** on `recon_key` with an indicator.
4. Classify each key:
   - present both sides, `abs(left − right) <= tolerance` → `MATCHED`
   - present both sides, amounts differ → `AMOUNT_MISMATCH`
   - left only → `MISSING_IN_RIGHT`
   - right only → `MISSING_IN_LEFT`
5. `difference = left_amount.fillna(0) − right_amount.fillna(0)`.
6. `breaks` = every row whose status is not `MATCHED`.

`tolerance` defaults to `0.01`. **Amounts are compared as floats today** — a known
limitation the build plan addresses (use `Decimal`/minor units in the new modes).

## Result schema (`ReconResult`)

- `detail` — every key with its `status` and `difference` (+ `<role>_amount` columns).
- `breaks` — the non-matched subset of `detail`.
- `summary` — counts (`total_keys`, `matched`, `amount_mismatch`, `missing_in_right`,
  `missing_in_left`, `total_breaks`, duplicate-key counts) and totals
  (`<role>_total`, `net_difference`).
- AI fields (populated only when enriched): `ai_commentary`, `confidence`,
  `journals`, `anomalies`; per-break columns like `ai_category` are added to `breaks`.

The semantic and one-to-many engines return the **same** `ReconResult` contract
with extra status values (`SEMANTIC_MATCH`, `ONE_TO_MANY_MATCH`) and columns
(`match_method`, `match_confidence`, `matched_members`, `group_size`).

## AI enrichment (`wrapper.enrich`)

When `run_rule(..., enrich=True)` is called (the app does this only if an OpenAI
key is entered), three passes run over the finished result: `classify_breaks`
(root cause + commentary), `detect_anomalies` (outlier flags), `generate_journals`
(draft Dr/Cr entries). With no key, `default_client()` returns `OfflineClient`
and each pass uses its deterministic heuristic. **The AI layer only annotates;
it never changes match/break classification.**

## UI tabs (`app.py`)

Sidebar holds the OpenAI key (session-only, password field) and model. Main pane:
rule dropdown → per-feed inputs (sample data or CSV upload + column selectors) →
**Run**. Results render as four tabs: **Detail**, **Breaks**, **Draft journals**,
**Anomalies**, plus summary metrics and the overall AI commentary.

## Regression guardrail

`tests/test_r1_regression.py` snapshots R1 on the built-in sample data (per-key
status, per-key difference, and summary counts). It must keep passing unchanged
through every later phase.
```bash
pip install -r requirements-dev.txt
pytest
```
