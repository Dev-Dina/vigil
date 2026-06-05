"""Raw AACT NDJSON -> validated ``ref_*`` Parquet tables.

Pure functions assemble per-NCT records, then validate at the boundary with the Pydantic
models in :mod:`ingestion.schema`. A missing/malformed field raises a typed
``DataValidationError`` and the offending record is recorded in the quality report (never
silently coerced or dropped). The ``fail_loud`` flag controls whether the first violation
aborts the run (spec default) or the run collects every violation for the report.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from ingestion import ctgov_enums
from ingestion.config import CLEAN_ROOT
from ingestion.errors import DataValidationError
from ingestion.extract import read_ndjson
from ingestion.report import QualityReport
from ingestion.schema import RefArm, RefTrial, RefWithdrawalReason
from ingestion.vocab import CENSORED, map_therapeutic_area, normalize_reason

# AACT milestone titles for participant flow.
_STARTED = "STARTED"
_COMPLETED = "COMPLETED"
_NOT_COMPLETED = "NOT COMPLETED"

_SPONSOR_CLASS_MAP = {
    "INDUSTRY": "INDUSTRY",
    "NIH": "NIH",
    "FED": "OTHER_GOV",
    "OTHER_GOV": "OTHER_GOV",
    "NETWORK": "ACADEMIC_OTHER",
    "OTHER": "ACADEMIC_OTHER",
    "INDIV": "ACADEMIC_OTHER",
    "UNKNOWN": "ACADEMIC_OTHER",
    "AMBIG": "ACADEMIC_OTHER",
}

_ARM_TYPE_MAP = {
    "experimental": "Experimental",
    "active comparator": "Active Comparator",
    "placebo comparator": "Placebo Comparator",
    "sham comparator": "Placebo Comparator",
    "no intervention": "Other",
    "other": "Other",
}


def _parse_date(value: Any) -> date | None:
    if value in (None, "", "None"):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _index_by_nct(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["nct_id"]].append(r)
    return out


def _first(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[0] if rows else {}


def load_raw_tables(snapshot_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Read every expected NDJSON table from a snapshot directory."""
    # TODO: never let sample fixtures be mistaken for real AACT
    # (``ingestion/fixtures/aact_sample`` is SAMPLE/synthetic — see its manifest flags).
    tables = [
        "studies",
        "calculated_values",
        "eligibilities",
        "designs",
        "browse_conditions",
        "conditions",
        "interventions",
        "sponsors",
        "countries",
        "facilities",
        "result_groups",
        "milestones",
        "drop_withdrawals",
    ]
    return {t: read_ndjson(snapshot_dir / f"{t}.ndjson") for t in tables}


def _therapeutic_area(
    nct_id: str,
    browse: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    report: QualityReport,
) -> str:
    """First MeSH term that maps wins; fall back to condition name; else OTHER."""
    for row in browse:
        term = row.get("mesh_term")
        if term:
            area, unmapped = map_therapeutic_area(term)
            if area != "OTHER":
                return area
    for row in conditions:
        name = row.get("downcase_name")
        if name:
            area, _ = map_therapeutic_area(name)
            if area != "OTHER":
                return area
    # Nothing mapped: record the first available original and return OTHER.
    original = ""
    if browse and browse[0].get("mesh_term"):
        original = str(browse[0]["mesh_term"])
    elif conditions and conditions[0].get("downcase_name"):
        original = str(conditions[0]["downcase_name"])
    if original:
        report.add_unmapped_mesh(nct_id, original)
    return "OTHER"


def _sponsor_class(sponsors: list[dict[str, Any]]) -> str:
    lead = next(
        (s for s in sponsors if s.get("lead_or_collaborator") == "lead"),
        _first(sponsors),
    )
    raw = (lead.get("agency_class") or "").upper()
    return _SPONSOR_CLASS_MAP.get(raw, "ACADEMIC_OTHER")


