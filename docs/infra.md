# Infrastructure and node configuration

OpenTofu owns the VMs. Ansible owns the OS baseline, the k3s install, and the kubeconfig. Argo CD owns every cluster addon. Ansible does not install Helm charts.

## Layout

| Path | Role |
| --- | --- |
| `tofu/` | Applied workspace. Multipass only |
| `tofu/modules/vmware`, `tofu/modules/proxmox` | Same node contract, not applied |
| `tofu/platforms/vmware`, `tofu/platforms/proxmox` | Separate roots for a later switch |
| `ansible/` | Inventory generated from tofu outputs, plus roles |
| `Makefile` | `plan`, `apply`, `adopt`, `destroy` |
| `gitops/` | Unchanged. Argo CD syncs it |

The node contract is one map, `nodes`, in `tofu/variables.tf`:

| Field | Meaning |
| --- | --- |
| key | VM name. `agent-sandbox` is rejected |
| `role` | `server` or `agent` |
| `cpus` | vCPU count |
| `memory` | Multipass-style size. Both lab nodes are `5G` |
| `disk` | Multipass-style size, such as `30G` |
| `address` | `dhcp` or a host address |
| `ssh_user` | SSH login Ansible uses |
| `image` | Image alias used when a VM is created |

`tofu output nodes` is the same shape after an address is known: `name`, `role`, `ip`, `ssh_user`, `cpus`, `memory`, `disk`, `address`. Ansible inventory is rendered only from that output, so a VMware or Proxmox workspace with the same output does not require role changes.

## Versions

Pinned in `versions/pins.env` and the lock files:

| Tool | Version |
| --- | --- |
| OpenTofu | 1.13.10 |
| ansible-core | 2.22.0 |
| todoroff/multipass | 2.3.1 |
| vmware/vsphere | 2.18.0 |
| bpg/proxmox | 0.124.0 |

OpenTofu is installed at `~/.local/bin/tofu`. Ansible is the `ansible-core` 2.22.0 virtualenv at `~/.local/share/ansible-venv`, linked into `~/.local/bin`.

## Commands

From the repo root, on the host that runs Multipass:

```bash
make plan      # tofu plan. Exit 0 means no changes
make apply     # tofu apply, authorize SSH, render inventory, ansible-playbook
make destroy   # guarded tofu destroy of k3s-server and k3s-agent only
```

`bootstrap/bootstrap.sh` execs `make apply`. `bootstrap/destroy.sh` execs `make destroy`. `bootstrap/legacy-bootstrap.sh` installs Cilium, cert-manager, HAProxy, Gitea, and Argo CD after k3s exists. It must not be used to create or delete VMs.

`make adopt` imports already-running Multipass VMs. Import ids are the names `k3s-server` and `k3s-agent`. State is local (`tofu/terraform.tfstate`, gitignored). The current nodes were created by `make apply`, not imported.

`make destroy` runs `scripts/plan-guard.py`, which refuses the plan if it mentions `agent-sandbox` or any resource other than the two lab instances.

## What Ansible does

1. `os_baseline` writes the sysctl and kernel-module files from the original cloud-init and stops unattended upgrades.
2. `k3s_server` installs pinned k3s only when the service is not active. On a running server it asserts the version and the flags (`--cluster-init`, no flannel, no kube-proxy, no Traefik, no ServiceLB).
3. `k3s_agent` does the same for the agent and `--node-ip`.
4. `registry_proxy` creates the Harbor pull-through projects, then writes the platform CA, `registries.yaml`, and the host firewall list. The Harbor API task needs Harbor to be up.
5. `kubeconfig` copies `credentials/kubeconfig` and `credentials/state.env`, then `kubectl apply`s `gitops/root.yaml`. That apply needs the Argo CD CRDs.

## SSH

Multipass does not share its daemon key with this user. `scripts/ensure-ssh-access.sh` creates `credentials/lab-ssh` and installs the public key with `multipass exec`. Ansible then uses normal SSH as `ubuntu`. The VMware and Proxmox modules take the same public key in `ssh_public_key` and put it in the guest through their clone or cloud-init settings.

## Switching to VMware

Neither vSphere nor Proxmox is installed here. Their workspaces were initialized and `tofu validate` succeeded. They were not applied.

```bash
cd tofu/platforms/vmware
cp terraform.tfvars.example terraform.tfvars
# edit datacenter, datastore, network, resource pool, template, and the node map
export VSPHERE_USER=... VSPHERE_PASSWORD=... VSPHERE_SERVER=vcenter.example
tofu init
tofu plan
tofu apply
```

Then render inventory from that workspace. This does not apply it:

```bash
cd ../../..
python3 scripts/render-inventory.py tofu/platforms/vmware
```

`scripts/render-inventory.py` reads `tofu/` when no directory is given. Ansible roles and `gitops/` stay as they are. Do not set `hypervisor` in `tofu/` to anything but `multipass`; that variable is rejected so a switch cannot delete the Multipass VMs from this state.

The vSphere module clones `vmware_template` and sets CPU, memory (MiB parsed from `5G`), and disk (GB parsed from `30G`). `address = "dhcp"` uses the address vSphere reports. A static address is passed through as the contract IP.

## Switching to Proxmox

```bash
cd tofu/platforms/proxmox
cp terraform.tfvars.example terraform.tfvars
# set endpoint, node name, datastore, bridge, template id, and the node map
export PROXMOX_VE_API_TOKEN='user@pve!lab=secret'
tofu init
tofu plan
tofu apply
```

Then render inventory from that workspace. This does not apply it:

```bash
cd ../../..
python3 scripts/render-inventory.py tofu/platforms/proxmox
```

The module clones `proxmox_template_id`, sets cores, dedicated memory, and a scsi0 disk, and uses cloud-init for `dhcp` or the given address plus `ssh_user` and `ssh_public_key`. Provider `bpg/proxmox` 0.124.0 also reads `PROXMOX_VE_ENDPOINT` when the configuration does not set one. This workspace sets `endpoint` from `proxmox_endpoint`.

## Declared sizes

Both nodes are created at `memory = "5G"`. Memory is not in `lifecycle.ignore_changes`, so the string in `tofu/variables.tf` is the size Multipass launches. `tofu plan` stays empty while the provider keeps that string. Disk, image, and zone stay in `ignore_changes`: Multipass reports disk as a byte count after launch (`32212254720` is 30GiB), and image and zone are launch-time. Changing memory on a running node means planning the new size. `resize_policy` is `in_place`, and Multipass stops the VM to apply it. Destroy and recreate is the path that also drops the cluster.

## Credentials

Unchanged from the platform overview, plus:

| File | Contents |
| --- | --- |
| `credentials/lab-ssh` | Ansible private key. Gitignored |
| `credentials/kubeconfig` | Rewritten only when the server kubeconfig changes |
| `credentials/state.env` | Server IP, agent IP, k3s join token |
| `tofu/terraform.tfstate` | OpenTofu state for the two Multipass VMs. Gitignored |
