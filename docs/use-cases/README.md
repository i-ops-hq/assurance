# Use cases

Pick the one closest to your work. Each page says what goes wrong without Assurance, where it sits in
the workflow, the few lines that set it up, and what it does not do.

| Your work | What goes wrong | Where Assurance sits |
|---|---|---|
| [Payments and fintech](payments.md) | Money code ships on the agent's "done", with the ledger test last run before the fix | Your declared tests must pass after the last edit; a push over untested billing code is a stop |
| [Banking and regulated change](banking.md) | No evidence of what an agent changed, what was tested, or who was allowed to | A JSON record per session, and a clearance check against `CODEOWNERS` before review |
| [Healthcare and life sciences](healthcare.md) | An agent "tidies" patient fixtures or installs a package that runs a script | Protected paths named when changed; installs read before they run; MCP tools pinned |
| [Legal, insurance and documents](legal.md) | A confident answer from four of the six documents it needed | Required vs. retrieved, by name; missing periods in a folder of reports |
| [Platform, DevOps and SRE](platform.md) | An agent pushes, deploys or migrates over code nothing tested | Stop and check on the ship; what a change reaches; limits a session cannot raise |
| [Teams building their own agents](agent-builders.md) | The gate said allow, the run said done, and the check failed | A run record or your OTel traces, audited; a verdict your code can stop on |

New to it? Start with the [README](../../README.md): one command shows what your last Claude Code
session did and did not check.
