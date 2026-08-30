
---

## Session 139 — 2026-08-30 · Making the READMEs claim only what was proved, then stopping

Ashwinth asked the fair question: was open-sourcing assurance the right move.

**As distribution, not proven.** Zero stars, zero forks, zero issues, two days in. One real
conversation.

**As a forcing function on the product, clearly yes**, and this is the part that is easy to lose.
Making those modules survive outside I-Ops found defects in the PRODUCT, not just the packages:
`Coverage` reporting `complete: True` on eleven of twelve, `read` counting the wrong set, a run that
checked nothing reporting complete one level down, and `worker`/`policy`/`effects` carrying product
data that made them untestable in isolation. Every one landed back in `app/core/`. All of them would
have shipped.

**The cost is real.** Two days on packages with no users, while I-Ops has red Linux CI, three surfaces
disagreeing after a run, a Control Room saying "Load failed", and no design partner.

### The improvement pass was honesty, not features

Looking for what was broken rather than what was missing found the same defect in two READMEs: **they
led with the claim our own A/B refuted.**

`assurance-mcp` opened with *"Give your agent a way to find out what it missed."* Cursor beat that arm
without the tool. It now opens by saying so outright, then states what survived: an agent that has
already retrieved and holds `k` results, where nothing in those results says what the other set
contained.

`assurance-cli`'s `check` section now says it is **for CI, not for an agent** — a gate with no model
in it, returning the same exit code every time.

Pushed to GitHub only. PyPI keeps the older text until a release is judged worth it.

### Where the three packages actually stand

| | claim | status |
|---|---|---|
| `assurance-core` in a pipeline | coverage catches an answer no model could catch | **proven** |
| `assurance-cli --fail-on-gap` in CI | a gate with no model in it | structurally sound, **untested** |
| `assurance-mcp` for a strong agent | a coverage tool improves the answer | **lost, and the README now says so** |

Registry listing parked: `docs/ops/MCP_REGISTRY_BLOCKED.md`.

**Next: back to I-Ops.**
