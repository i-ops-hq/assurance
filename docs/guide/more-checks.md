# More checks

Beyond the session audit: retrieval coverage, MCP tool pinning, what an install runs, what a change reaches, and the same checks as MCP tools.

## Retrieval, MCP pinning and installs

<details>
<summary><b>Did the retriever fetch what the question needed?</b></summary>

```bash
assurance diff --expected needed.txt --found retrieved.json --fail-on-gap
# 2 of 5 items — not in the found set: doc-2, doc-3, doc-5
# also present and not expected: doc-9
```

</details>

<details>
<summary><b>Did an MCP server change a tool definition behind your back?</b> (the rug-pull, CVE-2025-54136)</summary>

```bash
pip install 'assurance-cli[mcp]'
assurance pin --save      # snapshot every tool your MCP servers expose; commit .assurance/mcp-pins.json
assurance pin --check     # in CI: exit 1 if any description changed, or any server couldn't be checked
```

</details>

<details>
<summary><b>What will this install run?</b></summary>

```bash
assurance deps package.json      # reads package-lock.json and node_modules; runs nothing
# Of the 100 read, 2 execute code when installed:
#   · esbuild 0.23.1   node install.js
#   · sharp 0.33.5   node install/check
```

It reads what is on disk: the lockfile, `node_modules`, or downloaded wheels for Python. With none of
those it says it read nothing, rather than guessing.

</details>

## What a change reaches

`assurance reach` reads a code graph, such as the one [Graphify](https://pypi.org/project/graphifyy/)
builds from your code with no model, and says what a change to a file or folder reaches: each symbol
that depends on it, hop by hop, at the line of the call or the import. What the graph read from the
code is kept apart from what it inferred. Every report says first how far the graph is behind the
code, checked by each file's content, and ends with what it could not determine.

```bash
graphify update .                       # writes graphify-out/graph.json
assurance reach src/billing/money.py    # --json for a program, --depth to follow further
```

It builds no graph and adds no edge, so a dependency the graph does not hold is one it cannot see, and
it names what it did not follow. [packages/reach](../../packages/reach) has an example and what it reads.

## Use it from an MCP client

Works in Cursor, Claude Desktop, Claude Code and any other MCP client.

```bash
pip install assurance-mcp
```

```json
{
  "mcpServers": {
    "assurance": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "assurance_mcp.server", "--root", "/absolute/path/to/your/project"]
    }
  }
}
```

> [!IMPORTANT]
> `--root` is the only folder the tools may read. You set it in your config; the model can't widen it.

[Back to the README](../../README.md)
