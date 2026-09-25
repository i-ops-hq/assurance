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

What it runs, all of it: [`hooks/hooks.json`](hooks/hooks.json) (one Stop hook),
[`scripts/assurance.sh`](scripts/assurance.sh) (28 lines, which find `uvx` and run the pinned
`assurance`), and [`skills/audit/SKILL.md`](skills/audit/SKILL.md) (run only when you type it; its one
permission is to run that script). Read them before you install it, as you should any plugin.

It runs `uvx assurance@<version>` (or an `assurance` installed with pip), so it needs
[uv](https://docs.astral.sh/uv/) or `pip install assurance`. On Windows the hook is a shell script, so it
needs Git Bash, which comes with Git for Windows; without it, use `uvx assurance@latest hook install`. No model, no network beyond fetching the
package once, no account. Source and documentation: https://github.com/i-ops-hq/assurance
