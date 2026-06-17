"""Hermetic posture for the Guide tests: stub the Guide LLM (no live call, no key).

Mirrors the app's VIGIL_LLM_STUB posture but for the Guide's OWN client. Set before any
guide.config import so the cached config picks it up.
"""

from __future__ import annotations

import os

os.environ.setdefault("VIGIL_GUIDE_LLM_STUB", "true")
