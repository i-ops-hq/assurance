# Sample session for `assurance audit`

`sample-session.jsonl` is a short, made-up Claude Code session in the real transcript format: the
agent is asked to fix an invoice-rounding bug "and make sure the tests pass", and ends by saying
"All done". Run the audit on it to see what that claim leaves out:

```bash
uvx assurance audit examples/audit/sample-session.jsonl
```

The session is invented; the output is what the published tool prints for it. To audit your own
work, run `assurance audit` with no arguments inside a project where you've used Claude Code.
