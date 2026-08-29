"""`assurance diff` — coverage over any two sets, with no folder and no date format involved.

`check` answers whether a folder's dated files are contiguous. That is one shape of question and it
is not most people's. These defend the general form: two lists of keys, an honest denominator, and a
sentence that names what the second list missed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_cli.cli import main
from assurance_cli.setdiff import KeySpecError, diff_sets, read_keys


# --- reading a key set from what people actually have ----------------------------------------------


def test_an_inline_comma_list_is_a_key_set() -> None:
    assert read_keys("a, b ,c", label="--expected") == ["a", "b", "c"]


def test_a_file_of_one_key_per_line(tmp_path: Path) -> None:
    listing = tmp_path / "keys.txt"
    listing.write_text("# the corpus\ndoc-1\n\ndoc-2\n", encoding="utf-8")

    assert read_keys(str(listing), label="--expected") == ["doc-1", "doc-2"]


def test_a_hash_inside_a_key_is_part_of_the_key(tmp_path: Path) -> None:
    """Only a full-line comment is dropped. Guessing would silently change someone's denominator."""
    listing = tmp_path / "keys.txt"
    listing.write_text("sha256#abc\n# a comment\n", encoding="utf-8")

    assert read_keys(str(listing), label="--expected") == ["sha256#abc"]


def test_a_json_list_of_strings(tmp_path: Path) -> None:
    listing = tmp_path / "found.json"
    listing.write_text('["chunk-a", "chunk-b"]', encoding="utf-8")

    assert read_keys(str(listing), label="--found") == ["chunk-a", "chunk-b"]


@pytest.mark.parametrize("field", ["key", "id", "name", "path"])
def test_a_json_list_of_objects_uses_the_first_identifying_field(tmp_path: Path, field: str) -> None:
    listing = tmp_path / "found.json"
    listing.write_text(json.dumps([{field: "ex-1"}, {field: "ex-2"}]), encoding="utf-8")

    assert read_keys(str(listing), label="--found") == ["ex-1", "ex-2"]


def test_duplicate_keys_collapse() -> None:
    assert read_keys("a,a,b", label="--expected") == ["a", "b"]


# --- refusing to guess ------------------------------------------------------------------------------


def test_a_json_object_without_a_keys_list_is_refused(tmp_path: Path) -> None:
    """An object of id → metadata is ambiguous, and a denominator we invent is one nobody can argue
    with. The refusal names the fix."""
    listing = tmp_path / "found.json"
    listing.write_text('{"doc-1": {"score": 0.9}}', encoding="utf-8")

    with pytest.raises(KeySpecError, match='"keys" list'):
        read_keys(str(listing), label="--found")


def test_a_json_object_with_a_keys_list_is_accepted(tmp_path: Path) -> None:
    listing = tmp_path / "found.json"
    listing.write_text('{"keys": ["doc-1"], "note": "top-k"}', encoding="utf-8")

    assert read_keys(str(listing), label="--found") == ["doc-1"]


def test_an_object_entry_with_no_identifying_field_is_refused(tmp_path: Path) -> None:
    listing = tmp_path / "found.json"
    listing.write_text('[{"score": 0.9}]', encoding="utf-8")

    with pytest.raises(KeySpecError, match="no key/id/name/path"):
        read_keys(str(listing), label="--found")


def test_malformed_json_says_so_rather_than_being_read_as_one_long_key(tmp_path: Path) -> None:
    listing = tmp_path / "found.json"
    listing.write_text('["doc-1",', encoding="utf-8")

    with pytest.raises(KeySpecError, match="does not parse"):
        read_keys(str(listing), label="--found")


# --- the diff itself ---------------------------------------------------------------------------------


def test_the_gap_is_named_in_the_locus_the_caller_gave() -> None:
    payload = diff_sets(
        "doc-1,doc-2,doc-3",
        "doc-1",
        scope="documents the question spans",
        where="the retrieved set",
    )

    assert payload["complete"] is False
    assert payload["summary"] == (
        "1 of 3 documents the question spans — not in the retrieved set: doc-2, doc-3"
    )


def test_something_read_outside_the_scope_is_reported_and_does_not_earn_credit() -> None:
    """The retriever pulled a chunk from outside the declared filter. That is worth knowing, and it
    is not coverage — so it appears, and the ratio does not move."""
    payload = diff_sets("a,b,c", "a,z")

    assert payload["read"] == 1
    assert payload["required"] == 3
    assert payload["unexpected"] == ["z"]
    assert payload["complete"] is False


def test_a_full_read_is_complete() -> None:
    assert diff_sets("a,b", "b,a")["complete"] is True


# --- the command ---------------------------------------------------------------------------------


def test_fail_on_gap_is_what_makes_it_a_ci_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "--expected", "a,b", "--found", "a", "--fail-on-gap"]) == 1
    assert main(["diff", "--expected", "a,b", "--found", "a"]) == 0
    assert main(["diff", "--expected", "a,b", "--found", "a,b", "--fail-on-gap"]) == 0


def test_json_output_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    main(["diff", "--expected", "a,b", "--found", "a", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["required"] == 2
    assert [entry["key"] for entry in payload["missing"]] == ["b"]


def test_an_error_is_printed_rather_than_swallowed(capsys: pytest.CaptureFixture[str]) -> None:
    """Text mode used to render an error as a blank line, so a bad path looked like a tool that had
    silently done nothing. Diagnostics go to stderr; stdout stays clean for a pipeline."""
    assert main(["check", "/nope/not/here"]) == 2

    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert "folder does not exist" in captured.err
