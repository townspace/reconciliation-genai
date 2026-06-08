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

def _pick(options, *preferred):
    """Default a selectbox to the first preferred column that exists."""
    for p in preferred:
        if p in options:
            return options.index(p)
    return 0


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Reconciliation GenAI", page_icon="🧮", layout="wide")
st.title("🧮 Reconciliation + GenAI")
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

# --- Rule selection ---------------------------------------------------------
rule_id = st.selectbox(
    "Reconciliation rule",
    options=wrapper.list_rules(),
    format_func=lambda r: wrapper.rules[r].label,
)
spec = wrapper.rules[rule_id]
st.markdown(f"> {wrapper.describe(rule_id).splitlines()[-1].strip()}")

feeds = spec.feeds          # list of FeedSpec (role, hint, narration)
use_sample = st.toggle("Use built-in sample data", value=True,
                       help="Turn off to upload your own CSVs.")
samples = sample_data(rule_id) if use_sample else {}

# --- Per-role inputs --------------------------------------------------------
sources: dict[str, DataSource] = {}
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
            index=_pick(opts, "order_id", "posting_id", "bank_ref", "journal_id"),
            key=f"key_{role}")
        amt_col = st.selectbox(
            "Amount column", opts,
            index=_pick(opts, "wallet_amount_utilized", "transaction_amount", "amount"),
            key=f"amt_{role}")
        narr_col = None
        if feed.narration:
            narr_col = st.selectbox(
                "Narration column", opts,
                index=_pick(opts, "narration", "description"),
                key=f"narr_{role}")

        sources[role] = DataSource(role, df, key_col, amt_col,
                                   narration_column=narr_col)

# --- Run --------------------------------------------------------------------
st.divider()
run = st.button("▶ Run reconciliation", type="primary", disabled=not ready,
                width="stretch")
if not ready:
    st.warning("Upload a CSV for every feed (or switch on sample data) to enable the run.")

if run and ready:
    client = OpenAIClient(api_key=api_key, model=model) if api_key else None
    with st.spinner("Reconciling" + (" and enriching with AI…" if client else "…")):
        try:
            result = wrapper.run_rule(rule_id, sources, enrich=bool(client),
                                      client=client)
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
        st.dataframe(result.detail, width="stretch")

    with tab_breaks:
        if len(result.breaks):
            st.dataframe(result.breaks, width="stretch")
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
