# Local k3s platform

OpenTofu declares the Multipass nodes. Ansible configures the OS and k3s. Argo CD syncs every addon from the Gitea repository.

The operator guide is `docs/infra.md`. What the platform is, the node contract, URLs, credentials, network policy, Vault ingestion, and the image-pull proxy are in `docs/k3s-platform-overview.md`.

```bash
make apply
```

`make destroy` deletes only `k3s-server` and `k3s-agent`. It does not touch the `agent-sandbox` VM. Credentials are written to `credentials/`, which is gitignored.
