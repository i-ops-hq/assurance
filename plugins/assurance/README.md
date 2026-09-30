# assurance, the Claude Code plugin

After every turn, the Stop hook checks Claude's code changes against the tests and checks that ran,
and against what you asked, and speaks when something is at stake, naming it first. *Check before
proceeding*: untested code was pushed, merged, published, deployed or committed on main, or shipped
before a command your project says must pass, or a test your last prompt names, passed. *Review
suggested*: a test or check after the last code edit failed, a path your project protects changed
without your prompt naming it, or Claude says the tests pass when nothing verified the edit. Routine
editing, and edits to prose and assets, pass quietly. Claude is asked to act too, at most once per
turn, and a session is never blocked.

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq                  # --scope project to share it with the repository
claude plugin disable assurance@i-ops-hq                  # pause it
claude plugin uninstall assurance@i-ops-hq                # take it out
```

`/assurance:audit` shows the whole report for the session you run it in, including what happened to
the files, tests and commands your last prompt names.

What it runs, all of it: [`hooks/hooks.json`](hooks/hooks.json) (one Stop hook),
[`scripts/assurance.sh`](scripts/assurance.sh) (50 lines, which find `uvx` and run the pinned
`assurance`), and [`skills/audit/SKILL.md`](skills/audit/SKILL.md) (run only when you type it; its one
permission is to run that script). Read them before you install it, as you should any plugin.

It runs `uvx assurance==<version>` (or an `assurance` installed with pip), so it needs
[uv](https://docs.astral.sh/uv/) or `pip install assurance`. It fetches the pinned version the first
time it runs and uses that copy from then on without the network, so a proxy, a private mirror or a
PyPI outage cannot keep a session from ending; if that first fetch fails, the hook says the turn was
not audited and lets it end. On Windows the hook is a shell script, so it needs Git Bash, which comes
with Git for Windows; without it, use `uvx assurance@latest hook install`. What it reads is the
transcript Claude Code keeps, on your machine, of the session it audits, and no other session's;
it sends that nowhere. [Privacy](https://github.com/i-ops-hq/assurance/blob/main/plugins/assurance/PRIVACY.md) says what it reads, keeps and sends.
Source and documentation: https://github.com/i-ops-hq/assurance
