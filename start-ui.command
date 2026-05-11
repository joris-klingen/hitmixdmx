#!/usr/bin/env bash
# Double-click in Finder (or run from terminal) to launch the lightgen Web UI.
# Opens http://127.0.0.1:7860 in your default browser.
set -e
cd "$(dirname "$0")"
# Finder-launched scripts don't load ~/.zshrc, so add common uv install paths:
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
exec uv run lightgen ui
