# Unreleased

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
