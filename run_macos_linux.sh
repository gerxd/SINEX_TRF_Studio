#!/bin/sh
set -eu
cd "$(dirname "$0")"

UV="$PWD/.uv/uv"
if [ ! -x "$UV" ]; then
    if command -v uv >/dev/null 2>&1; then
        UV="uv"
    else
        echo "Setting up the installer. This runs once."
        UV_INSTALL_DIR="$PWD/.uv" UV_NO_MODIFY_PATH=1 sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
        UV="$PWD/.uv/uv"
    fi
fi

exec "$UV" run --no-project --python 3.12 --python-preference only-managed main.py
