"""Pan-indication GBT PR-AUC decomposition by therapeutic indication.

Read-only analysis — no PHI, build-time only, no commit.

Steps:
1. Load ref_trial / ref_arm, apply modelling cohort filter.
2. Map nct_ids to indication buckets from browse_conditions.ndjson.
3. Report pan-indication GBT PR-AUC (train or reuse existing metrics.json).
4. Compute within-indication PR-AUC for the top 8 indications by usable-arm count.
5. Write data/models/decomposition/indication_pr_auc.json + decomposition_note.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import average_precision_score

# --- repo root on sys.path so local imports work -----------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingestion.config import CLEAN_ROOT  # noqa: E402
from models.cohort import modelling_cohort, cohort_nct_ids  # noqa: E402
from models.config import MODEL_SEED, MODELS_ROOT  # noqa: E402
from models.features.contract import (  # noqa: E402
    ContractTransformer,
    assemble_rows,
    build_matrices,
)
from models.leakage_check import run_smoke  # noqa: E402
from models.splits import temporal_group_split  # noqa: E402

RAW_ROOT = REPO_ROOT / "data" / "raw" / "aact" / "2026-06-05"
BROWSE_CONDITIONS = RAW_ROOT / "browse_conditions.ndjson"
DECOMP_ROOT = REPO_ROOT / "data" / "models" / "decomposition"
BASELINES_METRICS = MODELS_ROOT / "baselines" / "metrics.json"

# ---------------------------------------------------------------------------
# Indication bucket definitions — priority order (first match wins)
# ---------------------------------------------------------------------------
INDICATION_RULES: list[tuple[str, list[str]]] = [
    ("T2D", ["type 2 diabetes", "t2d", "diabetes mellitus, type 2"]),
    ("RA", ["rheumatoid arthritis", " ra "]),
    ("PSO", ["psoriasis"]),  # "psoriatic arthritis" handled via negative filter below
    ("MDD", ["major depressive disorder", "mdd", "depression"]),
    ("ALZ", ["alzheimer"]),
    ("HF", ["heart failure", "cardiac failure"]),
    ("MS", ["multiple sclerosis"]),
    ("IBD", ["crohn", "inflammatory bowel", "ulcerative colitis", "ibd"]),
]

# PSO exclusion: if the mesh_term contains "psoriatic arthritis" it should NOT match PSO
_PSO_EXCLUDE = "psoriatic arthritis"


def _classify_term(term: str) -> str | None:
    """Return indication bucket for a single mesh_term, or None for no match."""
    low = term.lower()
    for bucket, keywords in INDICATION_RULES:
        for kw in keywords:
            if kw in low:
                # Special exclusion: 'psoriasis' matches should skip if it's psoriatic arthritis
                if bucket == "PSO" and _PSO_EXCLUDE in low:
                    continue
                return bucket
    return None


def build_indication_map(nct_ids: frozenset[str]) -> dict[str, str]:
    """Return {nct_id -> indication} for nct_ids in the modelling cohort.

    Priority: first matching rule in INDICATION_RULES order. nct_ids with no match
    are mapped to 'OTHER' (excluded from per-indication analysis).
    """
    # Collect all (nct_id, term) pairs for cohort nct_ids only
    nct_bucket: dict[str, str] = {}
    with BROWSE_CONDITIONS.open(encoding="utf-8") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            rec = json.loads(raw_line)
            nct_id = rec.get("nct_id", "")
            if nct_id not in nct_ids:
                continue
            term = rec.get("mesh_term", "") or ""
            bucket = _classify_term(term)
            if bucket is None:
                continue
            # Priority: keep bucket only if it would rank earlier in INDICATION_RULES
            if nct_id not in nct_bucket:
                nct_bucket[nct_id] = bucket
            else:
                existing = nct_bucket[nct_id]
                # lower index = higher priority
                bucket_rank = {b: i for i, (b, _) in enumerate(INDICATION_RULES)}
                if bucket_rank[bucket] < bucket_rank[existing]:
                    nct_bucket[nct_id] = bucket

    # Fill unmapped with OTHER
    result: dict[str, str] = {}
    for nct_id in nct_ids:
        result[nct_id] = nct_bucket.get(nct_id, "OTHER")
    return result


def _safe_pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
    """Return average_precision_score or None when only one class is present."""
    classes = np.unique(y_true)
    if len(classes) < 2:
        return None
    return float(average_precision_score(y_true, y_score))


# Percentile-bootstrap CI config. Fixed seed for reproducibility; n_resamples stated in output.
# QUANTIFY-ONLY: this adds an honest range around the existing point estimate — it never changes
# the point estimate, and the CI is reported exactly as it comes out (no seed/n cherry-picking).
BOOTSTRAP_N_RESAMPLES = 2000
BOOTSTRAP_SEED = MODEL_SEED


def _bootstrap_pr_auc_ci(
    y_bin: np.ndarray,
    y_pred: np.ndarray,
    n_resamples: int = BOOTSTRAP_N_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Percentile bootstrap 95% CI for PR-AUC by resampling the TEST fold with replacement.

    Resamples that collapse to a single class (no positives or no negatives) are skipped — PR-AUC
    is undefined there — and counted in ``n_valid_resamples`` so the CI's own stability is
    auditable. Small test folds (e.g. ALZ n_test=172) yield deliberately WIDE intervals; that is
    the honest point of this analysis, not a defect to be tuned away.
    """
    y_bin = np.asarray(y_bin)
    y_pred = np.asarray(y_pred)
    n = len(y_bin)
    rng = np.random.default_rng(seed)
    aps: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        if len(np.unique(y_bin[idx])) < 2:
            continue
        aps.append(float(average_precision_score(y_bin[idx], y_pred[idx])))
    if len(aps) < 2:
        return {
            "ci_lo": None,
            "ci_hi": None,
            "ci_n_resamples": n_resamples,
            "ci_n_valid_resamples": len(aps),
        }
    return {
        "ci_lo": float(np.percentile(aps, 2.5)),
        "ci_hi": float(np.percentile(aps, 97.5)),
        "ci_n_resamples": n_resamples,
        "ci_n_valid_resamples": len(aps),
    }


