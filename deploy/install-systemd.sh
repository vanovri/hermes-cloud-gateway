#!/usr/bin/env bash
# Existing restored credentials/state are required. Never runs a second consumer.
set -euo pipefail
umask 077
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$repo" != "$HOME/hermes-cloud-gateway" ]]; then
  echo 'Clone this repository at ~/hermes-cloud-gateway before installing the supplied unit.' >&2
  exit 1
fi
test -s "$HOME/.hermes/.env"
test -s "$HOME/.hermes/config.yaml"
test -s "$HOME/.hermes/state.db"
python3 "$repo/ops/apply_core_patches.py" --core "$HOME/.hermes/hermes-agent"
mkdir -p "$HOME/.config/systemd/user"
install -m 600 "$repo/deploy/hermes.service" "$HOME/.config/systemd/user/hermes.service"
systemd-analyze --user verify "$HOME/.config/systemd/user/hermes.service"
systemctl --user daemon-reload
echo 'Unit installed. Enable only after the cloud gateway has stopped and its final state is restored.'
