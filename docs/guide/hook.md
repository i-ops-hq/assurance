# Run it after every session

Add a Stop hook, and Claude Code runs the audit each time Claude says it's finished. Claude Code opens
every hook message with "Stop says", so the hook names its level first, and there are three:

| Level | When | Who is asked |
|---|---|---|
| **note** | This turn edited code and no test or check has passed since. It names the command to run: every command your project says must pass, else the last test the session ran here that passed, else the last one it ran. | You. Claude is not asked anything, so the session is not kept going. Said once per stretch of untested edits, until something passes. |
| **suggested review** | The last test or check after the last code edit failed; a path your project protects was changed without your last prompt naming it; or Claude's last message says the tests pass when nothing verified the edit, or right after a test that failed. | You, and with `--nudge` Claude: fix it, or say plainly why not. |
| **stop and check** | The turn pushed, merged, published, deployed, ran a migration or committed on main, while code edited before it (with Edit or Write, or with `sed -i`, `>`, `git apply` and the like) had no test or check after it that visibly passed, or while a command your project says must pass, or a test your last prompt names, had not passed after it. | You, and with `--nudge` Claude: verify it before anything builds on it. |

Otherwise it stays quiet: edits to prose and assets (`.md`, images, `LICENSE`, …) need no test. It says
each finding once. A test piped into `tail` or followed by `; echo` doesn't count as passed: its exit
status is the other command's, so the result is read from the runner's summary line or reported as
unknown. Each command is read from where it ran, from the folder the shell was in and through `cd`,
`pushd` and `git -C`: an edit, a test, a commit or a push in another repository, or in one nested in
your project's folder, is not counted as your project's, and a commit after `git checkout -b` is not
on main. `assurance audit` still reports everything, whether the hook spoke or not.

```bash
uvx assurance@latest hook install   # shows the change to ~/.claude/settings.json, asks, then writes it
uvx assurance hook status           # where it is installed, which version, and whether it can run
uvx assurance hook remove           # takes it out again, wherever it is; nothing else changes
```

`install` pins the hook to the version it runs as, and running it again after an upgrade moves the
pin. The hook runs that version with `uvx --offline`, from the copy uv fetched when you installed it:
without the flag uv asks PyPI again every few minutes and exits 2 when it cannot reach it, and a Stop
hook that exits 2 tells Claude to keep going. `--scope project` writes it to the repository's
`.claude/settings.json`, so everyone who works on the project gets the audit once they commit it and
each fetches it once (`install` prints the command); `--scope local` is only you, in this repository.
`--no-nudge` tells you without asking Claude to act. It only ever touches the assurance hook,
refuses a settings file it cannot parse, and keeps the file as it was in
`~/.local/state/assurance/backups/` (Windows: `%LOCALAPPDATA%\assurance\backups\`). It nudges at
most once per turn, and it never fails or blocks a session: if it can't read the transcript it says
so and lets Claude finish. Take it out from your own terminal: in auto mode, Claude Code refuses to let
Claude remove it, as tampering with an audit.

Or install it as a **Claude Code plugin**, and let Claude Code add, pause and remove it:

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq     # --scope project to turn it on for everyone on the repository
claude plugin disable assurance@i-ops-hq     # pause it; `enable` turns it back on
claude plugin uninstall assurance@i-ops-hq   # take it out; `claude plugin marketplace remove i-ops-hq` forgets the source too
```

The plugin runs the same hook, pinned to the release, and finds `uvx` even when Claude Code starts
hooks without your terminal's PATH. It fetches that version the first time it runs and uses the copy
from then on, without the network. It also adds `/assurance:audit`, which shows the whole report
for the session you run it in; only you can run it, so it adds nothing to Claude's context until you do. And
when you ask to see your agents' work as a picture, across sessions, branches or teammates, Claude can
point you to [Rooms](https://github.com/i-ops-hq/iops-rooms), a free local board from the same makers
(`/assurance:board`); it runs nothing, and its one-line description is all it adds to Claude's context.
Use the plugin or `assurance hook install`, not both: together they audit twice per turn, and
`assurance hook status` says so.

**Read what a plugin runs before you install it, this one included.** A plugin runs as you. In
February 2026 Snyk scanned 3,984 agent skills from two public registries, ClawHub and skills.sh, of the
kind Claude Code, Cursor and OpenClaw load: 36.82% had at least one security flaw, 13.4% a critical
one, and 76 carried confirmed malicious payloads for credential theft, backdoors and data exfiltration
([ToxicSkills](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/)). This plugin is
[`plugins/assurance/`](../../plugins/assurance): one Stop hook, one 65-line shell script that runs the pinned
`uvx assurance==<version>`, a skill that only you can run, whose only permission is to run that script
when you type `/assurance:audit`, and a skill of words alone that tells you about Rooms when you ask.
It calls no model and sends nothing anywhere; the network is used once, by `uvx`, to fetch the pinned
package from PyPI.

On Windows, Claude Code runs hooks in Git Bash when Git for Windows is installed, and in PowerShell
when it is not. The plugin's hook is a shell script, so it needs Git Bash; without it, use
`uvx assurance@latest hook install`, which writes a hook both shells can run. Commands Claude runs
through the PowerShell tool are read like Bash ones: `Set-Content app.py` counts as an edit and
`pytest` as a test. Their result is taken from what the runner printed, because that tool's error
flag has not been checked against its exit status.

<details>
<summary>Or add it by hand</summary>

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "uvx --offline assurance@0.1.29 audit --hook --nudge" }] }
    ]
  }
}
```

Put it in `~/.claude/settings.json` for every project, or `.claude/settings.json` for one. Leave out
`--nudge` to be told without Claude being asked. Run `uvx assurance@0.1.29 --version` once first:
`--offline` runs the copy uv already has, so the hook never waits on PyPI.

The version is pinned on purpose. A hook runs after every turn in every project, so it should run a
version you chose: unpinned, `uvx` keeps whichever version it cached first and switches without
telling you when that cache is pruned. To upgrade, change the number.

Installed with pip instead of uv? Use `"command": "assurance audit --hook --nudge"`. If Claude Code
reports `command not found`, put the full path from `which uvx` (or `which assurance`) in the command;
`assurance hook install` does that for you.

</details>

[Back to the README](../../README.md)
