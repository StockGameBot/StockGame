#!/usr/bin/env bash
# Run as root on the droplet to diagnose deploy SSH issues.
#   bash /opt/StockGame/scripts/deploy/verify-deploy-ssh.sh
set -euo pipefail

DEPLOY_USER="deploy"
DEPLOY_HOME="/home/${DEPLOY_USER}"
AUTH_KEYS="${DEPLOY_HOME}/.ssh/authorized_keys"
EXPECTED_REPO_DIR="/opt/StockGame"
DEPLOY_SCRIPT="${EXPECTED_REPO_DIR}/scripts/deploy/deploy-stockgame.sh"
DEPLOY_CMD="/bin/bash ${DEPLOY_SCRIPT}"

fail() { echo "FAIL: $*" >&2; ERRORS=$((ERRORS + 1)); }
warn() { echo "WARN: $*" >&2; WARNS=$((WARNS + 1)); }
ok() { echo "OK: $*"; }

ERRORS=0
WARNS=0

echo "=== StockGame deploy SSH verification ==="

id "${DEPLOY_USER}" >/dev/null 2>&1 || fail "user ${DEPLOY_USER} does not exist"

if id "${DEPLOY_USER}" >/dev/null 2>&1; then
  shell="$(getent passwd "${DEPLOY_USER}" | cut -d: -f7)"
  echo "user shell: ${shell}"
  [[ "${shell}" == "/bin/bash" ]] || warn "shell should be /bin/bash (not nologin)"
  groups "${DEPLOY_USER}" | grep -q docker && ok "deploy is in docker group" || fail "deploy is not in docker group"
fi

[[ -d "${DEPLOY_HOME}" ]] || fail "missing ${DEPLOY_HOME}"
home_owner="$(stat -c '%U' "${DEPLOY_HOME}")"
home_mode="$(stat -c '%a' "${DEPLOY_HOME}")"
echo "home: $(stat -c '%U:%G %a' "${DEPLOY_HOME}") ${DEPLOY_HOME}"
[[ "${home_owner}" == "${DEPLOY_USER}" ]] || fail "home must be owned by ${DEPLOY_USER}"

[[ -d "${DEPLOY_HOME}/.ssh" ]] || fail "missing ${DEPLOY_HOME}/.ssh"
ssh_mode="$(stat -c '%a' "${DEPLOY_HOME}/.ssh")"
[[ "${ssh_mode}" == "700" ]] && ok ".ssh mode 700" || warn ".ssh should be mode 700 (is ${ssh_mode})"

[[ -f "${AUTH_KEYS}" ]] || fail "missing ${AUTH_KEYS}"
keys_mode="$(stat -c '%a' "${AUTH_KEYS}")"
keys_owner="$(stat -c '%U' "${AUTH_KEYS}")"
[[ "${keys_mode}" == "600" ]] && ok "authorized_keys mode 600" || warn "authorized_keys should be 600 (is ${keys_mode})"
[[ "${keys_owner}" == "${DEPLOY_USER}" ]] && ok "authorized_keys owned by deploy" || fail "authorized_keys must be owned by deploy"

echo
echo "authorized_keys:"
cat "${AUTH_KEYS}"
echo

grep -qF "command=\"${DEPLOY_CMD}\"" "${AUTH_KEYS}" \
  && ok "forced command matches ${DEPLOY_CMD}" \
  || fail "authorized_keys missing: command=\"${DEPLOY_CMD}\""

grep -q 'no-pty' "${AUTH_KEYS}" && fail "authorized_keys has no-pty (remove it; breaks GitHub Actions and interactive ssh)"
grep -q 'restrict' "${AUTH_KEYS}" && fail "authorized_keys has restrict (remove it; includes no-pty)"

[[ -f "${DEPLOY_SCRIPT}" ]] && ok "deploy script exists" || fail "missing ${DEPLOY_SCRIPT}"
[[ -r "${DEPLOY_SCRIPT}" ]] && ok "deploy script readable by root" || fail "deploy script not readable"

[[ -d "${EXPECTED_REPO_DIR}" ]] && ok "repo directory exists" || fail "missing ${EXPECTED_REPO_DIR}"
repo_owner="$(stat -c '%U' "${EXPECTED_REPO_DIR}")"
[[ "${repo_owner}" == "${DEPLOY_USER}" ]] && ok "repo owned by deploy" || warn "repo should be owned by deploy (is ${repo_owner})"

[[ -f "${DEPLOY_HOME}/.ssh/git_deploy" ]] && ok "git deploy key present" || warn "git deploy key missing (${DEPLOY_HOME}/.ssh/git_deploy)"

[[ -f "${EXPECTED_REPO_DIR}/.env" ]] && ok ".env present" || warn ".env missing (${EXPECTED_REPO_DIR}/.env)"

echo
if [[ "${ERRORS}" -gt 0 ]]; then
  echo "Result: ${ERRORS} error(s), ${WARNS} warning(s)"
  echo "Re-run setup: cd ${EXPECTED_REPO_DIR} && bash scripts/deploy/setup-deploy-user.sh /path/to/github_actions_deploy.pub"
  exit 1
fi

echo "Result: OK (${WARNS} warning(s))"
echo
echo "Test from your laptop:"
echo "  ssh -T -i .\\github_actions_deploy deploy@YOUR_DROPLET_IP"
echo
echo "Manual deploy on droplet:"
echo "  runuser -u ${DEPLOY_USER} -- ${DEPLOY_CMD}"
