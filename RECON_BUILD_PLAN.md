# Reconciliation Engine — Build Plan (for Claude Code)

A phase-by-phase plan to take the current single-rule reconciliation app to the
full 13-rule, end-to-end OMS→bank model.

---

## How to use this file

1. Drop this file in the repo root (e.g. `RECON_BUILD_PLAN.md`).
2. In Claude Code, first paste the **Context** and **Conventions** sections so the
   agent has the framing and guardrails.
3. Then feed **one phase at a time**. Tell Claude Code: *"Do Phase N from
   RECON_BUILD_PLAN.md. Stop when the acceptance criteria pass and show me the diff."*
4. Do **not** start a phase until the previous phase's acceptance criteria pass.
5. Phases 1–2 are a refactor with zero behaviour change — verify the R1 regression
   test still passes before moving on.

---

## Context

**Current state.** A Streamlit app with a single-rule UI. The user picks a rule
(R1–R3), supplies two CSV feeds (or uses built-in sample data), maps a key column
and an amount column on each side, and runs reconciliation. Under the hood there is
**one engine**: a 1:1 join on the key with strict amount equality, producing the
statuses `MATCHED`, `AMOUNT_MISMATCH`, `MISSING_IN_LEFT`, `MISSING_IN_RIGHT` plus a
`difference` column. An AI layer enriches results (draft journals, anomalies) and
runs in OFFLINE deterministic-heuristics mode by default, LIVE when an API key is set.

**Target.** A 13-rule model (R1–R13) that traces money left-to-right across four
stages: OMS / internal data → recon bucket 1 (internal reports) → recon bucket 2
(provider / settlement reports + rate masters) → bank reco (bank statement).

**The reframe that drives this plan.** The 13 rules are **not** 13 separate engines.
They reduce to **four match modes**, of which mode 1 already exists:

| Mode | Rules | What it does | Status today |
|------|-------|--------------|--------------|
| 1. `exact_key` | R1, R2, R3, R4, R5, R6 | 1:1 key join, amount equality (± tolerance) | **Built** |
| 2. `tolerance_timing` | R7, R8 | Key join allowing fee differences + value-date lag; decompose the difference | New |
| 3. `rate_validation` | R11, R13 | Compute expected fee from a rate master, compare to actual deduction | New |
| 4. `aggregate_match` | R9, R10, R12 | Group/sum many transactions, match the aggregate to one bank line (N:1) | New |

So the work is **3 new modes + a registry/dispatcher + an orchestration layer**, not
10 new rules. Most of the remaining rules are configuration once their mode exists.

**Validation target (golden-path spine).** One full OMS→bank trace exercises every
new mode at once: **R2** (exact) → **R8** (tolerance_timing) → **R12** (aggregate),
with **R13** (rate_validation) checking the service charge inside the settlement.
Completing Phases 3–5 yields this spine; Phase 7 chains it end-to-end.

---

## Conventions (house rules for Claude Code)

- **Do not break R1.** R1's current behaviour and output are the regression baseline.
  Phase 0 captures a snapshot test; it must keep passing through every later phase.
- **Offline mode stays the default.** Never require an API key for core reconciliation.
  The AI layer is enrichment only and must degrade gracefully when offline.
- **No secrets in code or commits.** API keys come from the existing UI/env only.
- **Small, reviewable steps.** One phase per branch/PR. Show the diff and the test
  output before declaring a phase done.
- **Tests per phase.** Each phase adds tests for its own sample data and asserts the
  expected status breakdown. Do not mark a phase complete on "it runs" alone.
- **Ask before large rewrites.** If a phase seems to require restructuring beyond what
  is described, surface a short plan and wait for confirmation.
- **Keep the UI patterns.** Match the existing Streamlit layout, sidebar, and tab
  structure rather than introducing a new UI paradigm.

---

## Cross-cutting concerns (apply in every phase that touches matching)

- **Money precision.** Use `Decimal` (or integer minor units / paise) for all amounts.
  Float equality causes spurious breaks — never compare raw floats.
- **Duplicate-key handling.** Modes 2 and 4 will see repeated keys (many transactions
  per settlement / per bank line). Decide the strategy explicitly per rule:
  composite key, or aggregate-then-match (sum by key, then compare). This is a
  prerequisite for Phases 3 and 5, not an afterthought.
- **Dates & timezones.** Normalise dates to a single timezone; matching windows are
  measured in calendar days unless a rule says otherwise.
- **Determinism / idempotency.** Same inputs → same output. The AI layer must not
  change match/break classification, only annotate it.

---

## Phase 0 — Orientation & guardrails

**Goal.** Understand the existing code before changing anything, and lock in a
regression baseline for R1.

**Tasks.**
- Map the repo: locate the reconciliation engine, how R1–R3 are defined, how the two
  feeds and key/amount mappings flow into the engine, how sample data is wired, and
  where the AI offline/live layer plugs in.
- Write a 1-page `docs/ARCHITECTURE.md` describing the current data flow
  (inputs → engine → result schema → UI tabs → AI enrichment).
- Add a regression test that runs R1 on the built-in sample data and snapshots the
  exact result (matched count, break count, per-row status, differences).

**Acceptance.**
- `docs/ARCHITECTURE.md` exists and matches the actual code.
- The R1 regression test passes and is wired into the test suite.

---

## Phase 1 — Rule registry + mode dispatcher (refactor, no behaviour change)

**Goal.** Replace the hard-coded rule list with a declarative registry, and route
execution through a mode dispatcher. Existing rules must behave identically.

**Tasks.**
- Define a rule schema:
  `{ id, label, description, left_feed, right_feed, mode, keys, amount_map,
     rate_map, tolerance, date_window, group_by }`
  (fields unused by a given mode may be null).
