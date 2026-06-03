"""
demo_genai.py
-------------
End-to-end showcase of the GenAI layer. Runs fully offline (deterministic
heuristics); set OPENAI_API_KEY to have OpenAI sharpen every step
(ANTHROPIC_API_KEY also works for Claude).

Covers, in order:
  1. AI enrichment of an exact recon (R1): root cause + commentary + confidence,
     draft adjustment journals, anomaly flags.
  2. Semantic matching (R2): bank narrations vs GL descriptions with no shared key.
  3. One-to-many discovery (R3): one order settled by several split postings.
  4. AI source/rule auto-detection from raw feeds.
"""

import pandas as pd

from base import DataSource, ReconType
from wrapper import ReconWrapper, build_config_with_ai
from ai import default_client

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

client = default_client()
MODE = (f"LIVE ({client.provider})" if getattr(client, "live", False)
        else "OFFLINE (heuristics)")
wrapper = ReconWrapper()

print("=" * 78)
print(f"GenAI reconciliation demo — running in {MODE} mode  [model: {client.model}]")
print("Registered rules:", ", ".join(wrapper.list_rules()))
print("=" * 78)


# ---------------------------------------------------------------------------
# 1. AI enrichment of an exact recon (R1)
# ---------------------------------------------------------------------------
print("\n[1] EXACT RECON (R1) + AI ENRICHMENT")
print("-" * 78)
oms_pos = pd.DataFrame({
    "order_id":               ["A101", "A102", "A103", "A104", "A106"],
    "wallet_amount_utilized": [500.00,  250.00,  120.00,  999.00, 50000.00],
    "store_id":               ["S1",    "S1",    "S2",    "S3",    "S1"],
})
wallet = pd.DataFrame({
    "order_id":           ["A101", "A102", "A103", "A105"],
    "transaction_amount": [500.00,  245.00,  120.00,  300.00],
    "wallet_txn_id":      ["W1",    "W2",    "W3",    "W5"],
})
sources = {
    "oms_pos": DataSource("oms_pos", oms_pos, "order_id", "wallet_amount_utilized"),
    "wallet":  DataSource("wallet",  wallet,  "order_id", "transaction_amount"),
}

result = wrapper.run_rule("R1", sources, enrich=True,
                          group_column=None)  # store_id not on breaks frame

print("\nBreaks with AI root cause:")
cols = ["recon_key", "status", "difference", "ai_category", "ai_confidence", "ai_commentary"]
print(result.breaks[cols].to_string(index=False))

print(f"\nOverall AI commentary: {result.ai_commentary}")
print(f"Mean break confidence: {result.confidence}")

print("\nDraft adjustment journals (ERP-ready):")
jcols = ["recon_key", "dr_account", "cr_account", "amount", "narrative"]
print(result.journals[jcols].to_string(index=False))

print("\nAnomalies flagged (outlier magnitude):")
if len(result.anomalies):
    print(result.anomalies[["recon_key", "difference", "anomaly_score",
                            "anomaly_note"]].to_string(index=False))
else:
    print("  (none)")


