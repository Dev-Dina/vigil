---
name: progress
description: Use to update or report build progress. Reads ROADMAP.md, scans the repo for TODO comments, syncs the open-TODO register, checks off completed done-when items, and reports what's left. Trigger at the end of a phase, after a fix batch, or when asked "where are we" / "what's left".
---

# Progress tracker (Vigil)

`ROADMAP.md` is the source of truth for *where we are*. Keep it accurate; do not invent progress.

## Steps
1. Read `ROADMAP.md`.
2. Scan the tree for `TODO(`, `FIXME(`, `XXX` comments (`grep -rn`). Reconcile against the
   "Open TODO register": add new ones (file:line + note), mark resolved ones `[x]` only if the
   comment is gone.
3. For each phase, check the "Done when" criteria against reality (tests green? files exist?).
   Mark `[x]` only when the criterion is actually met — `[~]` if partial. Never tick a box you
   cannot verify; say what's missing instead.
4. Update the phase status markers ([ ]/[~]/[x]).
5. Report: current phase, what's done, what's blocking, and the top 3 next actions. One screen max.

## Rules
- Verify, don't assume. A phase that passes its tests but is built on placeholder/sample data is
  `[~]`, not `[x]` — note the gap.
- Do not edit code or specs; only `ROADMAP.md`.
- If a done-when criterion is ambiguous, flag it rather than guessing.
