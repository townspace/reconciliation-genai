"""
samples.py
----------
Built-in sample feeds for each rule, so the app and the test suite share a
single source of truth. Pure pandas — no Streamlit or AI dependency — so it is
safe to import from tests.

`sample_data(rule_id)` returns a dict of {role: DataFrame} matching the roles
that rule expects (see RULE_UI in app.py).
"""

from __future__ import annotations

import pandas as pd


def sample_data(rule_id: str) -> dict:
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
