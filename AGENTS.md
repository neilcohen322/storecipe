# Storecipe Repository Instructions

## Personal recipe store

`services/recipe_store` is the runnable product: one SQLite file and an MCP server.
Do not add a UI for it. `compose.personal.yaml` is how to run it.
The Expo app and the catalog/ingestion stack are the earlier multi-service design.

## Planning artifacts

- Store plans, design specs, walkthroughs, and other project-management notes only in
  the sibling `../project-notes/` directory.
- Do not create or commit planning artifacts inside this repository.
- Preserve the `docs/superpowers/plans` or `docs/superpowers/specs` relative structure
  under `../project-notes/` when a workflow expects those paths.
