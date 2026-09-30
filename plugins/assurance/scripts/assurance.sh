#!/bin/sh
# Runs `assurance` at the version this plugin release pins.
#
# Claude Code can start hooks without the PATH your terminal has (the macOS desktop app gives them only
# /usr/bin:/bin:/usr/sbin:/sbin), so the places uv installs `uvx` go on the end of it, and `uvx` is
# called by name; on Windows this runs in Git Bash, which finds `uvx.exe`. Without uv, an `assurance`
# installed with pip is used. Without either, the hook says nothing was audited and lets the session
# end: an audit tool must never be the reason a session breaks.
#
# For the same reason the hook runs the copy uv already has without the network. The version is
# pinned, so that copy is the right one, and PyPI being out of reach (a proxy, a private mirror, an
# outage) stops mattering after the first run. When the first run cannot fetch it, the turn is
# reported as not audited: uv exits 2 when it cannot reach the index, and a Stop hook that exits 2
# tells Claude to keep going.
VERSION=0.1.16
PATH="$PATH:$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin"
export PATH

if command -v uvx >/dev/null 2>&1; then
  case " $* " in
    *" --hook "*) ;;
    *)
      # By hand or from /assurance:audit. uv's own progress lines ("Installed 8 packages in 7ms" on a
      # first run) are left out of what it prints; uv's errors and the audit's own messages are not.
      { err=$(uvx assurance==0.1.16 "$@" 2>&1 >&3); } 3>&1
      status=$?
      [ -n "$err" ] && printf '%s\n' "$err" | grep -v -E '^(Downloading|Downloaded|Installed|Prepared|Resolved|Uninstalled|Audited|Built|Building|Updated) ' >&2
      exit "$status"
      ;;
  esac
  input=$(cat)
  printf '%s' "$input" | UV_OFFLINE=1 uvx assurance==0.1.16 "$@" 2>/dev/null && exit 0
  { err=$(printf '%s' "$input" | uvx assurance==0.1.16 "$@" 2>&1 >&3); } 3>&1 && exit 0
  why=$(printf '%s\n' "$err" | grep -m 1 '^error:' | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '{"systemMessage": "assurance: %s did not run%s, so this turn was not audited."}\n' "$VERSION" "${why:+ ($why)}"
  exit 0
fi

if command -v assurance >/dev/null 2>&1; then
  exec assurance "$@"
fi

case " $* " in
  *" --hook "*)
    printf '%s\n' '{"systemMessage": "assurance: neither uvx nor assurance was found, so this turn was not audited. Install uv (https://docs.astral.sh/uv/) or run: pip install assurance"}'
    exit 0
    ;;
esac
echo "assurance: neither uvx nor assurance was found. Install uv (https://docs.astral.sh/uv/) or run: pip install assurance" >&2
exit 2
