"""The isolated public Guide service (Phase 7).

A SEPARATE service from the real Vigil app (`vigil/`). It imports NOTHING from `vigil.*` — the
structural isolation proof (specs/isolation.md § Phase 7 ratified decisions). Its only capability
(from 7.2) is searching its own file-backed approved-document index; it holds no participant-DB
credential, no Vault token, no Redis/queue URL, and no internal/admin address.
"""
