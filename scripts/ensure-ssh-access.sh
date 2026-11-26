#!/usr/bin/env bash
# Multipass has no SSH key for this user. Install the lab key with the
# Multipass CLI. VMware and Proxmox pass the same public key through cloud-init.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
KEY="${ROOT}/credentials/lab-ssh"
mkdir -p "${ROOT}/credentials"
chmod 700 "${ROOT}/credentials"
if [[ ! -s "${KEY}" ]]; then
  ssh-keygen -t ed25519 -f "${KEY}" -N "" -C "lab-k3s"
fi
chmod 600 "${KEY}"
hypervisor="$(tofu -chdir="${ROOT}/tofu" output -raw hypervisor)"
if [[ "${hypervisor}" != "multipass" ]]; then
  echo "ssh access for ${hypervisor} is cloud-init in that module"
  exit 0
fi
pub="$(cat "${KEY}.pub")"
mapfile -t names < <(tofu -chdir="${ROOT}/tofu" output -json nodes | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)))')
for name in "${names[@]}"; do
  multipass exec "${name}" -- bash -s <<EOF
set -euo pipefail
mkdir -p "\$HOME/.ssh"
chmod 700 "\$HOME/.ssh"
touch "\$HOME/.ssh/authorized_keys"
chmod 600 "\$HOME/.ssh/authorized_keys"
grep -qxF '${pub}' "\$HOME/.ssh/authorized_keys" || echo '${pub}' >> "\$HOME/.ssh/authorized_keys"
EOF
done
echo "lab ssh key is authorized on the multipass nodes"
