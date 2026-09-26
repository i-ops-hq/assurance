#!/bin/sh
# Runs `assurance` at the version this plugin release pins.
#
# Claude Code can start hooks without the PATH your terminal has (the macOS desktop app gives them only
# /usr/bin:/bin:/usr/sbin:/sbin), so `uvx` is looked for where uv installs it, not only on PATH; on
# Windows this runs in Git Bash, where it is `uvx.exe`. Without uv, an `assurance` installed with pip
# is used. Without either, the hook says nothing was audited and lets the session end: an audit tool
# must never be the reason a session breaks.
#
# For the same reason the hook runs the copy uv already has without the network. The version is
# pinned, so that copy is the right one, and PyPI being out of reach (a proxy, a private mirror, an
# outage) stops mattering after the first run. When the first run cannot fetch it, the turn is
# reported as not audited: uv exits 2 when it cannot reach the index, and a Stop hook that exits 2
# tells Claude to keep going.
VERSION=0.1.3

for uvx in "$(command -v uvx 2>/dev/null)" "$HOME/.local/bin/uvx" "$HOME/.local/bin/uvx.exe" "$HOME/.cargo/bin/uvx" "$HOME/.cargo/bin/uvx.exe" /opt/homebrew/bin/uvx /usr/local/bin/uvx; do
  if [ -n "$uvx" ] && [ -x "$uvx" ]; then
    case " $* " in *" --hook "*) ;; *) exec "$uvx" "assurance@$VERSION" "$@" ;; esac
    input=$(cat)
    printf '%s' "$input" | UV_OFFLINE=1 "$uvx" "assurance@$VERSION" "$@" 2>/dev/null && exit 0
    { err=$(printf '%s' "$input" | "$uvx" "assurance@$VERSION" "$@" 2>&1 >&3); } 3>&1 && exit 0
    why=$(printf '%s\n' "$err" | grep -m 1 '^error:' | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"systemMessage": "assurance: %s did not run%s, so this turn was not audited."}\n' "$VERSION" "${why:+ ($why)}"
    exit 0
  fi
done

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