def _build_trial(
    nct_id: str,
    raw: dict[str, dict[str, list[dict[str, Any]]] | Any],
    report: QualityReport,
) -> RefTrial:
    studies = _first(raw["studies"].get(nct_id, []))
    if not studies:
        raise DataValidationError(
            "ref_trial", nct_id, "studies", "no studies row", None
        )
    cv = _first(raw["calculated_values"].get(nct_id, []))
    elig = _first(raw["eligibilities"].get(nct_id, []))
    design = _first(raw["designs"].get(nct_id, []))

    start = _parse_date(studies.get("start_date"))
    pcd = _parse_date(studies.get("primary_completion_date"))
    if start is None or pcd is None:
        raise DataValidationError(
            "ref_trial",
            nct_id,
            "planned_duration_days",
            "missing start_date or primary_completion_date",
            (studies.get("start_date"), studies.get("primary_completion_date")),
        )
    planned_duration_days = (pcd - start).days

    countries = [c for c in raw["countries"].get(nct_id, []) if not c.get("removed")]
    facilities = raw["facilities"].get(nct_id, [])
    n_sites = len(facilities) or int(cv.get("number_of_facilities") or 0)
    n_countries = len({c.get("name") for c in countries if c.get("name")})

    # Normalise CTGOV2 enum tokens onto the spec's controlled values. Unmapped tokens
    # (genuinely outside the spec's set, e.g. primary_purpose 'ECT') resolve to None so the
    # Pydantic enum rejects them and the offending value is recorded — never silently coerced.
    def _enum(name: str, table_map: dict[str, str], raw_val, *, na_value=None):
        canonical, unmapped = ctgov_enums.normalize_enum(
            table_map, raw_val, na_value=na_value
        )
        if unmapped:
            report.add_unmapped_enum(nct_id, name, unmapped)
        return canonical

    fields = {
        "nct_id": nct_id,
        "study_type": _enum(
            "study_type", ctgov_enums.STUDY_TYPE, studies.get("study_type")
        ),
        "phase": _enum("phase", ctgov_enums.PHASE, studies.get("phase"), na_value="NA"),
        "therapeutic_area": _therapeutic_area(
            nct_id,
            raw["browse_conditions"].get(nct_id, []),
            raw["conditions"].get(nct_id, []),
            report,
        ),
        "enrollment": studies.get("enrollment"),
        "n_arms": studies.get("number_of_arms"),
        "n_sites": n_sites,
        "n_countries": n_countries,
        "planned_duration_days": planned_duration_days,
        "actual_duration_days": cv.get("actual_duration"),
        "allocation": _enum(
            "allocation",
            ctgov_enums.ALLOCATION,
            design.get("allocation"),
            na_value="NA",
        ),
        "intervention_model": _enum(
            "intervention_model",
            ctgov_enums.INTERVENTION_MODEL,
            design.get("intervention_model"),
            na_value="NA",
        ),
        "masking": _enum(
            "masking", ctgov_enums.MASKING, design.get("masking"), na_value="NA"
        ),
        "primary_purpose": _enum(
            "primary_purpose",
            ctgov_enums.PRIMARY_PURPOSE,
            design.get("primary_purpose"),
            na_value="NA",
        ),
        "min_age_years": cv.get("minimum_age_num"),
        "max_age_years": cv.get("maximum_age_num"),
        "gender": _enum(
            "gender", ctgov_enums.GENDER, elig.get("gender"), na_value="ALL"
        ),
        "healthy_volunteers": _as_bool(elig.get("healthy_volunteers")),
        "sponsor_class": _sponsor_class(raw["sponsors"].get(nct_id, [])),
        "results_reported": _as_bool(cv.get("were_results_reported"), default=True),
    }
    try:
        return RefTrial(**fields)
    except ValidationError as exc:
        raise _to_data_error("ref_trial", nct_id, fields, exc) from exc


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "t", "yes", "y", "1"):
        return True
    if s in ("false", "f", "no", "n", "0"):
        return False
    if s in ("accepts healthy volunteers",):
        return True
    if s in ("no",):
        return False
    return default


def _to_data_error(
    table: str, nct_id: str, fields: dict[str, Any], exc: ValidationError
) -> DataValidationError:
    err = exc.errors()[0]
    loc = err.get("loc", ("?",))
    field_name = str(loc[0]) if loc else "?"
    return DataValidationError(
        table=table,
        nct_id=nct_id,
        field_name=field_name,
        message=err.get("msg", "validation error"),
        value=fields.get(field_name),
        record=fields,
    )


