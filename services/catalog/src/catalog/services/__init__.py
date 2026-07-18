"""Framework-independent catalog domain services.

Routes, MCP tools, and future internal workflows call these functions. They
operate on explicitly injected dependencies (``AsyncSession``, ``AsyncEngine``)
and raise domain errors from ``catalog.services.errors`` — never HTTP errors.
Nothing in this package imports FastAPI.
"""
