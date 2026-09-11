"""The sentence a stranger reads, with the coverage line before the findings.

Modelled on `assurance-budget`'s closing line, which is the best sentence in the offering:

    Not tested by this log: iterations, frontier_calls, retries, seconds. The log carries no events
    of that kind, so this is silence rather than a pass.

The order here is the argument. What could not be read comes FIRST, because "no issues found" over
47 of 52 packages is a lie by omission, and a reader who sees the findings first has already formed
a conclusion by the time the caveat arrives.
"""

from __future__ import annotations

import json
from typing import Any

from assurance_deps.scan import Report

#: Never "safe", never "sandboxed", never "clean". This reports what an install will EXECUTE;
#: whether that is acceptable is the reader's call and the tool has no standing to make it.
_SHOWN = 8


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many}"


def format_report(report: Report) -> str:
    """The report as a person reads it: what could not be examined first, findings after."""
    lines: list[str] = []
    total, examined = report.total, report.examined

    if not total:
        return f"{report.manifest.name} lists no requirements.\n"

    unit = report.unit
    lines.append(f"{report.manifest.name} — {_plural(total, unit, unit + 's')}, {examined} read in full")
    if report.scope_note:
        lines.append(f"Counted over {report.scope_note}.")
    lines.append("")

    # A lockfile flag answers "does this run code" and nothing else. Counting it as read would be
    # claiming coverage of contents nobody opened.
    if report.partial:
        with_scripts = [e for e in report.partial if e.runs_at_install]
        lines.append(
            f"{len(report.partial)} known from the lockfile only — it records whether each has an "
            "install script, not what the script does:"
        )
        for known in with_scripts[:_SHOWN]:
            lines.append(f"  · {known.name} {known.version}".rstrip() + "   declares an install script")
        if not with_scripts:
            lines.append("  · none of them declares one")
        elif len(with_scripts) > _SHOWN:
            lines.append(f"  · and {len(with_scripts) - _SHOWN} more that declare one")
        lines.append("")

    # --- what could not be checked, first and at the same weight as what was ---
    if report.unexamined:
        lines.append(f"Could not be examined at all ({len(report.unexamined)}):")
        for gap in report.unexamined[:_SHOWN]:
            lines.append(f"  · {gap.name:<22} {gap.why}")
        if len(report.unexamined) > _SHOWN:
            lines.append(f"  · and {len(report.unexamined) - _SHOWN} more")
        lines.append("")

    if not examined:
        lines.append(
            "Nothing was read, so nothing is reported about install-time code. This is silence, "
            "not a pass."
        )
        lines.append("")
    else:
        hooks = report.runs_at_install
        if hooks:
            verb = "executes" if len(hooks) == 1 else "execute"
            lines.append(f"Of the {examined} read, {len(hooks)} {verb} code when installed:")
            for hooked in hooks[:_SHOWN]:
                what = "; ".join(h.what for h in hooked.hooks)
                lines.append(f"  · {hooked.name} {hooked.version}".rstrip() + f"   {what}")
            if len(hooks) > _SHOWN:
                lines.append(f"  · and {len(hooks) - _SHOWN} more")
        else:
            lines.append(
                f"Of the {examined} read, none execute code when installed — every one is a wheel, "
                "which pip unpacks rather than builds."
            )
        lines.append("")

        native = report.with_native
        if native:
            lines.append(f"{len(native)} {'ships' if len(native) == 1 else 'ship'} a compiled binary:")
            for compiled in native[:_SHOWN]:
                lines.append(
                    f"  · {compiled.name} {compiled.version}".rstrip()
                    + f"   {_plural(len(compiled.native), 'binary', 'binaries')}"
                )
            if len(native) > _SHOWN:
                lines.append(f"  · and {len(native) - _SHOWN} more")
            lines.append("")

        startup = report.with_startup_hooks
        if startup:
            lines.append(
                f"{len(startup)} {'installs' if len(startup) == 1 else 'install'} a .pth file, "
                "which the interpreter runs on every start:"
            )
            for booted in startup[:_SHOWN]:
                lines.append(
                    f"  · {booted.name} {booted.version}".rstrip()
                    + f"   {', '.join(booted.startup_hooks[:3])}"
                )
            lines.append("")

    if report.off_index:
        lines.append(
            f"{len(report.off_index)} {'does' if len(report.off_index) == 1 else 'do'} not come "
            "from the package index:"
        )
        for req in report.off_index[:_SHOWN]:
            note = f"   {req.note}" if req.note else ""
            lines.append(f"  · {req.name:<22} {req.where or req.source}{note}")
        if len(report.off_index) > _SHOWN:
            lines.append(f"  · and {len(report.off_index) - _SHOWN} more")
        lines.append("")

    if report.delta is not None:
        d = report.delta
        if d.only_in_lock or d.only_in_manifest:
            lines.append(f"Against {d.lock.name}:")
            if d.only_in_lock:
                lines.append(
                    f"  · {_plural(len(d.only_in_lock), 'package', 'packages')} in the lock and not "
                    f"asked for directly: {', '.join(d.only_in_lock[:6])}"
                )
            if d.only_in_manifest:
                lines.append(
                    f"  · {_plural(len(d.only_in_manifest), 'package', 'packages')} asked for and "
                    f"not in the lock: {', '.join(d.only_in_manifest[:6])}"
                )
        else:
            lines.append(f"Against {d.lock.name}: the same set of names, so the lock is not stale.")
        lines.append("")
    elif report.no_lock:
        lines.append(f"{report.no_lock[0].upper()}{report.no_lock[1:]}.")
        lines.append("")

    if report.includes:
        named = ", ".join(p.name for p in report.includes[:4])
        lines.append(f"This file includes others ({named}); they were not followed.")
        lines.append("")

    lines.append(
        "This says what an install will run, not whether running it is acceptable — that is your "
        "call. Nothing here was executed, no advisory database was consulted, and the network was "
        "never opened."
    )
    return "\n".join(lines) + "\n"


