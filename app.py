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

# ---------------------------------------------------------------------------
# Per-rule UI metadata: which roles a rule needs, and whether each role wants a
# narration column. Keeps the dynamic form generic across rules.
# ---------------------------------------------------------------------------
RULE_UI = {
    "R1": {
        "label": "R1 — Exact match (OMS/POS wallet vs internal wallet)",
        "roles": {
            "oms_pos": {"narration": False, "hint": "Primary feed (e.g. OMS/POS export)"},
            "wallet":  {"narration": False, "hint": "Secondary feed (e.g. internal wallet)"},
        },
    },
    "R2": {
        "label": "R2 — Semantic match (bank narration vs GL description)",
        "roles": {
            "bank": {"narration": True, "hint": "Bank statement lines"},
            "gl":   {"narration": True, "hint": "GL postings"},
        },
    },
    "R3": {
        "label": "R3 — One-to-many (split settlements summing to one order)",
        "roles": {
            "oms_pos": {"narration": False, "hint": "Single side (orders)"},
            "wallet":  {"narration": False, "hint": "Split side (postings)"},
        },
    },
}


# ---------------------------------------------------------------------------
# Built-in sample data, so the app is usable with zero setup. Mirrors the
# synthetic frames in demo_genai.py.
# ---------------------------------------------------------------------------
def sample_data(rule_id: str):
    if rule_id == "R1":
        oms = pd.DataFrame({
            "order_id":               ["A101", "A102", "A103", "A104", "A106"],
            "wallet_amount_utilized": [500.00, 250.00, 120.00, 999.00, 50000.00],
            "store_id":               ["S1", "S1", "S2", "S3", "S1"],
        })
        wallet = pd.DataFrame({
            "order_id":           ["A101", "A102", "A103", "A105"],
            "transaction_amount": [500.00, 245.00, 120.00, 300.00],
            "wallet_txn_id":      ["W1", "W2", "W3", "W5"],
        })
        return {"oms_pos": oms, "wallet": wallet}

    if rule_id == "R2":
        bank = pd.DataFrame({
            "bank_ref":  ["BNK-1", "BNK-2", "BNK-3", "BNK-4"],
            "narration": ["NEFT ACME CORP 88123", "NEFT GLOBEX LTD REF4471",
                          "NEFT INITECH PAYROLL JUN", "NEFT STARK INDS ADVANCE"],
            "amount":    [12000.00, 4500.00, 80000.00, 250.00],
        })
        gl = pd.DataFrame({
            "journal_id":  ["J-900", "J-901", "J-902", "J-903"],
            "description": ["ACME CORP invoice 88123", "Globex Limited ref 4471",
                            "Initech payroll June", "Wayne Enterprises misc"],
            "amount":      [12000.00, 4500.00, 80000.00, 9000.00],
        })
        return {"bank": bank, "gl": gl}

    if rule_id == "R3":
        orders = pd.DataFrame({
            "order_id": ["O-1", "O-2", "O-3"],
            "amount":   [500.00, 750.00, 1000.00],
        })
        splits = pd.DataFrame({
            "posting_id": ["P1", "P2", "P3", "P4", "P5", "P6"],
            "amount":     [100.00, 250.00, 150.00, 750.00, 400.00, 200.00],
        })
        return {"oms_pos": orders, "wallet": splits}
    return {}


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
    options=list(RULE_UI.keys()),
    format_func=lambda r: RULE_UI[r]["label"],
)
st.markdown(f"> {wrapper.describe(rule_id).splitlines()[-1].strip()}")

roles = RULE_UI[rule_id]["roles"]
use_sample = st.toggle("Use built-in sample data", value=True,
                       help="Turn off to upload your own CSVs.")
samples = sample_data(rule_id) if use_sample else {}

# --- Per-role inputs --------------------------------------------------------
sources: dict[str, DataSource] = {}
ready = True
cols = st.columns(len(roles))

for (role, meta), col in zip(roles.items(), cols):
    with col:
        st.subheader(role)
        st.caption(meta["hint"])
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
        if meta["narration"]:
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
