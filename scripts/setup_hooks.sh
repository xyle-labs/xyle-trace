#!/bin/sh
# Configure this clone for the approved publication identity and fail-closed checks.
set -eu
cd "$(git rev-parse --show-toplevel)"
PATH="$PWD/.venv/bin:$PATH"
export PATH
command -v gitleaks >/dev/null 2>&1 || {
    echo "Install Gitleaks 8.30.1 or newer first; see CONTRIBUTING.md." >&2
    exit 1
}
gitleaks version
git config --local core.hooksPath .githooks
git config --local user.name 'Xyle Labs'
git config --local user.email 'jesse@xyle.de'
echo "Hooks enabled; commits use the project's approved publication identity."
