# assurance, the Claude Code plugin

Your agent says it's done. This tells you what it didn't check.

`/assurance:audit` reads the transcript of the session you run it in and reports what actually
happened: the edits Claude made, the tests and checks that ran after them and whether they passed,
how that compares with the files, tests and commands your last prompt named, and — the part that
matters most — what it could **not** classify or read. Every answer is arithmetic over what the
session did. No model decides anything, so the result does not depend on which model you were using.

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq                  # --scope project to share it with the repository
claude plugin disable assurance@i-ops-hq                  # pause it
claude plugin uninstall assurance@i-ops-hq                # take it out
```

## What this plugin contains, all of it

Five files, and none of them runs anything on its own:
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json), an icon, this README,
[`PRIVACY.md`](PRIVACY.md), and [`skills/audit/SKILL.md`](skills/audit/SKILL.md) — instructions
Claude follows when you type `/assurance:audit`. The skill asks Claude to run
`uvx assurance@<version> audit`, and **Claude asks your permission before running it**, as it would
for any command. Read the skill before you install this, as you should any plugin; it is shorter
than this README.

It needs [uv](https://docs.astral.sh/uv/), or `pip install assurance`. The first run fetches the
pinned version from PyPI and uses that copy afterwards; apart from that fetch nothing leaves your
machine. What it reads is the transcript Claude Code keeps on your machine, of the session you run
it in and no other.
[Privacy](https://github.com/i-ops-hq/assurance/blob/main/plugins/assurance/PRIVACY.md) says what it
reads, keeps and sends.

## Running it after every turn

The plugin reports when you ask it to. For the same audit to run by itself when a turn ends, install
the Stop hook from the package instead:

```bash
uvx assurance hook install     # shows the change and asks before writing; `hook remove` undoes it
```

That hook speaks at one of three levels, naming the level first. *Note*: this turn changed code and
nothing has tested it yet — you are told once, with the command to run, and Claude is not asked
anything. *Suggested review*: a test or check after the last code edit failed, a path your project
protects changed without your prompt naming it, or Claude says the tests pass when nothing verified
the edit. *Stop and check*: untested code was pushed, merged, published, deployed or committed on
main, or shipped before a command your project says must pass. Edits to prose and assets pass
quietly, and a session is never blocked.

**Why the hook is not in the plugin.** Anthropic's plugin directory accepts hooks, shell scripts and
pre-approved tools only from organizations it has verified, and we are not one of those. Rather than
ship something that cannot be listed, the plugin carries the part that asks your permission every
time, and the part that runs by itself is one command away in the package, where you install it
yourself and can see it in your own settings.

Source and documentation: https://github.com/i-ops-hq/assurance
