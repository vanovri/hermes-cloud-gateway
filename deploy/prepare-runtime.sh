#!/usr/bin/env bash
# Install the same immutable runtime used by Actions. Does not start a gateway.
set -euo pipefail
umask 077
missing_packages=()
for command in jq openssl sqlite3 curl; do
  if ! command -v "$command" >/dev/null 2>&1; then missing_packages+=("$command"); fi
done
if ! python3 -c 'import yaml' >/dev/null 2>&1; then missing_packages+=(python3-yaml); fi
if (( ${#missing_packages[@]} )); then
  sudo timeout --kill-after=10s 90s apt-get update
  sudo timeout --kill-after=10s 90s apt-get install -y "${missing_packages[@]}"
fi
hermes_ref=2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19
installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
curl -fsSL --connect-timeout 10 --max-time 60 --retry 3 --retry-max-time 120 \
  --proto '=https' "https://raw.githubusercontent.com/NousResearch/hermes-agent/${hermes_ref}/scripts/install.sh" -o "$installer"
echo "abf338605dae6dd28d101673ac6aed7995c955745db46d47676bc591a7957209  $installer" | sha256sum -c -
timeout --kill-after=30s 900s bash "$installer" --commit "$hermes_ref" --non-interactive
export PATH="$HOME/.local/bin:$HOME/.hermes/node/bin:$PATH"
export npm_config_fetch_timeout=30000 npm_config_fetch_retries=3 npm_config_fetch_retry_maxtimeout=30000
timeout --kill-after=15s 300s npm install -g 9router@0.5.112
if ! timeout --kill-after=15s 180s npm install -g @llmtrim/cli@0.13.4; then
  echo 'Optional llmtrim install failed; gateway operation can continue'
fi
if ! timeout --kill-after=15s 180s uv tool install https://github.com/Panniantong/Agent-Reach/archive/47898829204ad27521d8a1719e3aeca3b8a3e0ca.zip; then
  echo 'Optional Agent Reach install failed; built-in web tools remain available'
fi
