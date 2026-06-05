"""Committed SAMPLE AACT fixtures. NOT a real extract.

These small NDJSON tables let the clean->synthetic->features pipeline run and be tested
end-to-end without a live AACT Postgres. They are clearly marked SAMPLE in the manifest.
A real extraction (``ingestion.extract.extract_to_raw``) is a drop-in replacement.
"""
