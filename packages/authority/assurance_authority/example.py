"""A declaration to point this at when you have not written one yet.

**Why this module exists, since a bundled example usually does not need a reason.** `INBOUND_LEDGER`
row 2, heard twice: the open repo has no reason to be installed. Tested rather than assumed, this
package was the clearest case of it — `assurance check` reads a folder you already have and
`assurance deps` reads a manifest you already have, and this one needs a declaration of your own
principals hand-authored before anything happens at all. Nothing to point it at is a worse first run
than a wrong answer, because a wrong answer at least tells you what the tool does.

The example is a real team shape rather than a minimal one, and it produces all three outcomes,
because the middle one is the whole product. A sample where everything proceeds teaches that this
is an access-control library, which it is not.
"""

from __future__ import annotations

import json
from typing import Any

#: What `--write` calls the file when the caller does not say.
EXAMPLE_NAME = "team.json"

_EXAMPLE: dict[str, Any] = {
    "principals": [
        {
            "id": "priya",
            "name": "Priya (intern)",
            "kind": "user",
            "may_receive": ["public", "team-internal"],
        },
        {
            "id": "cfo",
            "name": "CFO",
            "kind": "user",
            "may_receive": ["public", "team-internal", "finance-confidential"],
        },
        {
            "id": "drafting-agent",
            "name": "Drafting agent",
            "kind": "service_account",
            "may_receive": ["public"],
        },
    ],
    "tasks": [
        # Proceeds, silently, which is the point.
        {"name": "team roster", "initiator": "priya", "requires": ["team-internal"]},
        # The one that matters: the TASK moves to someone who may hold the answer. Priya is told it
        # moved. She is never told the figure.
        {"name": "Q3 margin memo", "initiator": "priya", "requires": ["finance-confidential"]},
        # Nobody declared may receive payroll, so it stops. Not downgraded to a summary.
        {"name": "payroll extract", "initiator": "drafting-agent", "requires": ["payroll"]},
    ],
}


def example_json() -> str:
    """The example declaration, as the JSON a reader would have written."""
    return json.dumps(_EXAMPLE, indent=2) + "\n"
