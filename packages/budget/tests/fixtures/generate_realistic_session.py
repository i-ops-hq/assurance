#!/usr/bin/env python3
"""Generate packages/budget/tests/fixtures/realistic-session.jsonl — synthetic only, no real paths."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "realistic-session.jsonl"
SID = "a1b2c3d4-fixture-session"
CWD = "/workspace/demo-app"
T0 = "2026-09-01T10:00:00.000Z"


def L(**fields: object) -> str:
    return json.dumps(fields, separators=(",", ":"))


def tool_use(tid: str, name: str, **inp: object) -> dict[str, object]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid: str, content: object, *, is_error: bool = False) -> dict[str, object]:
    return {
        "type": "tool_result",
        "tool_use_id": tid,
        "content": content,
        "is_error": is_error,
    }


def assistant(blocks: list[dict[str, object]], ts: str) -> str:
    return L(
        type="assistant",
        sessionId=SID,
        timestamp=ts,
        cwd=CWD,
        message={"role": "assistant", "content": blocks},
    )


def user(content: object, ts: str) -> str:
    return L(
        type="user",
        sessionId=SID,
        timestamp=ts,
        cwd=CWD,
        message={"role": "user", "content": content},
    )


def pair(tid: str, name: str, ts_a: str, ts_u: str, result: object = "ok", *, is_error: bool = False, **inp: object) -> list[str]:
    return [
        assistant([tool_use(tid, name, **inp)], ts_a),
        user([tool_result(tid, result, is_error=is_error)], ts_u),
    ]


def main() -> None:
    lines: list[str] = []
    # Bookkeeping + user/assistant turns
    lines.append(L(type="summary", sessionId=SID, summary="fixture session"))
    lines.append(user("please improve the demo app", "2026-09-01T10:00:01.000Z"))
    lines.append(
        assistant([{"type": "text", "text": "I will inspect and edit."}], "2026-09-01T10:00:02.000Z")
    )

    # Two unknown record types (not_read reasons)
    lines.append(L(type="progress", sessionId=SID, timestamp="2026-09-01T10:00:03.000Z", data={"n": 1}))
    lines.append(L(type="progress", sessionId=SID, timestamp="2026-09-01T10:00:04.000Z", data={"n": 2}))
    lines.append(
        L(
            type="assistant",
            sessionId=SID,
            timestamp="2026-09-01T10:00:05.000Z",
            cwd=CWD,
            message={
                "role": "assistant",
                "content": [{"type": "server_tool_use", "name": "x", "input": {}}],
            },
        )
    )

    n = 0

    def bash(cmd: str, *, err: bool = False, result: str = "ok") -> None:
        nonlocal n
        n += 1
        tid = f"b{n}"
        minute = 10 + (n // 60)
        second = n % 60
        ts_a = f"2026-09-01T10:{minute:02d}:{second:02d}.000Z"
        ts_u = f"2026-09-01T10:{minute:02d}:{second:02d}.500Z"
        lines.extend(pair(tid, "Bash", ts_a, ts_u, result, is_error=err, command=cmd))

    # §1 cases: heredocs, venv pytest, TZ=, uv run, for-loop, quoted pipe in python -c
    bash("cat > notes.md <<'EOF'\nSee .assurance/config.toml for limits.\nAlso more notes.\nEOF")
    bash("/tmp/x/venv/bin/python -m pytest -q")
    bash("TZ=UTC pytest -q")
    bash("cd pkg && uv run pytest -q")
    bash("for f in a b; do pytest tests/$f.py; done")
    bash('python -c "print(1|2)"')  # stays unclassified — intentional

    # git reads + git writes
    bash("git status")
    bash("git log -1 --oneline")
    bash("git diff")
    bash("git branch")
    bash("git rev-parse HEAD")
    bash("git ls-files")
    bash("git blame src/app.py")
    bash("git stash list")
    bash("git tag -l")
    bash("git remote -v")
    bash("git commit -m 'wip'")  # unclassified
    bash("git push origin HEAD")  # unclassified

    # more classified commands
    bash("make test")
    bash("make check")
    bash("npm run typecheck")
    bash("python -m mypy packages/budget")
    bash("python -m ruff check .")
    bash("black --check .")
    bash("pnpm lint")
    bash("timeout 600 pytest -q")
    bash("bun test")
    bash("python -m unittest")
    bash("pnpm run test")
    bash("ls -la")
    bash("pwd")
    bash("pip list")
    bash("python --version")
    bash("env")
    bash("sed -n '1,20p' src/app.py")
    bash("awk '{print NR}' src/app.py")
    bash("sort names.txt")
    bash("jq . package.json")
    bash("diff a.txt b.txt")
    bash("stat src/app.py")
    bash("tree -L 2")
    bash("realpath src/app.py")
    bash("npm ls")
    bash("git show HEAD:src/app.py")
    bash("nice -n 5 pytest -q")
    bash("poetry run pytest -q")
    bash("cd src")  # neutral alone → not unclassified
    bash("true")
    bash("export DEMO=1")
    bash("mkdir -p build")  # unclassified
    bash("rm -rf build")  # unclassified

    # §3 limits-file cases (bash)
    bash("W=/tmp/x; cd $W && echo '[budget]' > .assurance/config.toml")  # must NOT count
    bash("echo '[budget]' > .assurance/config.toml")  # DOES count
    bash("printf x | tee ./.assurance/config.toml")  # DOES count
    bash("sed -i 's/400/9999/' .assurance/config.toml")  # DOES count
    bash(f"cp /tmp/c.toml {CWD}/.assurance/config.toml")  # DOES count

    # Reads of project files
    lines.extend(
        pair(
            "r1",
            "Read",
            "2026-09-01T11:00:00.000Z",
            "2026-09-01T11:00:01.000Z",
            "print('hi')\n",
            file_path="src/app.py",
        )
    )
    lines.extend(
        pair(
            "r2",
            "Read",
            "2026-09-01T11:00:02.000Z",
            "2026-09-01T11:00:03.000Z",
            "x = 1\n",
            file_path="src/util.py",
        )
    )

    # §2 Write-then-Edit (should NOT report edited without read)
    lines.extend(
        pair(
            "w1",
            "Write",
            "2026-09-01T11:01:00.000Z",
            "2026-09-01T11:01:01.000Z",
            "ok",
            file_path="src/new_module.py",
            content="def f():\n    return 1\n",
        )
    )
    lines.extend(
        pair(
            "e1",
            "Edit",
            "2026-09-01T11:01:02.000Z",
            "2026-09-01T11:01:03.000Z",
            "ok",
            file_path="src/new_module.py",
            old_string="return 1",
            new_string="return 2",
        )
    )

    # §2 failed Edit with no Read — should NOT report
    lines.extend(
        pair(
            "e2",
            "Edit",
            "2026-09-01T11:02:00.000Z",
            "2026-09-01T11:02:01.000Z",
            "File not found",
            is_error=True,
            file_path="src/missing.py",
            old_string="a",
            new_string="b",
        )
    )

    # §2 Edit without Read — SHOULD report
    lines.extend(
        pair(
            "e3",
            "Edit",
            "2026-09-01T11:03:00.000Z",
            "2026-09-01T11:03:01.000Z",
            "ok",
            file_path="src/orphan.py",
            old_string="a",
            new_string="b",
        )
    )

    # §4 scratch edit outside cwd — must NOT restart after-last-edit clock
    lines.extend(
        pair(
            "e4",
            "Edit",
            "2026-09-01T11:04:00.000Z",
            "2026-09-01T11:04:01.000Z",
            "ok",
            file_path="/tmp/scratch/notes.md",
            old_string="old",
            new_string="new",
        )
    )

    # In-cwd edit that sets the last-edit point, then a test
    lines.extend(
        pair(
            "e5",
            "Edit",
            "2026-09-01T11:05:00.000Z",
            "2026-09-01T11:05:01.000Z",
            "ok",
            file_path="src/app.py",
            old_string="hi",
            new_string="hello",
        )
    )
    bash("pytest -q tests/")

    # Duration under 24h so header stays "Xh Y min" style — started 10:00, last ~11:05+bash
    # Add a few more reads to pad tool-call count toward 60–100
    for i, cmd in enumerate(
        [
            "head -n 5 src/app.py",
            "tail -n 5 src/app.py",
            "wc -l src/app.py",
            "grep -n def src/app.py",
            "find . -name '*.py'",
            "which pytest",
            "echo done",
            "cat package.json",
            "git status -sb",
            "python -m pytest tests/test_app.py -q",
        ]
    ):
        bash(cmd)

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Count tool calls roughly
    tools = sum(1 for line in lines if '"tool_use"' in line)
    print(f"Wrote {OUT} with ~{tools} tool_use blocks, {len(lines)} lines")


if __name__ == "__main__":
    main()
