"""
demo.py
-------
Runs R1 through the wrapper on small synthetic feeds that deliberately contain
every break type: a clean match, an amount mismatch, a record missing from the
wallet report, and a record missing from the OMS/POS report.
"""

import pandas as pd

from base import DataSource, ReconType
from wrapper import ReconWrapper

# --- Synthetic OMS / POS report ---------------------------------------------
# wallet_amount_utilized = amount the customer paid via wallet for the order
oms_pos = pd.DataFrame({
    "order_id":               ["A101", "A102", "A103", "A104"],
    "wallet_amount_utilized": [500.00,  250.00,  120.00,  999.00],
    "store_id":               ["S1",    "S1",    "S2",    "S3"],
})

# --- Synthetic Internal wallet report ---------------------------------------
# transaction_amount = wallet debit recorded internally for the order
wallet = pd.DataFrame({
    "order_id":           ["A101", "A102", "A103", "A105"],
    "transaction_amount": [500.00,  245.00,  120.00,  300.00],
    "wallet_txn_id":      ["W1",    "W2",    "W3",    "W5"],
})

# Map raw feeds to the roles R1 expects, naming key + amount columns.
sources = {
    "oms_pos": DataSource(
        role="oms_pos", df=oms_pos,
        key_column="order_id", amount_column="wallet_amount_utilized",
    ),
    "wallet": DataSource(
        role="wallet", df=wallet,
        key_column="order_id", amount_column="transaction_amount",
    ),
}

wrapper = ReconWrapper()

print("=" * 70)
print("Registered rules (transactional):", wrapper.list_rules(ReconType.TRANSACTIONAL))
print(wrapper.describe("R1"))
print("=" * 70)

result = wrapper.run_rule("R1", sources)

print("\nFull detail (every order, with status):")
print(result.detail.to_string(index=False))

print("\nBreaks only (what an analyst must action):")
print(result.breaks.to_string(index=False))

print("\nSummary:")
for k, v in result.summary.items():
    print(f"  {k:24} {v}")

print("\nClean reconciliation?", result.is_clean())
print("\nWrapper summary table:")
print(wrapper.summary_table({"R1": result}).to_string(index=False))
