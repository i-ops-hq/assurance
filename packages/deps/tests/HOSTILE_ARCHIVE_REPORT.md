# Hostile archive suite — Round 2 report

Branch: `test/deps-hostile-archives`  
Package: `packages/deps` (`assurance_deps` **0.2.3**)  
Production code: **fixed** (Round 1 tests kept; parent-tree test strengthened first)

```bash
python -m pytest -q packages/deps/tests
cd packages/deps && python -m mypy --strict assurance_deps --python-version 3.11
```

Result: **88 passed, 1 skipped**. mypy: Success. Sibling floors: 2 passed.

## Test that proved less than its name (fixed first)

`test_hostile_nothing_written_outside_tree.py` asserted only the canary's exact path. Mutating
`examine_archive` to write any other new file into the parent passed all five tests.

**Now:** snapshot the parent tree before fixtures, allowlist exactly the relative paths the test
creates, assert membership is otherwise unchanged (`assert_parent_membership_unchanged`).

Mutation re-check:
- Stray write `assurance-deps-stray-not-canary` → fails with `unexpected new paths ...`
- Canary-path rewrite → all five fail via `assert_unchanged`

## Wrong Round-1 test (said so, corrected)

`test_symlinked_manifest_pointing_outside_folder_is_not_followed_as_trusted_input` expected a
caller-named manifest symlink to be refused. Round 2 clarifies: **a path the caller names may be
followed; a path discovery finds may not.** That Round-1 test was wrong under the clarified rule.

Replaced with:
- `test_discovered_archive_symlink_out_of_tree_is_unread_not_followed` — discovery refuse
- `test_caller_named_manifest_symlink_may_be_followed` — documents the allowed case

## Per defect: before → after

### 1. Encrypted wheel → traceback

**Before:** `RuntimeError: ... is encrypted, password required` escaped `examine_archive`; CLI
exited 1 with a traceback.

**After (CLI):**
```
requirements.txt — 1 requirement, 0 read in full
Could not be examined at all (1):
  · enc                    encrypted entry could not be read: enc-1.0.dist-info/METADATA (...)
```
No traceback. Denominator stays 1; read = 0.

### 2. Discovery symlink out of tree → "read in full"

**Before:** `wheels/evil-1.0-....whl` → outside wheel was examined like a real archive.

**After (CLI):**
```
  · evil                   symlink out of the tree
```
`--json` reports the same reason; requirement still counted.

### 3. Raw ANSI in refusal / report / JSON

**Before:** `\x1b[31m` reached stderr and `format_report` text raw.

**After (CLI refusal):**
```
  line 1: hello \u001b[31mworld
```
No raw ESC. `--json` string fields pre-scrubbed; `json.dumps` remains valid.

### 4–6. Tar SYMTYPE / FIFO / CHR / hardlink silent skip

**Before:** `note=''`, `readable=True` — clean bill of health.

**After:** note names each kind, e.g. `3 non-file members not read: symlink …, fifo …, …`.
Archive is unread (named), not silently complete.

### 7. Zip MAX_MEMBERS silent truncate

**Before:** sliced `infolist()[:MAX_MEMBERS]` with empty note.

**After:** `note='stopped after N entries'` (same shape as tar).

### 8. 10 MB single-line requirements

**Before:** accepted as one distribution name (`len == 10_000_000`).

**After:** line skipped; `unparsed` / report limits name the `100_000` character cap; no fabricated
package name.

### 9. package.json nested 10_000 deep

**Before:** uncaught `RecursionError`.

**After:** `err` names that the file nests too deeply (limit named at 200 levels); no crash.

### 10. Terminal escapes in format_report

**Before:** CSI / OSC / RTL / NUL interpolated unchanged.

**After:** `scrub_controls` on text and JSON string fields.

## Counterfactuals (revert → fail → restore → clear `__pycache__`)

| Broke | Test(s) that failed |
|---|---|
| Removed zip MAX_MEMBERS note | `test_zip_with_far_more_entries_than_cap_states_the_gap` |
| Dropped RuntimeError handling for encrypted zip | `test_encrypted_zip_entry_is_named_unreadable_not_expanded` |
| Restored silent tar non-file skip | symlink / fifo hostile tar tests |
| Discovery follows symlinks again | `test_discovered_archive_symlink_out_of_tree_is_unread_not_followed` |
| Removed `scrub_controls` from `format_report` | both `test_hostile_terminal_escapes` tests |
| Removed line-length cap | `test_ten_megabyte_single_line_requirements_is_bounded` |
| Dropped `RecursionError` catch in JSON load | `test_package_json_nested_ten_thousand_deep_is_bounded` |

Each restored; full suite green again (88 passed, 1 skipped).

## Scope

Only `packages/deps`. Version **0.2.3**. No other packages, CI, or sibling versions touched.
