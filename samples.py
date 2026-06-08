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

    if rule_id == "R8":
        # window=2d, fee allowance ~3%. Covers matched / timing / fee / genuine
        # break / one-sided.
        pg_txn = pd.DataFrame({
            "txn_id":     ["T1", "T2", "T3", "T4", "T5"],
            "gross_amount": [1000.00, 500.00, 750.00, 1200.00, 300.00],
            "txn_date":   ["2026-06-01", "2026-06-01", "2026-06-02",
                           "2026-06-03", "2026-06-04"],
        })
        pg_settlement = pd.DataFrame({
            "txn_id":     ["T1", "T2", "T3", "T4", "T6"],
            "net_amount": [980.00, 500.00, 750.00, 1000.00, 450.00],
            "settle_date": ["2026-06-02", "2026-06-03", "2026-06-02",
                            "2026-06-03", "2026-06-05"],
        })
        return {"pg_txn": pg_txn, "pg_settlement": pg_settlement}

    if rule_id == "R7":
        # window=3d, fee allowance ~2.5%.
        wallet_internal = pd.DataFrame({
            "wallet_txn_id": ["W1", "W2", "W3", "W4", "W5"],
            "amount":        [2000.00, 1000.00, 500.00, 800.00, 250.00],
            "date":          ["2026-06-01", "2026-06-01", "2026-06-02",
                              "2026-06-02", "2026-06-04"],
        })
        wallet_provider = pd.DataFrame({
            "wallet_txn_id": ["W1", "W2", "W3", "W4"],
            "amount":        [1960.00, 1000.00, 500.00, 600.00],
            "date":          ["2026-06-02", "2026-06-04", "2026-06-02",
                              "2026-06-02"],
        })
        return {"wallet_internal": wallet_internal, "wallet_provider": wallet_provider}

    if rule_id == "R13":
        settlement = pd.DataFrame({
            "txn_id":       ["S1", "S2", "S3", "S4", "S5"],
            "base_amount":  [1000.00, 5000.00, 2000.00, 800.00, 1500.00],
            "charge_type":  ["STD", "PREMIUM", "STD", "PROMO", "UNKNOWN"],
            "actual_charge": [20.00, 90.00, 40.00, 5.00, 30.00],
        })
        # expected: STD 2% , PREMIUM 1.5%, PROMO 0%. S2/S4 off-rate; S5 no rate.
        rate_master = pd.DataFrame({
            "charge_type": ["STD", "PREMIUM", "PROMO"],
            "rate_pct":    [2.0, 1.5, 0.0],
        })
        return {"settlement": settlement, "rate_master": rate_master}

    if rule_id == "R11":
        channel_partner = pd.DataFrame({
            "txn_id":            ["M1", "M2", "M3", "M4"],
            "base_amount":       [1000.00, 2000.00, 500.00, 900.00],
            "merchant_id":       ["MERCH_A", "MERCH_B", "MERCH_A", "MERCH_X"],
            "actual_commission": [50.00, 75.00, 25.00, 45.00],
        })
        # expected: MERCH_A 5%, MERCH_B 3%. M2 off-rate; M4 no rate.
        commission_master = pd.DataFrame({
            "merchant_id": ["MERCH_A", "MERCH_B"],
            "rate_pct":    [5.0, 3.0],
        })
        return {"channel_partner": channel_partner,
                "commission_master": commission_master}

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