_CI_NULL = {
    "ci_lo": None,
    "ci_hi": None,
    "ci_n_resamples": BOOTSTRAP_N_RESAMPLES,
    "ci_n_valid_resamples": 0,
}


def _clip01(arr: np.ndarray) -> np.ndarray:
    return np.clip(arr.astype(float), 0.0, 1.0)


def get_pan_indication_pr_auc(
    ref_trial: pd.DataFrame,
    ref_arm: pd.DataFrame,
) -> float:
    """Return pooled TEST GBT PR-AUC, reading metrics.json if available else training fresh."""
    if BASELINES_METRICS.exists():
        data = json.loads(BASELINES_METRICS.read_text(encoding="utf-8"))
        pr_auc = data["test"]["gbt"]["pr_auc"]
        print(
            f"[pan-indication] Loaded existing metrics.json: GBT TEST PR-AUC = {pr_auc:.4f}"
        )
        return float(pr_auc)

    # Metrics don't exist — train now
    print("[pan-indication] metrics.json not found — training pan-indication GBT...")
    from models.baselines import train_baselines_from_frames  # noqa: PLC0415

    results = train_baselines_from_frames(
        ref_trial,
        ref_arm,
        out_root=MODELS_ROOT / "baselines",
        shap_sample=200,
    )
    pr_auc = results["test"]["gbt"]["pr_auc"]
    print(f"[pan-indication] Trained. GBT TEST PR-AUC = {pr_auc:.4f}")
    return float(pr_auc)


