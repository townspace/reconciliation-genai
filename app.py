"""
app.py
------
Streamlit web UI for the Reconciliation + GenAI framework.

Run it:
    cd recon
    pip install -r requirements.txt
    python3 -m streamlit run app.py

What it does:
  - Lets you pick a rule (R1 exact / R2 semantic / R3 one-to-many).
  - Upload a CSV per required source (or load built-in sample data).
  - Map each feed's key / amount / narration columns.
  - Runs the recon and, if you paste an OpenAI key in the sidebar, enriches the
    result with AI root-cause commentary, draft journals, and anomaly flags.
    With no key it runs the deterministic offline heuristics instead.

The OpenAI key is entered in the sidebar, kept only in this session, and never
written to disk.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from base import DataSource
from wrapper import ReconWrapper
from ai.client import OpenAIClient
from samples import sample_data
from pipeline import golden_path

def _display(df):
    """Hide columns that are entirely empty/NA so a mode's unused generalised
    columns don't clutter the table (Phase 2: extra columns only when populated)."""
    if df is None or not len(df):
        return df
    keep = [c for c in df.columns
            if not df[c].isna().all()
            and not (df[c].astype(str).str.strip() == "").all()]
    return df[keep]


def _pick(options, *preferred):
    """Default a selectbox to the first preferred column that exists."""
    for p in preferred:
        if p in options:
            return options.index(p)
    return 0


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Reconciliation Wrap model", page_icon="🧮", layout="wide")
st.title("🧮 Reconciliation Wrap model")
st.caption("Match two feeds, then let AI explain the breaks, draft journals, and flag anomalies.")

# --- Sidebar: credentials + model ------------------------------------------
with st.sidebar:
    st.header("AI settings")
    api_key = st.text_input(
        "OpenAI API key", type="password", placeholder="sk-...",
        help="Used only for this session; never saved to disk. Leave blank to run "
             "the offline heuristics.",
    )
    model = st.text_input("Model", value="gpt-4o-mini",
                          help="e.g. gpt-4o-mini (fast/cheap) or gpt-4o.")
    if api_key:
        st.success(f"AI mode: LIVE (OpenAI · {model})")
    else:
        st.info("AI mode: OFFLINE (deterministic heuristics)")
    st.divider()
    st.markdown(
        "**How it works**\n\n"
        "1. Pick a rule.\n"
        "2. Upload a CSV per feed (or use sample data).\n"
        "3. Map the key / amount columns.\n"
        "4. Run — AI enriches the result if a key is set."
    )

wrapper = ReconWrapper()

view = st.sidebar.radio("View", ["Single rule", "Pipeline (end-to-end)"])

# ===========================================================================
# Pipeline view — the connected OMS -> bank money trace (Phase 7)
# ===========================================================================
if view == "Pipeline (end-to-end)":
    st.header("End-to-end pipeline — golden path")
    st.caption("R2 semantic → R8 settlement timing → R13 charge validation → "
               "R12 bank reco, on the built-in sample data.")

    pipe, sources_ps, rate_maps, flows = golden_path()
    presult = pipe.run(sources_ps, wrapper=wrapper, rate_map_per_stage=rate_maps)

    st.subheader("Per-stage summary (four-lane structure)")
    st.dataframe(presult.stage_summary, width="stretch", hide_index=True)
    cols = st.columns(len(pipe.stages))
    for c, stg in zip(cols, pipe.stages):
        r = presult.results[stg.name]
        c.metric(f"{stg.name} ({stg.lane})",
                 f"{len(r.detail) - len(r.breaks)}/{len(r.detail)} ok",
                 delta=f"-{len(r.breaks)} breaks" if len(r.breaks) else "clean",
                 delta_color="inverse")

    st.subheader("End-to-end trace — first breaking hop per flow")
    trace = pipe.trace(presult, flows)

    def _hl(row):
        out = []
        fb = row["first_break"]
        for col in trace.columns:
            if col == "first_break" and fb != "(clean)":
                out.append("background-color:#ffe0e0")
            elif col == fb and fb != "(clean)":
                out.append("background-color:#ffe0e0")
            else:
                out.append("")
        return out

    st.dataframe(trace.style.apply(_hl, axis=1), width="stretch", hide_index=True)

    st.subheader("Drill into a hop")
    pick = st.selectbox("Stage", [s.name for s in pipe.stages])
    res = presult.results[pick]
    if len(res.breaks):
        st.dataframe(_display(res.breaks), width="stretch")
    else:
        st.success(f"{pick}: no breaks at this hop. ✅")
    st.stop()

# --- Rule selection ---------------------------------------------------------
rule_ids = wrapper.list_rules()
if not rule_ids:
    st.error("No reconciliation rules are registered.")
    st.stop()
# Precompute labels into a plain dict; format_func then does a pure dict lookup
# (no live attribute traversal inside Streamlit's widget machinery).
rule_labels = {r: wrapper.rules[r].label for r in rule_ids}
rule_id = st.selectbox(
    "Reconciliation rule",
    options=rule_ids,
    format_func=lambda r: rule_labels.get(r, r),
)
spec = wrapper.rules[rule_id]
st.markdown(f"> {wrapper.describe(rule_id).splitlines()[-1].strip()}")

