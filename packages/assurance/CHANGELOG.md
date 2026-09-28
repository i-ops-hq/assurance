# Unreleased

- **The plugin's script calls `uvx` by name.** Anthropic's plugin directory refused it as an
  "unpinned uvx launcher": the script found `uvx` and ran it from a variable, which a scanner cannot
  read. It now puts the places uv installs itself on the end of `PATH` (the macOS desktop app starts
  hooks without them) and runs `uvx assurance==<version>`, so the program and its pin are both in
  plain sight. It finds `uvx` in the same places, in the same order, as before.
- **The plugin has an icon**, `.claude-plugin/icon.svg`, for its listing.

# 0.1.6

- **Requires `assurance-budget` 0.2.6:** Go projects work: `gotestsum`, `staticcheck` and `go tool`
  are recognised, and a piped Go test or check is read from what it printed. The README's hook
  command and the plugin are pinned to this version.
- **The plugin's script names the package it runs.** It ran `assurance@$VERSION`; it now writes the
  version out where it runs it, so what runs can be read without following a variable, including by
  Anthropic's plugin directory, which refuses a launcher it cannot see pinned.
- **The plugin README says what the hook reads:** the transcript Claude Code keeps of the session
  that just ended, on your machine, and it sends that nowhere.

# 0.1.5

- **Requires `assurance-budget` 0.2.5:** a call Claude Code refused is no longer counted as a failure,
  a test, an edit or an unclassified command, and `permission-mode` records are read as bookkeeping.
  The README's hook command and the plugin are pinned to this version.
- **The README says to take the hook out yourself.** In auto mode, Claude Code refuses to let Claude
  remove it, as tampering with an audit, so `assurance hook remove` is for your own terminal.

# 0.1.4

- **Requires `assurance-budget` 0.2.4 and `assurance-cli` 0.6.2:** `assurance hook install`,
  `remove` and `status`, with a hook that runs without the network; a project's own tests and checks
  declared under `[audit]`; results read from jest, vitest, mocha, `node --test`, bun and cargo; a
  faster Stop hook on long sessions; Windows, including commands run through the PowerShell tool; and
  different edits to one file no longer reported as a loop. The README's hook command and the plugin
  are pinned to this version.
- **The plugin's hook no longer keeps Claude going when PyPI is out of reach.** uv exits 2 when it
  cannot reach the index, the script passed that on, and a Stop hook that exits 2 tells Claude to keep
  going: behind a proxy or during an outage, every turn would have ended with Claude told not to stop.
  The script now runs the copy uv already has without the network, fetches only on a first run, and
  when that fails says the turn was not audited and exits 0. The plugin README's "no network beyond
  fetching the package once" is now what the script does.
- **A Claude Code plugin**, `assurance@i-ops-hq`: `claude plugin marketplace add i-ops-hq/assurance`,
  then `claude plugin install assurance@i-ops-hq`. It runs the Stop hook pinned to this release, and
  `claude plugin disable` / `uninstall` pause it or take it out. It looks for `uvx` where uv installs
  it, because the desktop app can start hooks without your terminal's PATH, falls back to an
  `assurance` installed with pip, and without either says the turn was not audited and lets the
  session end. `/assurance:audit` shows the whole report in a session; only you can run it, so it
  adds nothing to Claude's context until you do.
- **`assurance hook install` / `remove` / `status`** in the README, as the way to add the Stop hook
  and take it out; the hand-written JSON stays as the alternative.
- **The README's sample report shows the corrected "after the last edit" line**: no test or check
  the audit recognises ran, and the two commands after the edit that it could not classify are named.
  The change itself is in `assurance-budget`.

# 0.1.3

- **Requires `assurance-budget` 0.2.3:** the Stop hook no longer treats a test piped into `tail` as
  passed, and counts edits made with `sed -i`, `>` and similar. The README's hook command is pinned to
  this version.

# 0.1.2

- **Requires `assurance-budget` 0.2.2 and `assurance-cli` 0.6.1:** `assurance audit --hook` runs the
  audit after every Claude Code turn (and with `--nudge` sends Claude back to run the tests it
  skipped), `assurance audit --demo` shows a report on a bundled sample, the unclassified line names
  what it could not classify, and `assurance --version` names every installed part.

# 0.1.1

- **Requires `assurance-budget` 0.2.1**, so `uvx assurance audit` gets its fixes: paths shown relative
  to the session folder on macOS, no false report of a limits-file change for a path outside the
  project, singular wording at 1, and repeated test commands grouped.

# 0.1.0

- **First release: `pip install assurance` installs every command-line tool**, and `uvx assurance`
  runs one without installing anything. No code of its own — it depends on `assurance-cli`,
  `assurance-deps`, `assurance-budget` and `assurance-authority`, and declares the `assurance`
  command so `uvx` and `pipx` find it on the package that was named.