def _milestone_counts(
    nct_id: str, milestones: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, int]], dict[str, str | None]]:
    """Per group code, the baseline-period STARTED/COMPLETED/NOT COMPLETED counts.

    AACT participant-flow milestones are reported per ``period``; multi-period flows (e.g.
    crossover trials, ~15% of the cohort) repeat the canonical STARTED/COMPLETED/NOT
    COMPLETED titles for every period, and the periods are not in a reliable file order. We
    therefore pick, per group code, the **baseline period = the period with the largest
    STARTED count** (ties broken by period label for determinism). That is the period where
    the arm actually enrolled participants; the arm's started/completed/not-completed come
    from it. Collapsing across periods would mix STARTED from an empty period with COMPLETED
    from the real period and produce ``completed > started`` artifacts that raw AACT does not
    contain.

    Returns ``(counts_by_group, baseline_period_by_group)`` so the withdrawal stage can align
    to the same period.
    """
    # group_code -> period -> {title: count}, restricted to canonical flow titles.
    per_period: dict[str, dict[str | None, dict[str, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for m in milestones:
        code = m.get("ctgov_group_code")
        title = (m.get("title") or "").strip().upper()
        count = m.get("count")
        period = m.get("period")
        if code is None or count is None:
            continue
        if title not in (_STARTED, _COMPLETED, _NOT_COMPLETED):
            continue
        per_period[code][period][title] = int(count)

    counts: dict[str, dict[str, int]] = {}
    baseline: dict[str, str | None] = {}
    for code, periods in per_period.items():
        # Baseline period = max STARTED, deterministic tiebreak on the period label.
        chosen = max(
            periods.items(),
            key=lambda kv: (kv[1].get(_STARTED, 0), str(kv[0] or "")),
        )
        baseline[code] = chosen[0]
        counts[code] = chosen[1]
    return counts, baseline


def _arm_type(title: str | None) -> str:
    text = (title or "").strip().lower()
    for needle, label in _ARM_TYPE_MAP.items():
        if needle in text:
            return label
    if "placebo" in text:
        return "Placebo Comparator"
    if "experimental" in text or "treatment" in text:
        return "Experimental"
    return "Other"


def _build_arms(
    nct_id: str,
    raw: dict[str, Any],
    report: QualityReport,
) -> tuple[list[RefArm], dict[str, str | None]]:
    groups = {
        g.get("ctgov_group_code"): g
        for g in raw["result_groups"].get(nct_id, [])
        if g.get("result_type") == "Participant Flow"
    }
    counts, baseline = _milestone_counts(nct_id, raw["milestones"].get(nct_id, []))
    arms: list[RefArm] = []
    for code, c in counts.items():
        started = c.get(_STARTED)
        completed = c.get(_COMPLETED)
        if started is None:
            continue
        if completed is None:
            # NOT COMPLETED present without COMPLETED -> derive completed.
            nc = c.get(_NOT_COMPLETED)
            completed = started - nc if nc is not None else started
        not_completed = started - completed
        dropout_rate = (not_completed / started) if started > 0 else 0.0
        title = groups.get(code, {}).get("title")
        fields = {
            "arm_id": f"{nct_id}{code}",
            "nct_id": nct_id,
            "arm_type": _arm_type(title),
            "started": started,
            "completed": completed,
            "not_completed": not_completed,
            "dropout_rate": dropout_rate,
        }
        try:
            arms.append(RefArm(**fields))
        except ValidationError as exc:
            raise _to_data_error("ref_arm", nct_id, fields, exc) from exc
    return arms, baseline


def _build_withdrawals(
    nct_id: str,
    raw: dict[str, Any],
    arms: list[RefArm],
    baseline: dict[str, str | None],
    report: QualityReport,
) -> list[RefWithdrawalReason]:
    arm_index = {a.arm_id: a for a in arms}
    # Aggregate by (arm_id, canonical reason). Only count withdrawals from the SAME baseline
    # period the arm's STARTED/COMPLETED came from, so the cross-record check compares
    # like-for-like (multi-period flows otherwise sum every period's withdrawals against a
    # single period's not_completed and spuriously overflow).
    agg: dict[tuple[str, str], int] = defaultdict(int)
    for dw in raw["drop_withdrawals"].get(nct_id, []):
        code = dw.get("ctgov_group_code")
        count = dw.get("count")
        if code is None or count is None:
            continue
        if code in baseline and dw.get("period") != baseline[code]:
            continue
        arm_id = f"{nct_id}{code}"
        canonical, unmapped = normalize_reason(dw.get("reason") or "")
        if canonical == CENSORED:
            # Right-censoring / still-ongoing: NOT a dropout reason. Excluded from the
            # reason-mix entirely (neither a category nor OTHER), recorded separately so the
            # volume is never silently lost and never counts toward explained dropout.
            report.add_excluded_censoring(nct_id, arm_id, unmapped or "", int(count))
            continue
        if unmapped is not None:
            report.add_unmapped_reason(nct_id, arm_id, unmapped)
        agg[(arm_id, canonical)] += int(count)

    out: list[RefWithdrawalReason] = []
    for (arm_id, reason), count in agg.items():
        fields = {
            "nct_id": nct_id,
            "arm_id": arm_id,
            "reason": reason,
            "count": count,
        }
        try:
            out.append(RefWithdrawalReason(**fields))
        except ValidationError as exc:
            raise _to_data_error("ref_withdrawal_reason", nct_id, fields, exc) from exc

    # Cross-record: sum(withdrawal counts) <= not_completed per arm. Remainder reported.
    by_arm: dict[str, int] = defaultdict(int)
    for w in out:
        by_arm[w.arm_id] += w.count
    for arm_id, explained in by_arm.items():
        arm = arm_index.get(arm_id)
        if arm is None:
            continue
        if explained > arm.not_completed:
            raise DataValidationError(
                "ref_withdrawal_reason",
                nct_id,
                "count",
                f"sum(withdrawal counts) {explained} > not_completed "
                f"{arm.not_completed} for arm {arm_id}",
                explained,
            )
        if explained < arm.not_completed:
            report.add_unexplained_withdrawal(
                nct_id, arm_id, arm.not_completed, explained
            )
    return out


def clean_snapshot(
    snapshot_dir: Path,
    *,
    report: QualityReport,
    fail_loud: bool = True,
    out_root: Path = CLEAN_ROOT,
) -> dict[str, pd.DataFrame]:
    """Validate a raw snapshot into the three ``ref_*`` tables and write Parquet.

    With ``fail_loud=True`` (spec default) the first violation aborts. With ``False`` the
    offending NCT is recorded in the report and skipped so the report can enumerate every
    issue in one pass.
    """
    raw_lists = load_raw_tables(snapshot_dir)
    raw = {name: _index_by_nct(rows) for name, rows in raw_lists.items()}
    nct_ids = sorted(raw["studies"].keys())

    trials: list[dict[str, Any]] = []
    arms: list[dict[str, Any]] = []
    withdrawals: list[dict[str, Any]] = []

    for nct_id in nct_ids:
        try:
            trial = _build_trial(nct_id, raw, report)
            arm_objs, baseline = _build_arms(nct_id, raw, report)
            if not arm_objs:
                raise DataValidationError(
                    "ref_arm", nct_id, "started", "no participant-flow arms", None
                )
            wd_objs = _build_withdrawals(nct_id, raw, arm_objs, baseline, report)
        except DataValidationError as exc:
            report.add_validation_failure(
                exc.table, exc.nct_id, exc.field_name, exc.message, exc.value
            )
            if fail_loud:
                raise
            continue

        trials.append(trial.model_dump() | {"study_url": trial.study_url})
        arms.extend(a.model_dump() for a in arm_objs)
        withdrawals.extend(w.model_dump() for w in wd_objs)

    frames = {
        "ref_trial": pd.DataFrame(trials),
        "ref_arm": pd.DataFrame(arms),
        "ref_withdrawal_reason": pd.DataFrame(withdrawals),
    }
    _record_stats(frames, report)
    _write_parquet(frames, out_root)
    return frames


def _record_stats(frames: dict[str, pd.DataFrame], report: QualityReport) -> None:
    for name, df in frames.items():
        null_rates = (
            {col: float(df[col].isna().mean()) for col in df.columns} if len(df) else {}
        )
        report.set_table_stats(name, rows=len(df), null_rates=null_rates)


def _write_parquet(frames: dict[str, pd.DataFrame], out_root: Path) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_parquet(out_root / f"{name}.parquet", index=False)