def within_indication_pr_auc(
    indication: str,
    ind_nct_ids: set[str],
    cohort: pd.DataFrame,
    ref_arm: pd.DataFrame,
) -> dict[str, Any]:
    """Fit a fresh GBT within one indication subset; return metrics dict."""
    sub_trial = cohort[cohort["nct_id"].isin(ind_nct_ids)].copy()
    sub_arm = ref_arm[ref_arm["nct_id"].isin(ind_nct_ids)].copy()

    n_trials = len(sub_trial)
    n_arms = len(sub_arm)

    result_base: dict[str, Any] = {
        "indication": indication,
        "n_trials": n_trials,
        "n_arms": n_arms,
        "test_pr_auc": None,
        "split_type": None,
        "n_train_arms": 0,
        "n_test_arms": 0,
        **_CI_NULL,
    }

    # Need started > 0 and completed not null — apply same filter used by baselines
    sub_arm = sub_arm[(sub_arm["started"] > 0) & sub_arm["completed"].notna()].copy()
    n_arms = len(sub_arm)
    result_base["n_arms"] = n_arms

    if n_trials < 2 or n_arms < 4:
        print(
            f"  [{indication}] Skipping: too few trials ({n_trials}) or arms ({n_arms})"
        )
        result_base["split_type"] = "skipped-too-small"
        return result_base

    # Decide split strategy
    if n_trials >= 9:
        # Try temporal 3-way split; fall back if any fold < 3 arms
        try:
            split = temporal_group_split(sub_trial)
            rows = assemble_rows(sub_trial, sub_arm)
            matrices = build_matrices(rows, split)
            run_smoke(matrices)

            train_m = matrices["train"]
            test_m = matrices["test"]

            if len(train_m.X) < 3 or len(test_m.X) < 3:
                raise ValueError("fold too small")

            split_type = "temporal"
        except Exception as exc:
            print(
                f"  [{indication}] Temporal split failed ({exc}), falling back to random 2-fold"
            )
            n_trials = len(sub_trial)  # recount after filter
            return _random_split_pr_auc(indication, sub_trial, sub_arm, result_base)
    else:
        print(
            f"  [{indication}] n_trials={n_trials} < 9 -> small-N random 2-fold fallback"
        )
        return _random_split_pr_auc(indication, sub_trial, sub_arm, result_base)

    # Fit GBT on TRAIN features from build_matrices
    threshold = float(np.median(train_m.y.to_numpy(dtype=float)))
    gbt = HistGradientBoostingRegressor(random_state=MODEL_SEED, max_iter=200)
    gbt.fit(train_m.X, train_m.y.to_numpy(dtype=float))

    y_test = test_m.y.to_numpy(dtype=float)
    y_bin = (y_test > threshold).astype(int)
    y_pred = _clip01(gbt.predict(test_m.X))

    pr_auc = _safe_pr_auc(y_bin, y_pred)
    ci = _bootstrap_pr_auc_ci(y_bin, y_pred) if pr_auc is not None else dict(_CI_NULL)

    result_base.update(
        {
            "test_pr_auc": pr_auc,
            "split_type": split_type,
            "n_train_arms": int(len(train_m.X)),
            "n_test_arms": int(len(test_m.X)),
            **ci,
        }
    )
    _ci_str = (
        f" CI[{ci['ci_lo']:.4f},{ci['ci_hi']:.4f}]"
        if ci["ci_lo"] is not None
        else " CI[n/a]"
    )
    print(
        f"  [{indication}] n_trials={n_trials} n_arms={n_arms} "
        f"split={split_type} PR-AUC={pr_auc if pr_auc is not None else 'N/A (1-class)'}{_ci_str}"
    )
    return result_base