- Create a `MODE` dispatcher. Move the current engine under `mode = "exact_key"`.
- Migrate R1, R2, R3 into the registry as `exact_key` rules.
- The UI rule dropdown reads from the registry (label + description).

**Acceptance.**
- R1 regression test from Phase 0 still passes, unchanged.
- R2 and R3 run via the registry and produce the same results as before.
- Adding a new rule requires only a registry entry (for existing modes).

---

## Phase 2 — Generalised result schema + mode-aware UI

**Goal.** Let the result schema and UI represent more than two fixed amount columns,
so later modes have somewhere to put their data.

**Tasks.**
- Generalise the result columns to: `recon_key`, `left_amount`, `right_amount`,
  `expected_amount`, `actual_amount`, `status`, `difference`, `break_reason`,
  `computed_from` (columns may be empty depending on mode).
- Make the Detail / Breaks / Draft journals / Anomalies tabs render only the columns
  relevant to the active rule's mode.
- Add a tolerance control (absolute and/or %) to the UI, defaulting to 0 for
  `exact_key` so existing behaviour is unchanged.

**Acceptance.**
- All `exact_key` rules render exactly as before when tolerance = 0.
- Extra columns appear only when a mode populates them.

---

## Phase 3 — Mode 2: tolerance + timing (R7, R8)

**Goal.** Reconcile transaction reports against settlement/provider reports where the
amount differs by fees and the value date lags.

**Tasks.**
- Implement `tolerance_timing`: key join, amount match within tolerance, value-date
  match within a configurable window.
- Decompose `difference` into fee-difference vs timing-difference vs genuine break.
- Add statuses: `MATCHED_WITHIN_TOLERANCE`, `FEE_DIFFERENCE`, `TIMING_DIFFERENCE`.
- Register **R8** (PG/EDC txn → PG/EDC settlement) and **R7** (internal wallet →
  wallet provider).
- Add sample data containing fee deductions and date lags.

**Acceptance.**
- On the sample data, R8 classifies fee and timing differences correctly instead of
  dumping everything into `AMOUNT_MISMATCH`.
- A genuine break (wrong amount beyond tolerance) is still flagged as a break.

---

## Phase 4 — Mode 3: rate validation (R11, R13)

**Goal.** Validate fees/charges by computing the expected value from a rate master,
since there is no second transaction amount to join against.

**Tasks.**
- Add support for a **third input**: a rate master, plus a `rate_map`
  (which key looks up which rate, and the formula, e.g. `expected = base × rate`).
- Implement `rate_validation`: compute `expected_amount`, compare to `actual_amount`
  from the settlement/transaction feed, set `computed_from`.
- Register **R13** (B2C service charge master vs PG/EDC settlement) and **R11**
  (merchant commission master vs channel-partner transactions).
- Sample data: a rate master + transactions where some deductions are off-rate.

**Acceptance.**
- Expected fees are computed from the master and compared per row.
- Off-rate deductions are flagged; on-rate ones pass.

---

## Phase 5 — Mode 4: N:1 aggregation / bank reco (R9, R10, R12)

**Goal.** Match many underlying transactions to a single lump bank credit.

**Tasks.**
- Implement `aggregate_match`: group the left feed by `group_by`
  (settlement batch / value date), sum, then match the aggregate to one bank line.
- Handle partial matches and unallocated bank lines explicitly.
- Register **R12** (settlement → bank statement), **R9** (cash deposit + CMS → bank),
  **R10** (channel-partner txn → bank).
- Sample data: multiple transactions rolling into single bank credits, plus an
  unmatched bank line and an unallocated transaction group.

**Acceptance.**
- Many transactions correctly reconcile against one bank credit.
- Unmatched bank lines and unallocated groups are surfaced as breaks, not hidden.

---

## Phase 6 — Fill the remaining rules (config, not engines)

**Goal.** Register every rule still missing from the diagram.

**Tasks.**
- Register **R4** (OMS-B2B → channel-partner txn) as `exact_key`.
- Register **R5** (internal wallet ↔ PG/EDC) and **R6** (PG/EDC ↔ internal
  subscription) as report-to-report `exact_key` rules.
- Confirm all of R1–R13 appear in the dropdown with correct mode, inputs, and
  sample data.

**Acceptance.**
- All 13 rules are selectable and runnable on sample data with sensible output.
- Reconcile the R14 question: if a 14th rule is intended (it is not on the diagram),
  note it as `TODO` in the registry rather than inventing behaviour.

---

## Phase 7 — Orchestration / end-to-end trace (the diagram itself)

**Goal.** Turn isolated rules into the connected money trace the diagram depicts, where
one rule's output can feed the next and breaks are localised to a hop.

**Tasks.**
- Add a pipeline definition: declare which rules chain (e.g. R8's clean settlements
  feed R12's bank reco), forming the OMS→bank graph.
- Build a runner that executes the graph and produces an end-to-end trace per
  transaction/settlement, flagging the **first hop** where the trace breaks.
- Add a pipeline view in the UI: run the whole chain, show per-stage match/break
  counts, and let the user drill into the breaking hop.

**Acceptance.**
- Running the golden-path spine (R2 → R8 → R12, with R13) end-to-end produces a single
  traceable result and attributes any break to the correct hop.
- The pipeline view reflects the four-lane structure of the target diagram.

---

## Suggested order of work

Phase 0 → 1 → 2 (foundation, no behaviour change) → 3 → 4 → 5 (the three new modes,
in golden-path order) → 6 (config fill) → 7 (chaining). Completing 3–5 already gives
you a full OMS→bank spine; Phase 7 connects it.