def report_to_dict(report: Report) -> dict[str, Any]:
    """The same report as data, including the four things this tool did not do."""
    return {
        "manifest": str(report.manifest),
        "requirements": report.total,
        "read": report.examined,
        "complete": report.complete,
        "unexamined": [{"name": u.name, "why": u.why} for u in report.unexamined],
        "lockfile_only": [
            {"name": e.name, "version": e.version, "declares_install_script": e.runs_at_install}
            for e in report.partial
        ],
        "runs_at_install": [
            {
                "name": e.name,
                "version": e.version,
                "kind": e.kind,
                "hooks": [{"where": h.where, "what": h.what} for h in e.hooks],
            }
            for e in report.runs_at_install
        ],
        "native_payloads": [
            {"name": e.name, "version": e.version, "files": list(e.native[:20]), "count": len(e.native)}
            for e in report.with_native
        ],
        "startup_hooks": [
            {"name": e.name, "version": e.version, "files": list(e.startup_hooks)}
            for e in report.with_startup_hooks
        ],
        "off_index": [
            {"name": r.name, "source": r.source, "where": r.where, "note": r.note}
            for r in report.off_index
        ],
        "lock": (
            {
                "path": str(report.delta.lock),
                "only_in_lock": list(report.delta.only_in_lock),
                "only_in_manifest": list(report.delta.only_in_manifest),
            }
            if report.delta
            else None
        ),
        "lock_note": report.no_lock,
        "claims": {
            "executed_anything": False,
            "consulted_an_advisory_database": False,
            "opened_the_network": False,
            "says_whether_this_is_safe": False,
        },
    }


def report_to_json(report: Report) -> str:
    """`report_to_dict`, serialised, for `--json`."""
    return json.dumps(report_to_dict(report), indent=2)