def _random_split_pr_auc(
    indication: str,
    sub_trial: pd.DataFrame,
    sub_arm: pd.DataFrame,
    result_base: dict[str, Any],
) -> dict[str, Any]:
    """2-fold random train/test on a small-N indication; transformer fit on train only."""
    rng = np.random.default_rng(MODEL_SEED)
    nct_ids_arr = sub_trial["nct_id"].to_numpy()
    rng.shuffle(nct_ids_arr)
    n = len(nct_ids_arr)
    n_test = max(1, round(n * 0.2))
    test_ids = set(nct_ids_arr[-n_test:])
    train_ids = set(nct_ids_arr[:-n_test])

    train_arm = sub_arm[sub_arm["nct_id"].isin(train_ids)]
    test_arm = sub_arm[sub_arm["nct_id"].isin(test_ids)]

    if len(train_arm) < 2 or len(test_arm) < 1:
        result_base["split_type"] = "skipped-too-small"
        print(f"  [{indication}] Skipping: not enough arms for random split")
        return result_base

    # Build feature matrices manually for 2-fold (no val fold)
    all_trial = sub_trial.copy()
    all_arm = sub_arm.copy()
    rows = assemble_rows(all_trial, all_arm)

    # Assign folds manually
    rows = rows.copy()
    rows["fold"] = rows["nct_id"].apply(lambda x: "train" if x in train_ids else "test")

    transformer = ContractTransformer()
    train_rows = rows[rows["fold"] == "train"]
    test_rows = rows[rows["fold"] == "test"]

    if len(train_rows) < 2 or len(test_rows) < 1:
        result_base["split_type"] = "skipped-too-small"
        return result_base

    transformer.fit(train_rows, fold_name="train")
    X_train, _ = transformer.transform(train_rows)
    X_test, feature_names = transformer.transform(test_rows)

    from models.features.contract import FeatureMatrix, _TARGET, _GROUP  # noqa: PLC0415

    train_m = FeatureMatrix(
        X=X_train,
        y=train_rows[_TARGET].reset_index(drop=True),
        feature_names=feature_names,
        nct_id=train_rows["nct_id"].reset_index(drop=True),
        arm_id=train_rows["arm_id"].reset_index(drop=True),
        sponsor_class=train_rows[_GROUP].reset_index(drop=True),
        fold="train",
    )
    test_m = FeatureMatrix(
        X=X_test,
        y=test_rows[_TARGET].reset_index(drop=True),
        feature_names=feature_names,
        nct_id=test_rows["nct_id"].reset_index(drop=True),
        arm_id=test_rows["arm_id"].reset_index(drop=True),
        sponsor_class=test_rows[_GROUP].reset_index(drop=True),
        fold="test",
    )

    # Leakage check on 2-fold
    run_smoke({"train": train_m, "test": test_m})

    threshold = float(np.median(train_m.y.to_numpy(dtype=float)))
    gbt = HistGradientBoostingRegressor(random_state=MODEL_SEED, max_iter=200)
    gbt.fit(train_m.X, train_m.y.to_numpy(dtype=float))

    y_test = test_m.y.to_numpy(dtype=float)
    y_bin = (y_test > threshold).astype(int)
    y_pred = _clip01(gbt.predict(test_m.X))

    pr_auc = _safe_pr_auc(y_bin, y_pred)
    ci = _bootstrap_pr_auc_ci(y_bin, y_pred) if pr_auc is not None else dict(_CI_NULL)

    n_trials_used = len(sub_trial)
    n_arms_used = len(sub_arm)
    result_base.update(
        {
            "n_trials": n_trials_used,
            "n_arms": n_arms_used,
            "test_pr_auc": pr_auc,
            "split_type": "small-N random",
            "n_train_arms": int(len(train_m.X)),
            "n_test_arms": int(len(test_m.X)),
            **ci,
        }
    )
    print(
        f"  [{indication}] n_trials={n_trials_used} n_arms={n_arms_used} "
        f"split=small-N random PR-AUC={pr_auc if pr_auc is not None else 'N/A (1-class)'}"
    )
    return result_base


