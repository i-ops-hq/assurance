# 0.5.0

- **`--version`** prints `assurance-mcp <version>` and exits 0 before the server starts.

**The folder tools read only inside folders you grant in the server's config.** Breaking, on purpose.

- **Before, the model chose the boundary.** Every folder tool took `folder` and read whatever it
  named. The `..` and symlink checks confined a document to that folder, but nothing confined the
  folder: `/etc`, `~/.aws` and `/` were all answered. `check_staleness_tool` with `recorded_facts`
  returned the row count and numeric column totals of any CSV or XLSX on the machine, and `/` walked
  the filesystem until a `PermissionError` ended the call. Under prompt injection that is a read
  channel an attacker steers. Found 2026-09-24 driving the published server with a real MCP client.
- **Now:** `--root DIR` in the server's `args` (repeatable), or `ASSURANCE_MCP_ROOTS` in its `env`.
  A folder outside every root is refused with the roots that were granted. With none, the folder
  tools refuse and name the config line to add; `check_set_coverage_tool` and
  `check_retrieval_coverage_tool` touch no filesystem and keep working. A filesystem root (`/`,
  `C:\`) is refused even when granted.
- **Not the working directory, and not MCP roots.** Clients start servers from wherever the client
  started, which can be `/`. The 2026-07-28 MCP specification deprecates roots (SEP-2577) and names
  server configuration as the replacement.
- `folder` may be relative to a granted root, or empty when exactly one is granted.
- **Every tool now carries `readOnlyHint: true`, `destructiveHint: false`, `openWorldHint: false`**,
  so a client can let them run without an approval prompt each time. Verified on mcp 1.30 and 2.2.
- **Requires `assurance-cli>=0.5.13`**, which names an unreadable directory instead of crashing.

**To upgrade:** add `"--root", "/path/to/your/folder"` to the server's `args`.

# 0.4.8

- **Requires `assurance-cli>=0.5.12`.** No code change here; the floor moves because that is the
  version this tree tests against. It stayed at 0.5.10 through the cli's 0.5.11 and 0.5.12,
  naming a version these tests had stopped running against.

# 0.4.7

- **Requires `assurance-cli>=0.5.10`.** No code change here; the floor moves because that is the
  version this tree tests against, and it is where `assurance deps` appears.

# 0.4.6

- **Requires `assurance-cli>=0.5.9`.** No code change here. `check_coverage_tool` returns the new
  `name_vs_content` field with it, so an agent reading a folder is told when a filename and the rows
  inside it disagree — the case where the coverage number is right about the names and wrong about
  the data.

# 0.4.5

- **Requires `assurance-cli>=0.5.8`.** No code change here; the floor moves so a fresh install of
  this server gets the four message and traceback fixes found by probing the published 0.5.7.

# 0.4.4

- **Requires `assurance-cli>=0.5.7` and `assurance-core>=0.13.2`.** No code change here; the floors
  move because two of the defects fixed there were reported *through this server*. A folder holding
  a `.xlsx` that is not a zip archive made `check_coverage_tool` return `is_error=true` with the
  text *"Error executing tool check_coverage_tool"* and nothing an agent could act on, and a weekly
  folder crossing the end of a 52-week year was told a week that does not exist was missing.

# 0.4.3

- **The MCP registry ownership token is in the README**, so this release can actually be listed.
  The registry proves that whoever controls the PyPI package controls the server name by requiring
  `mcp-name: io.github.i-ops-hq/assurance-mcp` to appear there, and it reads the README from the
  published distribution — so 0.4.2 uploaded to PyPI cleanly and is unlistable forever. Nothing in
  the release path had ever looked at the README; a gate does now.

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
