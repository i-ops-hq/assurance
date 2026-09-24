# assurance

**One install for every assurance command.**

```bash
pip install assurance        # or, without installing anything:  uvx assurance --help
```

| command | question |
|---|---|
| `assurance diff` / `assurance check` | did the work cover what it was supposed to — and what did it miss? |
| `assurance pin` | did an MCP server change a tool definition after you approved it? |
| `assurance deps` | what will a `pip install` or `npm install` execute, read without executing it? |
| `assurance budget` | where did an agent run's budget go, and where did it loop going nowhere? |
| `assurance authority` | may this task proceed for the person who asked? |
| `assurance drift` | did a failure rate actually shift, or was the week noise? |

Nothing here consults a model, opens the network, or reports a check it could not run as a pass.

This package contains no code of its own. It installs
[`assurance-cli`](https://pypi.org/project/assurance-cli/),
[`assurance-deps`](https://pypi.org/project/assurance-deps/),
[`assurance-budget`](https://pypi.org/project/assurance-budget/) and
[`assurance-authority`](https://pypi.org/project/assurance-authority/), each of which also installs on
its own. The MCP server is separate: `pip install assurance-mcp`.

Source, issues and documentation: https://github.com/i-ops-hq/assurance · Apache-2.0.
