#!/usr/bin/env bash
# Delete only the lab nodes. Never touches the agent-sandbox VM.
set -euo pipefail

for name in "$@"; do
  if [[ "$name" != "k3s-server" && "$name" != "k3s-agent" ]]; then
    echo "refusing to delete ${name}; this script only accepts k3s-server and k3s-agent" >&2
    exit 1
  fi
done

targets=("k3s-server" "k3s-agent")
if [[ $# -gt 0 ]]; then
  targets=("$@")
fi

for name in "${targets[@]}"; do
  if multipass info "$name" >/dev/null 2>&1; then
    multipass delete "$name"
  fi
done
multipass purge
echo "deleted: ${targets[*]}"
