# Contributing

`assurance-mcp` exposes the assurance checks as MCP tools. **This repository is its source of
truth**, and pull requests are welcome.

## The lines it may not cross

- **Read-only by construction.** No tool writes, deletes, or opens the network.
  `tests/test_read_only.py` proves it by inspecting the package, not by trusting a docstring.
- **The model never chooses the boundary.** Which folders a tool may read is set by `--root` in the
  server's config (`assurance_mcp/boundary.py`), never by a tool argument. `tests/test_boundary.py`
  holds each escape that worked before it existed.
- **`expected` is never inferred.** The caller declares what should be there; a denominator the tool
  picks for itself always reports that it did fine.
- **No business logic here.** `server.py` adapts; the checks live in `assurance-cli` and the
  decisions in `assurance-core`.

## Before you open a PR

```bash
python -m pytest -q packages/mcp
cd packages/mcp && python -m mypy --strict assurance_mcp
```

Then the repository-wide [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
