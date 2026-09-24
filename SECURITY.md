# Security

## Reporting

**Email: hello@i-ops.dev.** Please do not open a public issue for a vulnerability.

Include enough detail to reproduce it. We aim to acknowledge within one week.

Each package also carries its own `SECURITY.md` with the threat model specific to it — start with
[`packages/deps/SECURITY.md`](packages/deps/SECURITY.md) if the report concerns dependency reading,
since that is the one package here that reads attacker-controlled input by design.

You can also use GitHub's [*Report a vulnerability*](https://github.com/i-ops-hq/assurance/security/advisories/new)
flow, which three of the package files already point at. Private vulnerability reporting was
disabled on this repository until 2026-09-14, which meant those links did nothing for anyone outside
the org; it is enabled now and both routes work.

## What is in this repository, and what is not

Six pure-Python packages. **Four of the six have no third-party runtime dependency at all**, and
the two that do have exactly one each:

| Package | Third-party runtime dependencies |
|---|---|
| `assurance-core` | none |
| `assurance-deps` | none |
| `assurance-budget` | none — `assurance-core` only |
| `assurance-authority` | none — `assurance-core` only |
| `assurance-cli` | `openpyxl`, to read `.xlsx` |
| `assurance-mcp` | `mcp`, the protocol SDK |

`pip show <package>` prints the `Requires:` line, so this is a claim anyone can check in five
seconds — which is why it is stated per package rather than as a round number. "Zero dependencies"
across all six would be a nicer sentence and two-sixths wrong.

There is **no service here**: no network endpoints, no credentials, no daemon, no background
process, no telemetry. Nothing in this repository phones home, and nothing in it listens.

## What each package touches

| Package | Reads | Writes | Network |
|---|---|---|---|
| `assurance-core` | nothing — pure functions over values you pass in | nothing | never |
| `assurance-cli` | files in the folder you name; for `pin`, your MCP config (project, `~/.cursor`, Claude Desktop) | two files, each only when you ask: the `.assurance.json` baseline, and a pin snapshot at the path you give `pin` | never itself — but `pin` **starts the stdio servers your MCP config names**, and they may |
| `assurance-mcp` | files in the folder you name | nothing | stdio to its client only |
| `assurance-deps` | a manifest you name, and archives already on disk | nothing | never |
| `assurance-budget` | one log file you name | nothing | never |
| `assurance-authority` | one JSON file you name | nothing | never |

**`assurance-deps` is the one to look at hardest.** It reads package archives, which are written by
whoever published them, and the whole reason to point it at one is that you do not trust it yet. It
never executes, imports or extracts what it reads: archive members are listed, a few named files are
pulled into memory, and Python source is parsed to an AST — which compiles but does not run.
`packages/deps/tests/test_never_executes.py` is the control for that and was written before the
reader it guards.

Never extracting also settles path traversal for free: a tar entry called `../../etc/passwd` is a
string in a listing here, not a file anywhere.

## What we would most like to be told

- **A path escape.** `assurance check` and the MCP server confine themselves to the folder you name.
  A symlink, a `..` segment or an archive member that reaches outside it is the report we most want.
- **Anything that makes `assurance-deps` execute, import or extract.** See above; that is the
  premise of the package rather than a preference.
- **A write from something documented as read-only.** The MCP server writes nothing at all. The CLI
  writes only the baseline and the pin snapshot, each on explicit request; a CI step exists for
  exactly this and runs on every change.
- **A number that reads as more than it is.** Not a vulnerability in the usual sense, and we treat it
  with the same seriousness — a coverage figure that omits what it could not read is how somebody
  ships a gap believing they checked for one. Those belong in a public issue, not here.

**`assurance pin` runs code by design.** Reading a server's tool definitions means starting it, so
`pin --save` and `pin --check` execute every stdio command in the MCP config they read. Treat the
config as code: in CI, run `pin` on `pull_request` rather than `pull_request_target`, and give that
job no secrets, because a pull request can edit `.mcp.json`.

## What is out of scope

- The hosted I-Ops product. Same address; it will be routed.
- Denial of service by pointing a command at a very large folder or archive. These are local tools
  you run on your own machine against input you chose.
- Anything requiring an attacker to already control the machine the command runs on.

## Supported versions

The latest published release of each package on PyPI. There are no long-term support branches and
there will not be until 1.0 — a fix ships as a new patch release.
