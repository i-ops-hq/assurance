# assurance, the Claude Code plugin

After every turn, the Stop hook audits the session: whether anything was tested after Claude's last
edit, whether that run passed, and what it could not check. When the last edit was not followed by a
passing test or check, you are told, and Claude is asked to run them before it stops. It nudges at
most once per turn and never blocks a session.

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq                  # --scope project to share it with the repository
claude plugin disable assurance@i-ops-hq                  # pause it
claude plugin uninstall assurance@i-ops-hq                # take it out
```

`/assurance:audit` shows the whole report inside a session.

It runs `uvx assurance@<version>` (or an `assurance` installed with pip), so it needs
[uv](https://docs.astral.sh/uv/) or `pip install assurance`. No model, no network beyond fetching the
package once, no account. Source and documentation: https://github.com/i-ops-hq/assurance
