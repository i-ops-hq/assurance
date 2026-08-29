"""Audit and compliance: "18 of 20 controls" is not one fact, it is six.

An agent gathering evidence for a control framework — SOC 2, ISO 27001, HIPAA, an internal risk
register — reports twenty controls and eighteen pieces of evidence. What happened to the other two?

The answer decides who does what next, and every one of these sends someone somewhere different:

    not in the folder      → chase the control owner for a document that may never have existed
    a tombstone says gone  → an artefact WAS here and is not now; that is an incident, not a gap
    more than one candidate→ two files could be the evidence, and PICKING one invents provenance
    present but unreadable → a scanned PDF with no text layer; the control is untested
    not cleared to open    → the evidence exists and this principal may not see it; escalate the
                             TASK to someone who may, never the ANSWER back down to someone who may not
    the listing was capped → the DENOMINATOR is wrong, so every ratio above it is a guess

Collapsing those into "2 missing" is how an audit finding becomes a surprise in the closing meeting.

    python examples/04_which_controls_actually_have_evidence.py
"""

from assurance_core.coverage import Coverage

controls = [
    "CC6.1", "CC6.2", "CC6.3", "CC7.1", "CC7.2",
    "CC7.3", "CC8.1", "A1.1", "A1.2", "C1.1",
]

coverage = Coverage.of(
    expected=controls,
    found=["CC6.1", "CC6.2", "CC7.1", "CC7.2", "CC8.1"],
    scope_label="controls in scope for this period",
    where="the evidence folder",
    derivation="every control marked in-scope in the trust services matrix for FY26 Q3",
    gone={"CC6.3": "CC6.3 had evidence until 14 August and does not now"},
    ambiguous={"CC7.3": ["evidence/CC7.3-final.pdf", "evidence/CC7.3-final-v2.pdf"]},
    unreadable={"A1.1": "scanned PDF with no text layer"},
    unauthorized={"A1.2": "restricted to the security team"},
)

print(coverage.summary())
print(f"\ncomplete: {coverage.complete}   ({coverage.read} of {coverage.required} evidenced)")

# Each bucket is a different queue for a different person.
print("\nWho does what next")
for entry in coverage.missing:
    print(f"  {entry.key:6} chase the control owner — nothing matched it")
for key, note in coverage.gone.items():
    print(f"  {key:6} INCIDENT — {note}")
for key, candidates in coverage.ambiguous.items():
    print(f"  {key:6} {len(candidates)} candidates — a human names the real one; we will not pick")
for key, why in coverage.unreadable.items():
    print(f"  {key:6} present and untested — {why}")
for key, why in coverage.unauthorized.items():
    print(f"  {key:6} escalate the task, not the answer — {why}")

# `to_dict()` is the same record as JSON, for a CI artefact or a ticket body.
