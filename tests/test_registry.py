"""
Phase 1 — registry + mode dispatcher tests.

Asserts that rules are declarative records routed through MODE_ENGINES, that the
three migrated rules keep their behaviour, and that adding a rule for an existing
mode needs only a registry entry (no engine code).
"""

import pandas as pd

import registry
from base import DataSource, ReconType
from registry import FeedSpec, RuleSpec, dispatch
from samples import sample_data
from wrapper import ReconWrapper


def _sources(rid):
    s = sample_data(rid)
    spec = ReconWrapper().rules[rid]
    keymap = {"oms_pos": "order_id", "wallet": "order_id",
              "bank": "bank_ref", "gl": "journal_id"}
    amtmap = {"oms_pos": "wallet_amount_utilized", "wallet": "transaction_amount",
              "bank": "amount", "gl": "amount"}
    if rid == "R3":
        keymap = {"oms_pos": "order_id", "wallet": "posting_id"}
        amtmap = {"oms_pos": "amount", "wallet": "amount"}
    out = {}
    for f in spec.feeds:
        df = s[f.role]
        narr = "narration" if (f.narration and "narration" in df.columns) else (
            "description" if f.narration else None)
        out[f.role] = DataSource(f.role, df, keymap[f.role], amtmap[f.role],
                                 narration_column=narr)
    return out


def test_rules_are_registered_specs():
    specs = registry.all_specs()
    assert set(["R1", "R2", "R3"]).issubset(specs)
    assert specs["R1"].mode == "exact_key"
    assert specs["R2"].mode == "semantic"
    assert specs["R3"].mode == "one_to_many"
    assert specs["R1"].required_roles == ["oms_pos", "wallet"]


def test_dispatch_matches_each_mode():
    w = ReconWrapper()
    assert w.run_rule("R1", _sources("R1")).summary["matched"] == 2
    assert w.run_rule("R2", _sources("R2")).summary["semantic_matched"] == 3
    assert w.run_rule("R3", _sources("R3")).summary["one_to_many_matched"] == 2


def test_unknown_mode_is_rejected_at_registration():
    bad = RuleSpec(id="RX", label="x", description="x", mode="does_not_exist",
                   recon_key="k", feeds=[FeedSpec("a"), FeedSpec("b")])
    try:
        registry.register(bad)
        assert False, "expected ValueError for unknown mode"
    except ValueError:
        pass


def test_adding_exact_key_rule_needs_only_a_registry_entry():
    # Register a throwaway exact_key rule purely from config, run it, clean up.
    rid = "RTEST"
    registry.register(RuleSpec(
        id=rid, label="RTEST", description="ad-hoc exact rule", mode="exact_key",
        recon_key="k", feeds=[FeedSpec("left"), FeedSpec("right")]))
    try:
        left = pd.DataFrame({"k": ["1", "2"], "v": [10.0, 20.0]})
        right = pd.DataFrame({"k": ["1", "2"], "v": [10.0, 99.0]})
        sources = {"left": DataSource("left", left, "k", "v"),
                   "right": DataSource("right", right, "k", "v")}
        res = dispatch(registry.RULES[rid], sources)
        assert res.summary["matched"] == 1
        assert res.summary["amount_mismatch"] == 1
    finally:
        registry.RULES.pop(rid, None)


def test_missing_source_raises():
    w = ReconWrapper()
    try:
        w.run_rule("R1", {"oms_pos": _sources("R1")["oms_pos"]})  # missing 'wallet'
        assert False, "expected ValueError for missing source"
    except ValueError:
        pass