# ---------------------------------------------------------------------------
# 2. Semantic matching (R2) — bank narration vs GL description, no shared key
# ---------------------------------------------------------------------------
print("\n\n[2] SEMANTIC RECON (R2) — narration-based fuzzy matching")
print("-" * 78)
bank = pd.DataFrame({
    "bank_ref":  ["BNK-1", "BNK-2", "BNK-3", "BNK-4"],
    # every line carries a payment-rail prefix (boilerplate IDF suppresses)
    "narration": ["NEFT ACME CORP 88123",
                  "NEFT GLOBEX LTD REF4471",
                  "NEFT INITECH PAYROLL JUN",
                  "NEFT STARK INDS ADVANCE"],   # no GL counterpart -> break
    "amount":    [12000.00, 4500.00, 80000.00, 250.00],
})
gl = pd.DataFrame({
    "journal_id":  ["J-900", "J-901", "J-902", "J-903"],
    "description": ["ACME CORP invoice 88123",   # reorder + extra word vs bank
                    "Globex Limited ref 4471",   # Ltd->Limited, spacing
                    "Initech payroll June",       # case + month spelt out
                    "Wayne Enterprises misc"],    # no bank counterpart -> break
    "amount":      [12000.00, 4500.00, 80000.00, 9000.00],
})
sem_sources = {
    "bank": DataSource("bank", bank, "bank_ref", "amount", narration_column="narration"),
    "gl":   DataSource("gl",   gl,   "journal_id", "amount", narration_column="description"),
}
r2 = wrapper.run_rule("R2", sem_sources, enrich=True)
print("\nDetail (note SEMANTIC_MATCH rows matched without a shared key):")
show = ["recon_key", "status", "match_method", "match_confidence", "difference"]
print(r2.detail[show].to_string(index=False))
print(f"\nSemantic matches: {r2.summary.get('semantic_matched')}  | "
      f"breaks: {r2.summary.get('total_breaks')}  | "
      f"mean match confidence: {r2.confidence}")
if len(r2.breaks):
    print("\nRemaining break(s) with AI root cause:")
    print(r2.breaks[["recon_key", "status", "ai_category", "ai_commentary"]]
          .to_string(index=False))


# ---------------------------------------------------------------------------
# 3. One-to-many discovery (R3) — split settlements summing to one order
# ---------------------------------------------------------------------------
print("\n\n[3] ONE-TO-MANY RECON (R3) — combinatorial match discovery")
print("-" * 78)
orders = pd.DataFrame({
    "order_id": ["O-1", "O-2", "O-3"],
    "amount":   [500.00, 750.00, 1000.00],
})
splits = pd.DataFrame({
    "posting_id": ["P1", "P2", "P3", "P4", "P5", "P6"],
    "amount":     [100.00, 250.00, 150.00,   # -> sum 500 == O-1
                   750.00,                    # -> O-2
                   400.00, 200.00],           # 600, no order -> leftover
})
om_sources = {
    "oms_pos": DataSource("oms_pos", orders, "order_id", "amount"),
    "wallet":  DataSource("wallet",  splits, "posting_id", "amount"),
}
r3 = wrapper.run_rule("R3", om_sources)
print("\nDetail (ONE_TO_MANY_MATCH shows the member postings that sum to the order):")
show3 = ["recon_key", "status", "matched_members", "group_size", "difference"]
print(r3.detail[show3].to_string(index=False))
print(f"\nOne-to-many matched: {r3.summary.get('one_to_many_matched')}  | "
      f"breaks: {r3.summary.get('total_breaks')}")


# ---------------------------------------------------------------------------
# 4. AI source/rule auto-detection from raw feeds
# ---------------------------------------------------------------------------
print("\n\n[4] AI SOURCE / RULE AUTO-DETECTION")
print("-" * 78)
raw_feeds = {
    "feed_one.csv": pd.DataFrame({
        "Value Date": ["2026-06-01"], "Narration": ["NEFT ACME"],
        "Debit": [0.0], "Credit": [12000.0], "Balance": [52000.0],
    }),
    "feed_two.csv": pd.DataFrame({
        "Journal ID": ["J-900"], "GL Account": ["2100"],
        "Description": ["Acme invoice"], "Amount": [12000.0],
    }),
}
detection = build_config_with_ai(raw_feeds, client=client)
print("\nDetected feeds:")
for f in detection["feeds"]:
    print(f"  {f['name']:14} -> type={f['source_type']:8} "
          f"key={f['key_column']} amount={f['amount_column']} "
          f"narration={f['narration_column']}")
print(f"\nRecommended rule:     {detection['recommended_rule']}")
print(f"Recommended strategy: {detection['recommended_strategy']}")
print(f"Notes: {detection['notes']}")

print("\n" + "=" * 78)
print("Demo complete. Set OPENAI_API_KEY to run the same flow with OpenAI.")
print("=" * 78)