def write_decomposition_note(
    pan_pr_auc: float,
    by_indication: list[dict[str, Any]],
    out_dir: Path,
) -> None:
    """Write decomposition_note.md with table + interpretation."""
    table_rows = []
    for row in by_indication:
        pr_auc_str = (
            f"{row['test_pr_auc']:.4f}"
            if isinstance(row["test_pr_auc"], float)
            else "N/A"
        )
        ci_str = (
            f"[{row['ci_lo']:.4f}, {row['ci_hi']:.4f}]"
            if isinstance(row.get("ci_lo"), float)
            else "N/A"
        )
        table_rows.append(
            f"| {row['indication']:<8} | {row['n_trials']:>8} | {row['n_arms']:>6} "
            f"| {pr_auc_str:>12} | {ci_str:>20} | {row['split_type'] or '':>18} "
            f"| {row['n_train_arms']:>12} | {row['n_test_arms']:>10} |"
        )

    table_str = "\n".join(table_rows)
    pan_str = f"{pan_pr_auc:.4f}"

    content = f"""# Pan-indication GBT PR-AUC Decomposition

**Pan-indication (pooled) TEST GBT PR-AUC: {pan_str}**

## Per-indication PR-AUC (top 8 by arm count)

| Indication | n_trials | n_arms | test_pr_auc | 95% CI (bootstrap) | split_type | n_train_arms | n_test_arms |
|------------|----------|--------|-------------|--------------------|------------|--------------|-------------|
{table_str}

95% CIs are percentile bootstrap ({BOOTSTRAP_N_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}) over the
test fold. Small folds (e.g. ALZ n_test≈172) give WIDE intervals that overlap neighbours — the
honest reading is "directional, not a ranked order". The point estimates are unchanged; the CIs
only quantify their uncertainty.

## Interpretation: Between- vs Within-Indication Signal

The pan-indication pooled PR-AUC ({pan_str}) summarises how well the GBT separates high- from
low-dropout arms across the full modelling cohort (Phase 1/2 through Phase 3, all therapeutic
areas, all sponsor classes).

**If within-indication PR-AUC ≈ pan-indication PR-AUC** (or higher), the model captures
participant- and arm-level structure inside each indication — the predictive signal does not
merely reflect indication-level base rates. This is the preferred scenario for a retention
intelligence platform: the model generalises within a disease domain, not only across them.

**If within-indication PR-AUC is substantially below the pooled value**, much of the pooled
gain comes from the model learning which indications carry higher baseline dropout (an
indication-level intercept effect). The model would then perform well pooled but poorly when
deployed within a single indication — a critical operational limitation.

**Small-N caution**: indications with fewer than 9 trials use a random 2-fold split. With small
test sets the PR-AUC estimate is high-variance; treat those numbers as directional only.
A null result ("N/A") means the test fold contained only one class after thresholding.
"""
    (out_dir / "decomposition_note.md").write_text(content, encoding="utf-8")
    print(f"[output] decomposition_note.md written to {out_dir}")


