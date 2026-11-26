# Local k3s platform

Multipass VMs on this host run a small k3s cluster. Cilium replaces flannel and kube-proxy. HAProxy replaces Traefik. Argo CD syncs the platform from the Gitea repository it bootstraps.

The node layout, URLs, credentials, network-policy exceptions, and validation results are in `docs/k3s-platform-overview.md` after bootstrap finishes.

Recreate from this checkout:

```bash
./bootstrap/bootstrap.sh
```

The script is idempotent. It does not touch the existing `agent-sandbox` Multipass VM. Credentials are written to `credentials/`, which is gitignored.
