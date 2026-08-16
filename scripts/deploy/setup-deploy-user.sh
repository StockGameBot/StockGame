#!/usr/bin/env bash
# One-time droplet setup — run as root from the production clone:
#
#   cd /opt/StockGame
#   bash scripts/deploy/setup-deploy-user.sh /path/to/github_actions_deploy.pub
#
# Full from-scratch checklist:
#   1. Clone repo to /opt/StockGame (see scripts/deploy/setup-deploy-user.sh output).
#   2. Generate GitHub Actions key on laptop; copy .pub to droplet.
#   3. Run this script as root from /opt/StockGame.
#   4. Add git deploy key + GitHub Deploy key (read-only).
#   5. Create /opt/StockGame/.env, data/, logs/.
#   6. bash scripts/deploy/verify-deploy-ssh.sh
#   7. Add DROPLET_* secrets in GitHub Actions.
#
# Re-run this script any time to refresh authorized_keys or fix permissions.
# Scripts stay in-repo; git pull updates deploy logic automatically.
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
VERIFY_SCRIPT="${SCRIPT_DIR}/verify-deploy-ssh.sh"

if [[ ! -f "${DEPLOY_SCRIPT}" ]]; then
  echo "Deploy script not found: ${DEPLOY_SCRIPT}" >&2
  exit 1
fi

if [[ "$(readlink -f "${REPO_DIR}")" != "$(readlink -f "${EXPECTED_REPO_DIR}")" ]]; then
  echo "Warning: expected repo at ${EXPECTED_REPO_DIR}, but this script lives under ${REPO_DIR}." >&2
  echo "Run setup from ${EXPECTED_REPO_DIR} so authorized_keys paths stay correct." >&2
fi

DEPLOY_USER="deploy"
DEPLOY_HOME="/home/${DEPLOY_USER}"
AUTH_KEYS="${DEPLOY_HOME}/.ssh/authorized_keys"

if ! id "${DEPLOY_USER}" &>/dev/null; then
  useradd --create-home --shell /bin/bash "${DEPLOY_USER}"
  echo "Created user ${DEPLOY_USER}"
else
  echo "User ${DEPLOY_USER} already exists"
fi

# Use /bin/bash, not nologin. SSH access is limited by command= in authorized_keys.
# nologin causes "This account is currently not available." when command= is missing
# or PTY negotiation fails (GitHub Actions / plain ssh both request a PTY).
usermod -s /bin/bash "${DEPLOY_USER}"
usermod -aG docker "${DEPLOY_USER}"

mkdir -p "${DEPLOY_HOME}/.ssh"
chmod 755 "${DEPLOY_HOME}"
chmod 700 "${DEPLOY_HOME}/.ssh"

chmod 755 "${DEPLOY_SCRIPT}" "${SCRIPT_DIR}/setup-deploy-user.sh"
[[ -f "${VERIFY_SCRIPT}" ]] && chmod 755 "${VERIFY_SCRIPT}"

# Always invoke via bash — git may store scripts as 100644 without execute bit.
DEPLOY_CMD="/bin/bash ${DEPLOY_SCRIPT}"

PUBKEY_LINE="$(tr -d '\r\n' < "${GITHUB_ACTIONS_PUBKEY}")"
# Do NOT add no-pty or restrict — appleboy/ssh-action (drone-ssh) needs a PTY.
RESTRICTIONS="command=\"${DEPLOY_CMD}\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc"
printf '%s\n' "${RESTRICTIONS} ${PUBKEY_LINE}" > "${AUTH_KEYS}"
chmod 600 "${AUTH_KEYS}"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${DEPLOY_HOME}"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${REPO_DIR}"

echo
echo "Installed authorized_keys:"
cat "${AUTH_KEYS}"
echo

if [[ -f "${VERIFY_SCRIPT}" ]]; then
  echo "Running verification..."
  bash "${VERIFY_SCRIPT}" || true
  echo
fi

cat <<EOF
================================================================================
Deploy user "${DEPLOY_USER}" is ready.

Repo:          ${REPO_DIR}
Deploy script: ${DEPLOY_CMD}

NEXT STEPS — as root on the droplet
================================================================================

0) First-time clone (skip if ${REPO_DIR} already exists)
     runuser -u ${DEPLOY_USER} -- git clone git@github.com:YOUR_ORG/StockGame.git ${EXPECTED_REPO_DIR}
     chown -R ${DEPLOY_USER}:${DEPLOY_USER} ${EXPECTED_REPO_DIR}
     cd ${EXPECTED_REPO_DIR} && bash scripts/deploy/setup-deploy-user.sh ${GITHUB_ACTIONS_PUBKEY}

1) Git deploy key (droplet -> GitHub, read-only)
     runuser -u ${DEPLOY_USER} -- ssh-keygen -t ed25519 -C "stockgame-droplet-deploy" \\
       -f ${DEPLOY_HOME}/.ssh/git_deploy -N ""
     cat ${DEPLOY_HOME}/.ssh/git_deploy.pub
   GitHub -> Settings -> Deploy keys -> Add (read-only)

2) App config
     runuser -u ${DEPLOY_USER} -- cp ${REPO_DIR}/.env.example ${REPO_DIR}/.env
     runuser -u ${DEPLOY_USER} -- nano ${REPO_DIR}/.env
     chmod 600 ${REPO_DIR}/.env
     runuser -u ${DEPLOY_USER} -- mkdir -p ${REPO_DIR}/data ${REPO_DIR}/logs

3) Test deploy on droplet
     runuser -u ${DEPLOY_USER} -- ${DEPLOY_CMD}

4) GitHub Actions secrets (repo -> Settings -> Secrets -> Actions)
     DROPLET_HOST     = droplet IP
     DROPLET_USER     = deploy
     DROPLET_SSH_KEY  = full private key (github_actions_deploy, no .pub)
     DROPLET_PORT     = 22   (optional)

   Laptop keygen (one-time):
     ssh-keygen -t ed25519 -C "github-actions-stockgame-deploy" -f ./github_actions_deploy -N ""

5) Test SSH from laptop
     ssh -T -i .\\github_actions_deploy deploy@YOUR_DROPLET_IP

6) Push to main -> CI passes -> Deploy workflow runs automatically.

TROUBLESHOOTING
  bash ${REPO_DIR}/scripts/deploy/verify-deploy-ssh.sh
  Re-run this setup script to reset authorized_keys and permissions.
  Force rebuild without a new commit: DEPLOY_FORCE=1 runuser -u ${DEPLOY_USER} -- ${DEPLOY_CMD}
================================================================================
EOF
