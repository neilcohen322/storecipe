"""HTTP routing layer.

Route modules own URLs, authentication dependencies, request parameters, HTTP
status codes, and response construction. They delegate all workflows to
``catalog.services`` and must not import SQLAlchemy or ORM models directly
(enforced by tests/test_architecture.py).
"""
