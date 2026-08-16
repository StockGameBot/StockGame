#!/usr/bin/env bash
# Runs on the droplet as the locked-down ``deploy`` user (forced SSH command).
# Lives in the repo (/opt/StockGame) so ``git pull`` updates deploy logic automatically.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GIT_KEY="${HOME}/.ssh/git_deploy"
export GIT_SSH_COMMAND="ssh -i ${GIT_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

cd "${REPO_DIR}"

git fetch origin main
git reset --hard origin/main

docker compose build
docker compose up -d --no-build

# Drop unused images from previous builds (keeps disk use bounded).
docker image prune -f

echo "Deploy finished at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
