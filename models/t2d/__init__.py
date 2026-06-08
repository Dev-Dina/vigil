"""Phase-3 T2D modelling layer (build-time only).

A focused, single-indication slice of the Phase-3 modelling work: real T2D registry baselines
(1a), a synthetic structural-only ablation (1b), a pre-registered bar (2), and a synthetic-cohort
LSTM sequence model (3). Every artifact lands under ``data/models/t2d/`` (git-ignored) and is
never reached at runtime or by an agent. The pan-indication baselines (``models/baselines.py``)
and their ``data/models/`` artifacts are left untouched.

Held-out axis (stated once, carded everywhere): **temporal, trial-level, keyed on
``ref_trial.start_date``, group-disjoint by ``nct_id``** — earlier-starting trials -> train,
later -> test. Within-participant visit-time is the sequence the LSTM consumes; lead-time is
measured in visits before the dropout event. ONE ``temporal_group_split`` over the 755 T2D
cohort trials is shared by 1a (real arms), 1b and 3 (synthetic participants assigned to folds by
their ``nct_id``).
"""

from __future__ import annotations
