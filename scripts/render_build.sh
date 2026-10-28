#!/usr/bin/env bash
# Shared Render build for every service in this repo that installs
# requirements.txt (execflex-backend-1 and execflex-call-dispatcher).
#
# requirements.txt pins the private repo fionnano/agentic-core. pip clones it
# over https and fails with "could not read Username for 'https://github.com'"
# unless git is given a credential first.
#
# execflex-backend-1 already does that in its dashboard build command:
#   git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/" && pip install -r requirements.txt
# execflex-call-dispatcher does not, so every build since the pin landed has
# failed. This script is that same rewrite plus the pip install.
#
# Render does not read this file on its own. There is no render.yaml. After
# this script is on the branch being built, set each service's build command
# to:
#   bash scripts/render_build.sh
# and set GITHUB_TOKEN on any service that does not already have it, available
# during the build. Do not change start commands.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "ERROR: GITHUB_TOKEN is unset. Refusing to build." >&2
  echo "requirements.txt installs the private repo fionnano/agentic-core." >&2
  echo "Set GITHUB_TOKEN on this Render service (available during build) and retry." >&2
  exit 1
fi

# Do not echo the token. Render build VMs are ephemeral; this git config is
# not part of the runtime slug. The line matches the web service's working
# dashboard command, including the quoting.
git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"

exec pip install -r requirements.txt
