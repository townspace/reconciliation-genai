"""
Phase 7 — orchestration / end-to-end trace.

Runs the golden-path spine (R2 -> R8 -> R13 -> R12) and asserts the stage summary
reflects the four-lane structure and the trace attributes each flow's break to the
correct hop.
"""

from pipeline import LANES, Pipeline, golden_path


def _run():
    pipe, sources, rate_maps, flows = golden_path()
    result = pipe.run(sources, rate_map_per_stage=rate_maps)
    trace = pipe.trace(result, flows)
    return pipe, result, trace


def test_all_stages_run():
    _pipe, result, _trace = _run()
    assert set(result.results) == {"semantic-match", "settlement-timing",
                                   "charge-validation", "bank-reco"}
    # every stage produced a populated detail frame
    for res in result.results.values():
        assert len(res.detail) > 0


def test_stage_summary_reflects_lanes():
    _pipe, result, _trace = _run()
    lanes_seen = list(result.stage_summary["lane"])
    assert set(lanes_seen).issubset(set(LANES))
    # lanes are ordered along the OMS -> bank spine
    assert lanes_seen == sorted(lanes_seen, key=lambda x: LANES.index(x))
    assert {"recon-1", "recon-2", "bank"}.issubset(set(lanes_seen))


def test_trace_attributes_break_to_first_hop():
    _pipe, _result, trace = _run()
    fb = dict(zip(trace["flow_id"], trace["first_break"]))
    assert fb["FLOW-1 (clean)"] == "(clean)"
    assert fb["FLOW-2 (breaks at settlement)"] == "settlement-timing"
    assert fb["FLOW-3 (breaks at charge)"] == "charge-validation"
    assert fb["FLOW-4 (breaks at bank)"] == "bank-reco"


def test_trace_records_per_stage_status():
    _pipe, _result, trace = _run()
    row = trace[trace["flow_id"] == "FLOW-3 (breaks at charge)"].iloc[0]
    # fee difference at the settlement hop is NOT a break, so the trace continues
    assert row["settlement-timing"] == "FEE_DIFFERENCE"
    assert row["charge-validation"] == "AMOUNT_MISMATCH"


def test_clean_flow_has_no_break():
    _pipe, _result, trace = _run()
    row = trace[trace["flow_id"] == "FLOW-1 (clean)"].iloc[0]
    assert row["semantic-match"] == "SEMANTIC_MATCH"
    assert row["settlement-timing"] == "MATCHED"
    assert row["bank-reco"] == "AGGREGATE_MATCH"
    assert row["first_break"] == "(clean)"