feeds = spec.feeds          # list of FeedSpec (role, hint, narration)
use_sample = st.toggle("Use built-in sample data", value=True,
                       help="Turn off to upload your own CSVs.")
samples = sample_data(rule_id) if use_sample else {}

# --- Per-role inputs --------------------------------------------------------
sources: dict[str, DataSource] = {}
rate_overrides: dict = {}      # rate_validation: extra column choices
ready = True
cols = st.columns(len(feeds))

for feed, col in zip(feeds, cols):
    role = feed.role
    with col:
        st.subheader(role)
        st.caption(feed.hint)
        df = None
        if use_sample:
            df = samples[role]
            st.dataframe(df, height=180, width="stretch")
        else:
            up = st.file_uploader(f"{role} CSV", type=["csv"], key=f"file_{role}")
            if up is not None:
                df = pd.read_csv(up)
                st.dataframe(df.head(20), height=180, width="stretch")

        if df is None or df.empty:
            ready = False
            continue

        opts = list(df.columns)
        key_col = st.selectbox(
            "Key column", opts,
            index=_pick(opts, "batch_id", "deposit_batch", "remittance_id", "txn_id",
                        "wallet_txn_id", "order_id", "sub_id", "posting_id",
                        "bank_ref", "journal_id", "charge_type", "merchant_id"),
            key=f"key_{role}")
        amt_col = st.selectbox(
            "Amount column", opts,
            index=_pick(opts, "wallet_amount_utilized", "gross_amount", "net_amount",
                        "credit_amount", "actual_charge", "actual_commission",
                        "rate_pct", "transaction_amount", "amount"),
            key=f"amt_{role}")
        narr_col = None
        if feed.narration:
            narr_col = st.selectbox(
                "Narration column", opts,
                index=_pick(opts, "narration", "description"),
                key=f"narr_{role}")
        date_col = None
        if feed.date:
            date_col = st.selectbox(
                "Date column", opts,
                index=_pick(opts, "txn_date", "settle_date", "date", "value_date",
                            "posting_date"),
                key=f"date_{role}")
        # Extra column pickers (e.g. rate_validation base/lookup columns).
        for label, rmap_key, candidates in feed.extra:
            choice = st.selectbox(label, opts, index=_pick(opts, *candidates),
                                  key=f"extra_{role}_{rmap_key}")
            rate_overrides[rmap_key] = choice

        sources[role] = DataSource(role, df, key_col, amt_col,
                                   narration_column=narr_col, date_column=date_col)

# --- Run --------------------------------------------------------------------
st.divider()
# Amount tolerance. Exact-key rules default to 0 (strict equality), matching the
# original behaviour; other modes default to their configured tolerance.
default_tol = 0.0 if spec.mode == "exact_key" else float(spec.tolerance or 0.0)
tol = st.number_input(
    "Amount tolerance (absolute)", min_value=0.0, value=default_tol, step=0.01,
    format="%.2f",
    help="Amounts within this absolute difference count as matching. 0 = strict "
         "equality (exact-key default).")
run = st.button("▶ Run reconciliation", type="primary", disabled=not ready,
                width="stretch")
if not ready:
    st.warning("Upload a CSV for every feed (or switch on sample data) to enable the run.")

if run and ready:
    client = OpenAIClient(api_key=api_key, model=model) if api_key else None
    with st.spinner("Reconciling" + (" and enriching with AI…" if client else "…")):
        try:
            result = wrapper.run_rule(rule_id, sources, enrich=bool(client),
                                      client=client, tolerance=tol,
                                      rate_map=rate_overrides or None)
        except Exception as exc:  # surface input/config errors plainly
            st.error(f"Run failed: {exc}")
            st.stop()

    s = result.summary
    st.subheader("Summary")
    m = st.columns(4)
    m[0].metric("Matched", int(s.get("matched", s.get("semantic_matched",
                s.get("one_to_many_matched", 0)) or 0)))
    m[1].metric("Breaks", int(s.get("total_breaks", len(result.breaks))))
    m[2].metric("Confidence", f"{result.confidence:.2f}")
    m[3].metric("AI", "OpenAI" if client else "Offline")

    if result.ai_commentary:
        st.info(f"**AI commentary:** {result.ai_commentary}")

    tab_detail, tab_breaks, tab_journals, tab_anom = st.tabs(
        ["Detail", "Breaks", "Draft journals", "Anomalies"])

    with tab_detail:
        st.dataframe(_display(result.detail), width="stretch")

    with tab_breaks:
        if len(result.breaks):
            st.dataframe(_display(result.breaks), width="stretch")
        else:
            st.success("No breaks — everything reconciled. ✅")

    with tab_journals:
        if len(result.journals):
            st.dataframe(result.journals, width="stretch")
            st.download_button("Download journals CSV",
                               result.journals.to_csv(index=False),
                               file_name=f"{rule_id}_journals.csv", mime="text/csv")
        else:
            st.caption("No draft journals (no monetary breaks to adjust).")

    with tab_anom:
        if len(result.anomalies):
            st.dataframe(result.anomalies, width="stretch")
        else:
            st.caption("No anomalies flagged.")