def main() -> int:
    print("=" * 72)
    print("Vigil - Pan-indication GBT decomposition analysis")
    print("=" * 72)

    # 1. Load data
    ref_trial = pd.read_parquet(CLEAN_ROOT / "ref_trial.parquet")
    ref_arm = pd.read_parquet(CLEAN_ROOT / "ref_arm.parquet")
    print(f"Loaded ref_trial: {ref_trial.shape}, ref_arm: {ref_arm.shape}")

    # Apply modelling cohort filter
    cohort = modelling_cohort(ref_trial)
    nct_ids = cohort_nct_ids(ref_trial)
    # Apply arm filter: started > 0 and completed not null
    arms = ref_arm[ref_arm["nct_id"].isin(nct_ids)].copy()
    arms = arms[(arms["started"] > 0) & arms["completed"].notna()].copy()
    print(f"Modelling cohort: {len(cohort)} trials, {len(arms)} arms")

    # 2. Indication mapping
    print("\n[Step 2] Building indication map from browse_conditions.ndjson...")
    ind_map = build_indication_map(nct_ids)
    cohort = cohort.copy()
    cohort["indication"] = cohort["nct_id"].map(ind_map)
    arms = arms.copy()
    arms["indication"] = arms["nct_id"].map(ind_map)

    # Count per indication (arms in cohort)
    ind_arm_counts = (
        arms[arms["indication"] != "OTHER"]
        .groupby("indication")
        .agg(n_trials=("nct_id", "nunique"), n_arms=("arm_id", "count"))
        .reset_index()
        .sort_values("n_arms", ascending=False)
    )
    print("\nIndication counts (arms in modelling cohort, excl. OTHER):")
    print(ind_arm_counts.to_string(index=False))

    # 3. Pan-indication PR-AUC + base-rate-adjusted skill
    print("\n[Step 3] Pan-indication GBT PR-AUC...")
    pan_pr_auc = get_pan_indication_pr_auc(ref_trial, ref_arm)

    # Base-rate-adjusted skill = (PR-AUC - prevalence) / (1 - prevalence): the fraction of the
    # gap from the test base rate to 1.0 that the model closes. Reads the prevalence straight from
    # baselines/metrics.json so it stays script-derived, not hand-typed.
    pan_base_rate: float | None = None
    pan_skill: float | None = None
    if BASELINES_METRICS.exists():
        _bm = json.loads(BASELINES_METRICS.read_text(encoding="utf-8"))
        pan_base_rate = _bm.get("test", {}).get("positive_prevalence")
        if pan_base_rate is not None and pan_base_rate < 1.0:
            pan_skill = (pan_pr_auc - pan_base_rate) / (1.0 - pan_base_rate)
            print(
                f"[pan-indication] base rate={pan_base_rate:.4f} -> "
                f"base-rate-adjusted skill={pan_skill:.4f}"
            )

    # 4. Per-indication PR-AUC for top 8
    top8 = ind_arm_counts.head(8)["indication"].tolist()
    print(f"\n[Step 4] Within-indication PR-AUC for top 8: {top8}")

    by_indication: list[dict[str, Any]] = []
    for ind in top8:
        ind_nct_ids = set(cohort[cohort["indication"] == ind]["nct_id"])
        result = within_indication_pr_auc(ind, ind_nct_ids, cohort, arms)
        # Fill n_trials and n_arms from counts table
        row_counts = ind_arm_counts[ind_arm_counts["indication"] == ind]
        if not row_counts.empty:
            result["n_trials"] = int(row_counts["n_trials"].iloc[0])
            result["n_arms"] = int(row_counts["n_arms"].iloc[0])
        by_indication.append(result)

    # 5. Write outputs
    DECOMP_ROOT.mkdir(parents=True, exist_ok=True)

    json_out = {
        "pan_indication": pan_pr_auc,
        "pan_indication_base_rate": pan_base_rate,
        "pan_indication_skill_above_base_rate": pan_skill,
        "ci_method": "percentile bootstrap, 95%, resampling the test fold with replacement",
        "ci_n_resamples": BOOTSTRAP_N_RESAMPLES,
        "ci_seed": BOOTSTRAP_SEED,
        "by_indication": by_indication,
    }
    (DECOMP_ROOT / "indication_pr_auc.json").write_text(
        json.dumps(json_out, indent=2), encoding="utf-8"
    )
    print(f"\n[output] indication_pr_auc.json written to {DECOMP_ROOT}")

    write_decomposition_note(pan_pr_auc, by_indication, DECOMP_ROOT)

    # 6. Summary
    print("\n" + "=" * 72)
    print(f"Pan-indication GBT TEST PR-AUC: {pan_pr_auc:.4f}")
    print("-" * 72)
    print(
        f"{'Indication':<10} {'n_trials':>10} {'n_arms':>8} {'PR-AUC':>10} {'split':>20}"
    )
    print("-" * 72)
    for row in by_indication:
        pr_s = (
            f"{row['test_pr_auc']:.4f}"
            if isinstance(row["test_pr_auc"], float)
            else "N/A"
        )
        print(
            f"{row['indication']:<10} {row['n_trials']:>10} {row['n_arms']:>8} "
            f"{pr_s:>10} {(row['split_type'] or ''):>20}"
        )
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
