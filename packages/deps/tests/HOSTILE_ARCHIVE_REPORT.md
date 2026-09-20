# Hostile archive suite — report

Branch: `test/deps-hostile-archives`  
Package: `packages/deps` (`assurance_deps`)  
Production code: **unchanged**  
Command:

```bash
python -m pytest -q packages/deps/tests/test_hostile_*.py
cd packages/deps && python -m mypy --strict assurance_deps --python-version 3.11
```

Result on this machine: **10 failed, 25 passed**. Failures are left red on purpose.

## Invariants

### 1. Nothing written outside the temp tree — HOLDS (for the cases exercised)

Canary in the parent directory; relative/absolute/Windows zip traversal; symlink members. Canary bytes and mtime unchanged. Full `scan_manifest` over a hostile sdist also leaves the canary alone.

Counterfactual: patched `examine_archive` to rewrite `assurance-deps-canary-*` in the parent. Test failed with `AssertionError` in `assert_unchanged`. Restored; cleared `__pycache__`; green again.

### 2. Nothing executed / nothing imported — HOLDS (guarded path)

`subprocess.Popen` / `run` / `os.system` / `os.exec*` / `posix_spawn` raise for the duration of every parse. Planted `hostile_planted_*` modules must not appear in `sys.modules`.

Counterfactuals:
- Inserted `os.system("true")` at the top of `examine_archive` → `ExecutionAttempted`. Restored → green.
- Injected `sys.modules["hostile_planted_setup_side_effect"] = ...` → assertion on planted modules. Restored → green.

### 3. No symlink followed out of the tree (archives) — PARTIAL / DEFECT

**Defect:** `_read_tar` does `if not member.isfile(): continue` and never sets `note`. Symlink, fifo, char-device, and hardlink members are omitted with `note=''` and `readable=True`.

| Test | Input | Output |
|---|---|---|
| `test_symlink_to_etc_passwd_is_reported...` | SYMTYPE → `/etc/passwd` + PKG-INFO | `Examined(..., members=1, note='')` |
| `test_symlink_pointing_outside_archive_root...` | SYMTYPE → `../outside-secret.txt` | `note=''`, `members=1` |
| `test_fifo_char_device_and_hardlink...` | FIFO + CHR + REG + LNK | `members=2, note=''` |

The outside file was not read into a package name (no content follow on this path), but the archive still looks fully readable — silent skip, which the promise forbids.

### 4. Bounded — PARTIAL / DEFECTS

- Member above `MAX_MEMBER_BYTES` (3MB setup.py): returns quickly — holds.
- Truncated gzip: `note='could not be opened: ...'`, `readable=False` — holds.
  - Counterfactual: except-handler returned `note=""` → test failed. Restored → green.
- Zip truncated at `MAX_MEMBERS`: **DEFECT** — tar sets `note=f"stopped after {MAX_MEMBERS} entries"`; zip slices `infolist()[:MAX_MEMBERS]` with **empty note**, `members=50`, looks complete.
- Encrypted zip entry: **DEFECT** — `RuntimeError: ... is encrypted, password required` escapes. `examine_archive` catches `BadZipFile`/`OSError`/`EOFError` only.
- 10MB single-line requirements.txt: **DEFECT** — accepted as one distribution name (`len(name)==10_000_000`). No size bound.
- package.json nested 10_000 deep: **DEFECT** — uncaught `RecursionError` from `json.loads` (not turned into `err=`).

### 5. What could not be read is named — FAILS on several shapes

Covered by the silent tar skips, silent zip truncation, encrypted RuntimeError, deep-JSON crash, and:

**Defect:** `Path.read_text` follows a symlinked `requirements.txt` out of the project.  
Input: `proj/requirements.txt` → `../outside-requirements.txt` containing `definitely-not-a-real-package-zzz==1.0.0`.  
Output: parsed requirements include that name. Attacker-controlled path becomes a trusted manifest.

### 6. Escapes do not reach the terminal raw — FAILS

`format_report` interpolates names and hook bodies unchanged.

| Test | Input | Output fragment |
|---|---|---|
| ANSI / OSC-8 in name + hook | `\x1b[31mevil\x1b[0m`, `echo \x1b]8;;https://evil\x07click` | raw ESC in report text |
| NUL in unexamined reason | `could not read\x00hidden` | NUL in report text |

## Counterfactuals run

| # | Guard broken | Test | Failure |
|---|---|---|---|
| 1 | `examine_archive` writes sibling canary | `test_tar_member_named_relative_escape_does_not_write_canary` | `assert_unchanged` |
| 2 | `os.system("true")` in `examine_archive` | `test_examine_archive_under_blocked_exec...` | `ExecutionAttempted` |
| 3 | Corrupt-archive handler returns empty note | `test_truncated_gzip_and_corrupt_tar...` | `note=''` |
| 4 | Plant module in `sys.modules` | same exec/import test | planted module listed |
| 5 | npm non-object returns `err=""` | `test_package_json_that_is_array...` | `assert err` |

Each restored; `__pycache__` cleared; re-run green for those tests. Production tree clean (`git diff packages/deps/assurance_deps` empty).

## mypy

`python -m mypy --strict assurance_deps --python-version 3.11` → Success.  
On a 3.10 interpreter without `--python-version 3.11`, mypy reports missing `tomllib` stubs (stdlib 3.11+). Pre-existing; not introduced by tests.

## Files added (tests only)

- `hostile_helpers.py` — fixtures, canary, exec/import guards
- `conftest.py` — puts tests/ on `sys.path`
- `test_hostile_nothing_written_outside_tree.py`
- `test_hostile_nothing_executed_or_imported.py`
- `test_hostile_tar_members_named_not_followed.py`
- `test_hostile_zip_bounds_and_structure.py`
- `test_hostile_npm_manifest_shapes.py`
- `test_hostile_manifests_on_disk.py`
- `test_hostile_terminal_escapes.py`
- this report
