# Contributing

Review Fabric uses OpenSpec. Before implementation, propose a named change under `openspec/changes/`, write or modify requirement scenarios, then implement its tasks using focused failing tests first. Mark a task complete only after its implementation and tests pass.

Validate a change before review:

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
npx --yes @fission-ai/openspec@1.6.0 validate establish-review-protocol --strict
```

Do not add credentials, captured provider responses containing secrets, or product mutations outside a review's `.review-fabric/` artifact directory.
