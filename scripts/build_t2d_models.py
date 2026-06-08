"""Driver: build the full Phase-3 T2D modelling layer on real + synthetic data.

Runs 1a (real floor) -> 1b (synthetic structural ablation) -> pre-register the bar -> the LSTM
sequence model -> attribution -> model card, writing every artifact under ``data/models/t2d/``
(git-ignored). Prints a concise reproducible summary. The pan-indication ``data/models/``
artifacts are never touched.

    uv run python scripts/build_t2d_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.t2d.pipeline import run_from_disk  # noqa: E402


def _fmt(v: Any) -> str:
    return f"{v:.4f}" if isinstance(v, float) and np.isfinite(v) else str(v)


def _print_stratum(
    title: str, per_stratum: dict[str, dict[str, dict[str, float]]]
) -> None:
    for sname, levels in per_stratum.items():
        print(f"  {title} by {sname}:")
        for level, panel in levels.items():
            print(
                f"    {level:<22} PR-AUC={_fmt(panel.get('pr_auc'))} "
                f"recall@p50={_fmt(panel.get('recall_at_p50'))} "
                f"Brier={_fmt(panel.get('brier'))} n={panel.get('n')}"
            )


def main() -> int:
    res = run_from_disk()
    sep = "=" * 78
    print(sep)
    print("Vigil Phase-3 T2D modelling layer")
    print(sep)
    print(f"held-out axis: {res['held_out_axis']}")
    print("-" * 78)

    real = res["real_1a"]["test"]
    print("TASK 1a — REAL T2D structural floor (test):")
    g = real["gbt"]
    print(
        f"  GBT   MAE={_fmt(g['mae'])} RMSE={_fmt(g['rmse'])} R2={_fmt(g['r2'])} "
        f"PR-AUC={_fmt(g['pr_auc'])} recall@p50={_fmt(g['recall_at_p50'])} Brier={_fmt(g['brier'])}"
    )
    print(
        f"  Logit PR-AUC={_fmt(real['logit']['pr_auc'])} Brier={_fmt(real['logit']['brier'])}"
    )
    _print_stratum("1a GBT", real["per_stratum_gbt"])
    print("-" * 78)

    sb = res["synthetic_1b"]["test"]
    print("TASK 1b — SYNTHETIC structural-ONLY ablation (test) [THE BAR]:")
    s = sb["gbt"]
    print(
        f"  GBT   PR-AUC={_fmt(s['pr_auc'])} recall@p50={_fmt(s['recall_at_p50'])} "
        f"Brier={_fmt(s['brier'])} n={s['n']} pos={s['positives']}"
    )
    print(
        f"  Logit PR-AUC={_fmt(sb['logit']['pr_auc'])} Brier={_fmt(sb['logit']['brier'])}"
    )
    _print_stratum("1b GBT", sb["per_stratum_gbt"])
    print("-" * 78)

    pre = res["preregistration"]
    print("TASK 2 — PRE-REGISTERED BAR (written before the fit):")
    print(
        f"  PR-AUC must reach >= {pre['criteria']['pr_auc']['must_reach']} "
        f"(1b={pre['criteria']['pr_auc']['baseline']} + {pre['criteria']['pr_auc']['margin']})"
    )
    print(
        f"  median lead-time must reach >= {pre['criteria']['lead_time']['must_reach']} visits"
    )
    print("-" * 78)

    panel = res["sequence_panel"]
    seq = panel["test"]["overall"]
    print("TASK 3 — SEQUENCE LSTM (synthetic, test):")
    print(
        f"  PR-AUC={_fmt(seq['pr_auc'])} recall@p50={_fmt(seq['recall_at_p50'])} "
        f"Brier={_fmt(seq['brier'])} decision_points={panel['n_test_decision_points']}"
    )
    _print_stratum("3 seq", panel["test"]["per_stratum"])
    lt = panel["lead_time"]
    print(
        f"  lead-time: median={_fmt(lt['median_lead_time_visits'])} "
        f"mean={_fmt(lt['mean_lead_time_visits'])} visits "
        f"(flagged {lt['n_flagged']}/{lt['n_test_droppers']} droppers)"
    )
    print("-" * 78)
    bar = res["bar"]
    print("BAR VERDICT vs 1b:")
    print(
        f"  PR-AUC  {_fmt(bar['sequence_test_pr_auc'])} >= {bar['must_reach_pr_auc']} "
        f"-> {'PASS' if bar['pr_auc_pass'] else 'FAIL'}"
    )
    print(
        f"  lead    {_fmt(bar['sequence_median_lead_time_visits'])} >= {bar['must_reach_lead_time']} "
        f"-> {'PASS' if bar['lead_time_pass'] else 'FAIL'}"
    )
    print(f"  OVERALL -> {'PASS' if bar['overall_pass'] else 'FAIL'}")
    print("-" * 78)
    print(f"artifacts written under: {res['out_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
