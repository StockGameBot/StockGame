#!/usr/bin/env bash
# Production deploy for the DigitalOcean droplet (/opt/StockGame).
# Invoked by:
#   - GitHub Actions (SSH forced command in /home/deploy/.ssh/authorized_keys)
#   - Manual test: runuser -u deploy -- /bin/bash /opt/StockGame/scripts/deploy/deploy-stockgame.sh
#   - Force rebuild: DEPLOY_FORCE=1 runuser -u deploy -- /bin/bash .../deploy-stockgame.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GIT_KEY="${HOME}/.ssh/git_deploy"
export GIT_SSH_COMMAND="ssh -i ${GIT_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

cd "${REPO_DIR}"

echo "Deploy starting in ${REPO_DIR} at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

git fetch origin main

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse origin/main)"

if [[ "${LOCAL_SHA}" == "${REMOTE_SHA}" && "${DEPLOY_FORCE:-}" != "1" ]]; then
  echo "No new commits on main (${LOCAL_SHA:0:12}); skipping docker rebuild."
  exit 0
fi

if [[ "${DEPLOY_FORCE:-}" == "1" ]]; then
  echo "DEPLOY_FORCE=1 set; rebuilding even though remote is ${REMOTE_SHA:0:12}."
elif [[ "${LOCAL_SHA}" != "${REMOTE_SHA}" ]]; then
  echo "Updating ${LOCAL_SHA:0:12} -> ${REMOTE_SHA:0:12}"
fi

git reset --hard origin/main

docker compose build
docker compose up -d --no-build

docker image prune -f

echo "Deploy finished at $(date -u +"%Y-%m-%dT%H:%M:%SZ") (${REMOTE_SHA:0:12})"
