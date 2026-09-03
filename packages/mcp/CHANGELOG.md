# 0.4.1

- Load the server class through `importlib` so both `mcp` 1.x (`FastMCP`) and 2.x (`MCPServer`)
  resolve without a dual import that defeats `mypy --strict`.
- `py.typed` added; the package is typed and `mypy --strict` clean.
- Aligned with `assurance-core` 0.10.

# 0.4.0

- Four MCP tools over the assurance primitives, the fourth of which needs no folder.
