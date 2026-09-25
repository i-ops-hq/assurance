#!/bin/sh
# Runs `assurance` at the version this plugin release pins.
#
# Claude Code can start hooks without the PATH your terminal has (the macOS desktop app gives them only
# /usr/bin:/bin:/usr/sbin:/sbin), so `uvx` is looked for where uv installs it, not only on PATH; on
# Windows this runs in Git Bash, where it is `uvx.exe`. Without uv, an `assurance` installed with pip
# is used. Without either, the hook says nothing was audited and lets the session end: an audit tool
# must never be the reason a session breaks.
VERSION=0.1.3

for uvx in "$(command -v uvx 2>/dev/null)" "$HOME/.local/bin/uvx" "$HOME/.local/bin/uvx.exe" "$HOME/.cargo/bin/uvx" "$HOME/.cargo/bin/uvx.exe" /opt/homebrew/bin/uvx /usr/local/bin/uvx; do
  if [ -n "$uvx" ] && [ -x "$uvx" ]; then
    exec "$uvx" "assurance@$VERSION" "$@"
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
