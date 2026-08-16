#!/usr/bin/env bash
# One-time droplet setup — run as root from the cloned repo on the droplet:
#   cd /opt/StockGame
#   bash scripts/deploy/setup-deploy-user.sh /path/to/github_actions_deploy.pub
#
# Production path: /opt/StockGame (scripts stay in-repo and update on git pull).
set -euo pipefail

EXPECTED_REPO_DIR="/opt/StockGame"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: bash $0 /path/to/github_actions_deploy.pub" >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: bash $0 /path/to/github_actions_deploy.pub" >&2
  exit 1
fi

GITHUB_ACTIONS_PUBKEY="$1"
if [[ ! -f "${GITHUB_ACTIONS_PUBKEY}" ]]; then
  echo "Public key not found: ${GITHUB_ACTIONS_PUBKEY}" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/deploy-stockgame.sh"

if [[ ! -f "${DEPLOY_SCRIPT}" ]]; then
  echo "Deploy script not found: ${DEPLOY_SCRIPT}" >&2
  exit 1
fi

if [[ "$(readlink -f "${REPO_DIR}")" != "$(readlink -f "${EXPECTED_REPO_DIR}")" ]]; then
  echo "Warning: expected repo at ${EXPECTED_REPO_DIR}, but this script lives under ${REPO_DIR}." >&2
  echo "If production uses ${EXPECTED_REPO_DIR}, run setup from that clone instead." >&2
fi

DEPLOY_USER="deploy"
DEPLOY_HOME="/home/${DEPLOY_USER}"
AUTH_KEYS="${DEPLOY_HOME}/.ssh/authorized_keys"

if ! id "${DEPLOY_USER}" &>/dev/null; then
  useradd --create-home --shell /usr/sbin/nologin "${DEPLOY_USER}"
  echo "Created user ${DEPLOY_USER}"
else
  echo "User ${DEPLOY_USER} already exists"
fi

usermod -aG docker "${DEPLOY_USER}"

mkdir -p "${DEPLOY_HOME}/.ssh"
chmod 700 "${DEPLOY_HOME}/.ssh"

chmod 755 "${DEPLOY_SCRIPT}" "${SCRIPT_DIR}/setup-deploy-user.sh"

# Invoke via bash so deploy works even when git clone drops the executable bit (100644).
DEPLOY_CMD="/bin/bash ${DEPLOY_SCRIPT}"

# Forced command: SSH as ``deploy`` always runs the in-repo deploy script.
PUBKEY_LINE="$(tr -d '\r\n' < "${GITHUB_ACTIONS_PUBKEY}")"
RESTRICTIONS="command=\"${DEPLOY_CMD}\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,no-user-rc,restrict"
echo "${RESTRICTIONS} ${PUBKEY_LINE}" > "${AUTH_KEYS}"
chmod 600 "${AUTH_KEYS}"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${DEPLOY_HOME}"

chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${REPO_DIR}"

cat <<EOF

================================================================================
Deploy user "${DEPLOY_USER}" is ready.

Repo:          ${REPO_DIR}
Deploy script: ${DEPLOY_CMD}
(Updates automatically when git pull brings in new script versions.)

NEXT STEPS — run as root unless noted
================================================================================

1) Git deploy key (droplet -> GitHub, read-only) — skip if already done
     runuser -u ${DEPLOY_USER} -- ssh-keygen -t ed25519 -C "stockgame-droplet-deploy" \\
       -f ${DEPLOY_HOME}/.ssh/git_deploy -N ""
     cat ${DEPLOY_HOME}/.ssh/git_deploy.pub

   GitHub: repo -> Settings -> Deploy keys -> Add deploy key
     Title: stockgame-droplet
     Allow write access: OFF

2) App secrets
     runuser -u ${DEPLOY_USER} -- cp ${REPO_DIR}/.env.example ${REPO_DIR}/.env
     runuser -u ${DEPLOY_USER} -- nano ${REPO_DIR}/.env
     chmod 600 ${REPO_DIR}/.env
     chown ${DEPLOY_USER}:${DEPLOY_USER} ${REPO_DIR}/.env

3) Data/logs directories
     runuser -u ${DEPLOY_USER} -- mkdir -p ${REPO_DIR}/data ${REPO_DIR}/logs

4) Test deploy manually
     runuser -u ${DEPLOY_USER} -- ${DEPLOY_CMD}

5) GitHub Actions secrets (repo -> Settings -> Secrets and variables -> Actions)
     DROPLET_HOST     = droplet IP or hostname
     DROPLET_USER     = deploy
     DROPLET_SSH_KEY  = private key from github_actions_deploy (full file, with BEGIN/END)
     DROPLET_PORT     = 22   (optional)

   Generate on your laptop:
     ssh-keygen -t ed25519 -C "github-actions-stockgame-deploy" \\
       -f ./github_actions_deploy -N ""

6) Push to main; after CI passes, Deploy workflow SSHes in and runs ${DEPLOY_CMD}.

SECURITY NOTES
- deploy user: nologin shell + forced command only.
- docker group is required for compose (main privilege beyond git pull).
- Keep ${REPO_DIR}/.env at mode 600.
================================================================================
EOF
