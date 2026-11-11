"""The Claude Code plugin in this repository: what `claude plugin install assurance@i-ops-hq` gets.

The plugin used to ship a Stop hook and a shell script. It no longer ships either, and most of this
file is now the rules that say so.

Anthropic's plugin directory accepts hooks, inline shell, pre-approved tools and files outside a
short allowed list only from organizations it has verified, and i-ops-hq is not one. Every version
after 0.1.30 was refused for four of those at once, so 930 installs sat on 0.1.30 from 13 October.
The hook did not go away — it moved to `assurance hook install`, where the user installs it into
their own settings and the directory has no say. What is left here is a skill that asks Claude to
run the audit, and Claude asks the user before it does.

The four rules are tested as rules rather than as "the files we happen to ship", because the cost of
breaking one is a refused release and another week on an old version.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / "plugins" / "assurance"

pytestmark = pytest.mark.skipif(not MARKETPLACE.is_file(), reason="not running from a source checkout")

#: What the directory accepts from an organization it has not verified. SVG is in: "Include only
#: text files, SVG included, complete PNG, JPEG, GIF, and WebP images, and font files."
ALLOWED_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"}

#: `allowed-tools` may name these four and nothing else, with no `${...}`, no `//` or `\\` path and
#: no URL.
ALLOWED_TOOLS = {"Read", "Grep", "Glob", "LS"}


def _release() -> str:
    match = re.search(r'^version = "([^"]+)"', (PACKAGE / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert match
    return match.group(1)


def _manifest() -> dict[str, object]:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


def _shipped() -> list[Path]:
    return sorted(p for p in PLUGIN.rglob("*") if p.is_file())


# --- what the directory refuses from an unverified organization ---------------------------------------


def test_the_plugin_ships_no_hook_of_any_type() -> None:
    """LOCAL_CODE_HOOK_COMMAND. "Changing the type won't help: this rule covers hooks of every
    type", so this checks for a hooks file and for a `hooks` key in the manifest, not for `command`.
    """
    assert not (PLUGIN / "hooks").exists(), "hooks/ is back"
    assert "hooks" not in _manifest(), "the manifest declares hooks"


def test_the_plugin_ships_no_file_the_directory_refuses() -> None:
    """LOCAL_CODE_FILE_TYPE. Not an allowed type, or starting with `#!`, and it is refused."""
    offenders = []
    for path in _shipped():
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            offenders.append(f"{path.relative_to(PLUGIN)}: {path.suffix or 'no suffix'} is not an allowed type")
            continue
        if path.read_bytes()[:2] == b"#!":
            offenders.append(f"{path.relative_to(PLUGIN)}: starts with a shebang")
        if b"\x00" in path.read_bytes():
            offenders.append(f"{path.relative_to(PLUGIN)}: holds a NUL byte")
    assert not offenders, "\n".join(offenders)


def test_no_skill_or_command_carries_inline_shell() -> None:
    """LOCAL_CODE_INLINE_SHELL. An exclamation mark then a backticked command, or a fenced block
    marked with one, runs on the user's machine at render time without asking. The skill tells
    Claude to run the audit instead, and Claude asks."""
    offenders = []
    for path in _shipped():
        if path.suffix != ".md":
            continue
        body = path.read_text(encoding="utf-8")
        for number, line in enumerate(body.splitlines(), 1):
            if re.search(r"(?:^|\s)!`", line) or line.strip().startswith("```!"):
                offenders.append(f"{path.relative_to(PLUGIN)}:{number}: {line.strip()[:70]}")
    assert not offenders, "\n".join(offenders)


def test_no_frontmatter_pre_approves_a_tool() -> None:
    """LOCAL_CODE_TOOL_GRANT. `allowed-tools` pre-approves a tool so Claude can use it without
    asking. Cut to Read/Grep/Glob/LS, or absent — absent is what we ship, because the audit should
    be approved each time it runs."""
    offenders = []
    for path in _shipped():
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        front = text.split("---")[1]
        for key in ("allowed-tools", "allowedTools"):
            found = re.search(rf"^{key}:(.*)$", front, re.M)
            if not found:
                continue
            named = {t.strip() for t in re.split(r"[,\s]+", found.group(1)) if t.strip()}
            if not named <= ALLOWED_TOOLS:
                offenders.append(f"{path.relative_to(PLUGIN)}: {key}: {found.group(1).strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_whole_plugin_is_five_readable_files() -> None:
    """A reader should be able to get through all of it, which is the claim the README makes."""
    assert sorted(str(p.relative_to(PLUGIN)) for p in _shipped()) == [
        ".claude-plugin/icon.svg",
        ".claude-plugin/plugin.json",
        "PRIVACY.md",
        "README.md",
        "skills/audit/SKILL.md",
    ]


# --- the listing, which is what the directory shows ----------------------------------------------------


def test_the_marketplace_lists_the_plugin_under_the_name_it_installs_by() -> None:
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert marketplace["name"] == "i-ops-hq"
    (entry,) = marketplace["plugins"]
    # The entry name is what people type; the manifest name prefixes the skills. They must agree.
    assert entry["name"] == _manifest()["name"] == "assurance"
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_the_listing_is_the_same_in_both_places_and_does_not_say_no_network() -> None:
    # Until 0.1.9 the listing, which is what Anthropic's directory shows, ended "No model, no
    # network, no account.", and the audit fetches its pinned package the first time it runs. The
    # listing is written twice, in the manifest and in the marketplace entry, so the two are kept
    # equal. Saying "no network beyond fetching" the package is true, and allowed.
    (entry,) = json.loads(MARKETPLACE.read_text(encoding="utf-8"))["plugins"]
    listing = _manifest()["description"]
    assert isinstance(listing, str) and entry["description"] == listing
    readme = " ".join((PLUGIN / "README.md").read_text(encoding="utf-8").split())
    for text in (listing, readme):
        assert not re.search(r"no network(?! beyond fetching)", text, re.I), text


def test_the_listing_does_not_promise_an_automatic_audit_any_more() -> None:
    """The plugin reports when asked. It said "After each turn, checks..." until 0.1.39, which was
    true of the hook it used to ship and is not true of a skill."""
    (entry,) = json.loads(MARKETPLACE.read_text(encoding="utf-8"))["plugins"]
    for text in (str(_manifest()["description"]), entry["description"]):
        assert not re.search(r"after (each|every) turn", text, re.I), text


def test_the_readme_links_the_privacy_policy_the_directory_asks_for() -> None:
    # Anthropic's directory asks a plugin that reads personal data for a privacy policy, as
    # `privacyPolicyUrl` in plugin.json or a "Privacy" link in the README. `claude plugin validate`
    # (2.1.86) refuses the key as unrecognised, and a manifest an older Claude Code refuses is a
    # plugin it will not load, so the README carries the link, to this folder's PRIVACY.md on main.
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    linked = re.findall(r"\[Privacy\]\(https://github\.com/i-ops-hq/assurance/blob/main/([^)\s]+)\)", readme)
    assert linked == ["plugins/assurance/PRIVACY.md"], linked
    assert (ROOT / linked[0]).is_file()
    assert "privacyPolicyUrl" not in _manifest()


def test_the_plugin_has_an_icon_the_directory_accepts() -> None:
    # Anthropic's directory warns without one: `.claude-plugin/icon.svg`, square, at least 128px.
    svg = (PLUGIN / ".claude-plugin" / "icon.svg").read_text(encoding="utf-8")
    view = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert svg.lstrip().startswith("<svg") and view
    width, height = int(view.group(1)), int(view.group(2))
    assert width == height >= 128


# --- the skill -----------------------------------------------------------------------------------------


def test_the_plugin_offers_one_skill_and_it_does_the_job() -> None:
    # A governance tool showed two skills, and the second ran nothing and pointed to another product
    # (`board`, removed in 0.1.31): half of what a new user saw in its menu was an advertisement. The
    # menu holds the audit and nothing else.
    skills = sorted(path.name for path in (PLUGIN / "skills").iterdir() if path.is_dir())
    assert skills == ["audit"], skills


def test_the_audit_skill_costs_nothing_until_you_run_it() -> None:
    front = (PLUGIN / "skills" / "audit" / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    # Only the user can invoke it, so its description is not in Claude's context on every turn.
    assert "disable-model-invocation: true" in front


def test_the_skill_runs_the_release_it_ships_with() -> None:
    """The version the skill names must be the one this release publishes, and must be written out
    rather than built from a variable: a reader sees exactly what will run, and so does the
    directory, which refuses a launcher it cannot read."""
    text = (PLUGIN / "skills" / "audit" / "SKILL.md").read_text(encoding="utf-8")
    # `[\w.+!-]+` rather than `\S+?\b`, which stops at the first dot and matches just "0".
    pinned = set(re.findall(r"\buvx assurance@([\w.+!-]+)", text))
    assert pinned == {_release()}, pinned
    assert _manifest()["version"] == _release()
    assert not re.search(r"uvx assurance@\$", text), "the version is computed, not named"


def test_the_skill_says_the_session_id_is_required() -> None:
    """Without `--session` the audit guesses which transcript is this session's by reading the start
    of each newer one, and guesses wrong as often as not. The instruction has to say so, because
    there is no longer a script enforcing it."""
    text = (PLUGIN / "skills" / "audit" / "SKILL.md").read_text(encoding="utf-8")
    assert 'audit --session "$CLAUDE_SESSION_ID"' in text
    assert "not optional" in text


def test_the_skill_points_at_the_package_for_the_automatic_audit() -> None:
    """The hook did not disappear, it moved. If the skill stops saying so, the plugin has quietly
    become a smaller product rather than a differently packaged one."""
    text = (PLUGIN / "skills" / "audit" / "SKILL.md").read_text(encoding="utf-8")
    assert "hook install" in text
