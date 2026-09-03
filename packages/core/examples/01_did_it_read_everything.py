"""Coverage: did the run open everything the task required?

The question nobody asks. Every tool call can return 200 and the answer can still be built
on two thirds of the data. Run this file directly:

    python examples/01_did_it_read_everything.py

It exits non-zero on purpose: a coverage gap should stop a pipeline, not warn it.
"""

from assurance_core.coverage import Coverage, EvidenceRef, Expectation

# 1. Declare what the task REQUIRES, before it runs. Derived from the scope by code —
#    here, every month in a two-year range.
expected = [
    Expectation(key=f"{year}-{month:02d}", label=f"{month:02d}/{year}", why="in the requested range")
    for year in (2024, 2025)
    for month in range(1, 13)
]

# 2. Record what was ACTUALLY opened, with provenance. Your agent fills this in as it reads.
on_disk = {e.key for e in expected} - {"2024-03", "2025-07"}   # two months never arrived
found = {
    key: EvidenceRef(key=key, path=f"/reports/{key}.csv", bytes=2048, reader="my_agent")
    for key in sorted(on_disk)
}

# 3. Diff them. This is the whole primitive.
coverage = Coverage(
    scope_label="monthly reports, 2024-2025",
    expected=expected,
    found=found,
    missing=[e for e in expected if e.key not in found],
)

print(coverage.summary())
print()
print(f"required : {coverage.required}")
print(f"read     : {coverage.read}")
print(f"complete : {coverage.complete}")

# 4. The point: a gap is a fact about the run, not a warning to be dismissed.
if not coverage.complete:
    raise SystemExit(
        "\nThis run must not be reported as complete. An answer built on 22 of 24 months\n"
        "is not a two-year trend, however confident the prose sounds."
    )
