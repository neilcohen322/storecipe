"""HTTP routing layer.

Route modules own HTTP concerns and delegate workflows to ``ingestion.services``.
They must not import SQLAlchemy or ORM models directly (enforced by
tests/test_architecture.py).
"""
