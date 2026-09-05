# 0.4.2

- **Floors raised to the versions the tests actually run against** — `assurance-core` 0.13.1 and
  `assurance-cli` 0.5.6, up from 0.7 and 0.3.1. Installed at the floors it used to declare, this
  server answered a folder of six annual reports with *"3 of 36 months from 2019-03 to 2024-03 —
  not in this folder: April 2021, May 2021, June 2021 and 30 more"*: thirty-three fabricated
  months, stated with no hedge, for a folder that was complete. The current stack refuses that
  folder and names the flags that would answer it anyway. No code changed in this package — the
  versions it is willing to run on did.

# 0.4.1

- Load the server class through `importlib` so both `mcp` 1.x (`FastMCP`) and 2.x (`MCPServer`)
  resolve without a dual import that defeats `mypy --strict`.
- `py.typed` added; the package is typed and `mypy --strict` clean.
- Aligned with `assurance-core` 0.10.

# 0.4.0

- Four MCP tools over the assurance primitives, the fourth of which needs no folder.
