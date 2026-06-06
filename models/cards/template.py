"""Markdown model-card rendering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def render_model_card(meta: dict[str, Any]) -> str:
    """Render a markdown model card from model metadata.

    The returned markdown is intentionally standalone: callers are responsible
    for choosing where, or whether, to write it.
    """
    data_source = _required_text(meta, "data_source")
    metrics = _required_mapping(meta, "metrics")
    per_group = _find_per_sponsor_class_metrics(metrics)

    lines = [
        f"# Model Card: {_required_text(meta, 'model_name')}",
        "",
        "## Data Source",
        "",
        f"**DATA SOURCE: {_escape_markdown(data_source)}**",
        "",
        f"- Model type: {_escape_markdown(_required_text(meta, 'model_type'))}",
        f"- Cohort: {_escape_markdown(_required_text(meta, 'cohort_description'))}",
        f"- Target: {_escape_markdown(_required_text(meta, 'target'))}",
        "",
        "## Features",
        "",
        *_bullet_lines(_required_sequence(meta, "features")),
        "",
        "## Metrics",
        "",
        *_overall_metric_lines(metrics),
        "",
        "### Per-Sponsor Class Metrics",
        "",
        *_metric_table_lines(per_group),
        "",
        "## Calibration",
        "",
        _escape_markdown(_required_text(meta, "calibration_note")),
        "",
        "## LIMITATIONS",
        "",
        *_bullet_lines(_required_sequence(meta, "limitations")),
        "",
    ]
    return "\n".join(lines)


def _required_text(meta: Mapping[str, Any], key: str) -> str:
    value = meta.get(key)
    if value is None:
        msg = f"Missing required model-card field: {key}"
        raise KeyError(msg)
    return str(value)


def _required_sequence(meta: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = meta.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str):
        msg = f"Model-card field must be a non-string sequence: {key}"
        raise TypeError(msg)
    return value


def _required_mapping(meta: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = meta.get(key)
    if not isinstance(value, Mapping):
        msg = f"Model-card field must be a mapping: {key}"
        raise TypeError(msg)
    return value


def _find_per_sponsor_class_metrics(
    metrics: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    for key in ("per_sponsor_class", "sponsor_class"):
        value = metrics.get(key)
        if isinstance(value, Mapping):
            return {
                str(group): group_metrics
                for group, group_metrics in value.items()
                if isinstance(group_metrics, Mapping)
            }
    msg = "metrics must include a per_sponsor_class sub-dict"
    raise KeyError(msg)


def _overall_metric_lines(metrics: Mapping[str, Any]) -> list[str]:
    excluded = {"per_sponsor_class", "sponsor_class"}
    lines = []
    for key in sorted(metrics):
        if key in excluded:
            continue
        value = metrics[key]
        if isinstance(value, Mapping):
            continue
        lines.append(f"- {_escape_markdown(str(key))}: {_format_value(value)}")
    return lines or ["- No overall metrics supplied."]


def _metric_table_lines(per_group: Mapping[str, Mapping[str, Any]]) -> list[str]:
    metric_names = sorted(
        {
            str(metric_name)
            for group_metrics in per_group.values()
            for metric_name in group_metrics
        }
    )
    header = ["sponsor_class", *metric_names]
    lines = [
        "| " + " | ".join(_escape_markdown(column) for column in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for sponsor_class in sorted(per_group):
        group_metrics = per_group[sponsor_class]
        row = [_escape_markdown(sponsor_class)]
        row.extend(_format_value(group_metrics.get(metric_name, "")) for metric_name in metric_names)
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _bullet_lines(items: Sequence[Any]) -> list[str]:
    return [f"- {_escape_markdown(str(item))}" for item in items]


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return _escape_markdown(str(value))


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")
