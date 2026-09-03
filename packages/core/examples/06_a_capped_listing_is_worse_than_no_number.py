"""Data pipelines: "24 of 24 partitions" from a listing that stopped at 24.

The subtle one, and the one that nearly got missed when this module was designed.

Every enumeration has a cap somewhere — an API page size, a `LIMIT`, a `maxKeys` on a bucket listing,
a guard against walking a huge directory. If the cap truncates the list, the DENOMINATOR is wrong,
and a coverage record that says "24 of 24, complete" is not merely unhelpful. It is confidently
wrong, which is the exact failure this library exists to prevent, walking back in the front door.

So `truncated` makes `complete` False on its own, even when nothing is missing. "We do not know what
we did not see" is not "nothing".

    python examples/06_a_capped_listing_is_worse_than_no_number.py
"""

from assurance_core.coverage import Coverage

# A day of hourly partitions. The bucket listing came back capped at 24 keys, and there are 31 days
# of data behind it — so this looks like a full day and is the first 24 keys of a much longer list.
listed = [f"dt=2026-08-14/hour={hour:02d}" for hour in range(24)]

capped = Coverage.of(
    expected=listed,
    found=listed,
    scope_label="hourly partitions for 2026-08-14",
    where="the warehouse",
    truncated="the listing returned its maximum of 24 keys",
)

print("With the cap recorded:")
print(" ", capped.summary())
print("  complete:", capped.complete, "\n")

honest = Coverage.of(
    expected=listed,
    found=listed,
    scope_label="hourly partitions for 2026-08-14",
    where="the warehouse",
    derivation="24 hourly partitions enumerated from the declared window, listing not capped",
)

print("With a listing that genuinely was not capped:")
print(" ", honest.summary())
print("  complete:", honest.complete)

print(
    "\nSame twenty-four rows, same 'everything we asked for was found', opposite verdicts.\n"
    "Pass `truncated` from every listing that can hit a limit — a page size, a LIMIT, a maxKeys.\n"
    "It is the difference between a number you can act on and a number that flatters you."
)
