"""Staleness: does this document still match the source it was made from?

A report is generated on Monday. The source spreadsheet changes on Thursday. The report is
now wrong, nobody knows, and it may already have been emailed. This is checkable:

    python examples/02_is_this_document_still_true.py
"""

from assurance_core.staleness import Verdict, compare

# What the document RECORDED about its source, at the moment it was generated.
recorded = {"file_name": "billing.csv", "rows": 12, "numeric": [{"name": "net", "total": 70867.50}]}

# What that same source says when you recompute it today.
current = {"file_name": "billing.csv", "rows": 8, "numeric": [{"name": "net", "total": 44470.00}]}

finding = compare(
    artifact_name="invoice_summary_2026-08.pdf",
    artifact_path="/reports/invoice_summary_2026-08.pdf",
    generated_at="2026-08-09T08:37:27+00:00",
    source_name="billing.csv",
    source_mtime=1787000000.0,
    recorded_facts=recorded,
    current_facts=current,
)

print(finding.sentence())
print()
print(f"verdict: {finding.verdict.value}")
for d in finding.divergences:
    print(f"   {d.measure}: claimed {d.claimed:,.2f} -> now {d.current:,.2f}  (delta {d.delta:+,.2f})")

# Two properties worth noticing, because they are what stop this being a false-positive machine:
#
#   1. It compares a document to ITS OWN named source. It never guesses that two documents
#      describe the same thing — that inference is where this kind of check goes wrong.
#   2. When it cannot check, it says UNCHECKABLE rather than staying quiet. A queue that
#      silently skips what it cannot verify is lying about its own coverage.
unknown = compare(
    artifact_name="scan.pdf", artifact_path="/reports/scan.pdf",
    generated_at="2026-08-09T08:37:27+00:00", source_name="",
    source_mtime=None, recorded_facts=None, current_facts=None,
)
print()
print(f"no source recorded -> {unknown.verdict.value}  (not silence)")
assert unknown.verdict is Verdict.UNCHECKABLE
