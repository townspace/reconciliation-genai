"""
Phase 4 — Mode 3: rate validation (R11, R13).

Asserts expected charges are computed from the rate master and compared per row:
on-rate rows match, off-rate deductions are flagged, and a missing rate is a break.
"""

from base import DataSource
from samples import sample_data
from wrapper import ReconWrapper


def _r13_sources():
    f = sample_data("R13")
    return {
        "settlement": DataSource("settlement", f["settlement"], "txn_id", "actual_charge"),
        "rate_master": DataSource("rate_master", f["rate_master"], "charge_type", "rate_pct"),
    }


def test_r13_expected_computed_and_off_rate_flagged():
    result = ReconWrapper().run_rule("R13", _r13_sources())
    by_key = {r["recon_key"]: r for _, r in result.detail.iterrows()}

    assert by_key["S1"]["status"] == "MATCHED"            # 1000 x 2% = 20 == 20
    assert by_key["S1"]["expected_amount"] == 20.00
    assert by_key["S3"]["status"] == "MATCHED"            # 2000 x 2% = 40 == 40

    assert by_key["S2"]["status"] == "AMOUNT_MISMATCH"    # exp 75, actual 90
    assert by_key["S2"]["expected_amount"] == 75.00
    assert by_key["S4"]["status"] == "AMOUNT_MISMATCH"    # exp 0, actual 5


def test_r13_missing_rate_is_a_break():
    result = ReconWrapper().run_rule("R13", _r13_sources())
    by_key = {r["recon_key"]: r for _, r in result.detail.iterrows()}
    assert by_key["S5"]["status"] == "AMOUNT_MISMATCH"
    assert "no rate" in by_key["S5"]["break_reason"].lower()


def test_r13_summary():
    s = ReconWrapper().run_rule("R13", _r13_sources()).summary
    assert s["matched"] == 2          # S1, S3
    assert s["off_rate"] == 2         # S2, S4
    assert s["rate_not_found"] == 1   # S5
    assert s["total_breaks"] == 3


def test_r11_commission_validation():
    f = sample_data("R11")
    sources = {
        "channel_partner": DataSource("channel_partner", f["channel_partner"],
                                      "txn_id", "actual_commission"),
        "commission_master": DataSource("commission_master", f["commission_master"],
                                        "merchant_id", "rate_pct"),
    }
    result = ReconWrapper().run_rule("R11", sources)
    by_key = {r["recon_key"]: r["status"] for _, r in result.detail.iterrows()}
    assert by_key["M1"] == "MATCHED"          # 1000 x 5% = 50
    assert by_key["M2"] == "AMOUNT_MISMATCH"  # exp 60, actual 75
    assert by_key["M3"] == "MATCHED"          # 500 x 5% = 25
    assert by_key["M4"] == "AMOUNT_MISMATCH"  # no rate for MERCH_X


def test_rate_map_override_remaps_columns():
    # Simulate the UI passing remapped column names for uploaded data.
    f = sample_data("R13")
    sources = _r13_sources()
    over = {"base_column": "base_amount", "lookup_key": "charge_type", "rate_is_pct": True}
    result = ReconWrapper().run_rule("R13", sources, rate_map=over)
    assert result.summary["matched"] == 2
