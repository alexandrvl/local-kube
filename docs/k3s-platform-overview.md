# Local k3s platform

A two-node k3s lab on Multipass VMs that run on this host. OpenTofu declares the VMs. Ansible configures the OS and k3s. Cilium is the CNI and the kube-proxy replacement. HAProxy is the ingress. cert-manager issues a local CA. Argo CD syncs the platform from a Gitea repository that lives in the cluster. Team namespaces are isolated by RBAC, Kyverno, Vault, and Cilium network policy. The operator guide for VMs, Ansible, and a later VMware or Proxmox switch is `docs/infra.md`.

Checked on 26 Nov 2026, afternoon Europe/Tallinn, on host `alexandrs-dev`.

## Architecture

```text
host (alexandrs-dev, Multipass on KVM)
  OpenTofu (tofu/) declares k3s-server and k3s-agent
  Ansible configures OS baseline, k3s, and kubeconfig
  Argo CD syncs gitops/ from Gitea
        |
  k3s-server  10.114.66.182   control plane, etcd, most system pods
  k3s-agent   10.114.66.245   second worker
        |
        +-- Cilium (VXLAN, Hubble) replaces flannel and kube-proxy
        +-- HAProxy DaemonSet on host ports 80 and 443
        +-- cert-manager CA  (ClusterIssuer platform-ca)
        +-- Gitea  -->  Argo CD app-of-apps  -->  the rest of the platform
        +-- Vault kubernetes auth, one path per team
        +-- Alloy -> Mimir (metrics) and Loki (logs) -> Grafana
        +-- Harbor, Kyverno, Falco, OpenCost
```

k3s is started with `--cluster-init`, so a second server can join the embedded etcd later. This lab has one server because the host is already running the `agent-sandbox` VM.

The host is itself a KVM guest. Nested virt is on (`/dev/kvm` is present) and Multipass launches the nodes here, not inside `agent-sandbox`.

## Node layout

| Node | Role | vCPU | RAM | Disk | IPv4 | Zone |
| --- | --- | --- | --- | --- | --- | --- |
| k3s-server | control-plane, etcd | 2 | 4G | 30G | 10.114.66.182 | zone1 |
| k3s-agent | agent | 2 | 3G | 30G | 10.114.66.245 | zone1 |

Both nodes report `Ready` and `v1.36.6+k3s1`. Ubuntu image `24.04`. Pod CIDR `10.42.0.0/16`. Service CIDR `10.43.0.0/16`. API server `https://10.114.66.182:6443`.

`agent-sandbox` (`10.114.66.250`, Ubuntu 26.04, 6 vCPU, 11.7 GiB configured) is not part of the cluster. It is not in the OpenTofu state, and `make destroy` refuses a plan that mentions it.

The host has 4 CPUs and 18 GiB. After the sandbox and these two VMs, about 4 GiB stays free on the host. A third k3s node was not added.

## Recreate from scratch

From this checkout, on the host (not inside a VM):

```bash
make apply
```

That is `tofu apply` for the Multipass nodes, SSH key setup, Ansible, and `kubectl apply` of `gitops/root.yaml`. `./bootstrap/bootstrap.sh` execs the same target. Details, including how these two VMs were imported instead of recreated, are in `docs/infra.md`.

```bash
make destroy
```

deletes only `k3s-server` and `k3s-agent`. `./bootstrap/destroy.sh` execs that target.

Kubeconfig for day-to-day use is `~/.kube/config`, mode 600, context `default` (this lab, and the current context). kubectl reads that path when `KUBECONFIG` is unset, so the shell rc does not set it. The repo copy remains `credentials/kubeconfig`.

```bash
kubectl --context default get nodes
```

## Versions

Pinned in `versions/pins.env` and `versions/charts.env`. Installed versions:

| Component | Version | How it is installed |
| --- | --- | --- |
| k3s / Kubernetes | v1.36.6+k3s1 | install script. Stable channel was v1.37.2; Cilium 1.20.5 tests 1.33–1.36, so 1.36.6 is pinned |
| Cilium / Hubble | chart 1.20.5 | Helm, then Argo CD |
| HAProxy ingress | chart 1.57.1, app 3.4.1 | Helm release `haproxy-ingress` |
| cert-manager | v1.21.3 | Helm |
| Argo CD | chart 10.20.0, app v3.6.3 | Helm |
| Gitea | image `docker.io/gitea/gitea:28.4.0` | manifest. Chart 12.7.0 still packages 1.27.0 |
| Vault | chart 0.34.1, image 2.2.2, injector vault-k8s 1.7.6 | Helm, file storage, Agent Injector |
| Alloy | chart 1.16.0, v1.22.0 | Helm |
| Mimir | `grafana/mimir:3.2.6` | single-binary manifest |
| Loki | `grafana/loki:3.7.10` | single-binary manifest |
| Grafana | chart 10.5.15, app 12.3.1 | Helm. That chart does not ship upstream 13.3.2 |
| kube-state-metrics | chart 9.1.0, app 2.20.0 | Helm |
| node-exporter | chart 6.0.0, app 1.13.1 | Helm |
| Harbor | chart 1.19.4, app 2.15.4 | Helm. Upstream 2.15.5 is one patch ahead of this chart |
| Kyverno | chart 3.10.0, app v1.20.0 | Helm |
| Falco | chart 9.2.2, app 0.45.2 | Helm, modern eBPF |
| OpenCost | chart 2.5.35, app 1.121.5 | Helm |
| sample app | `ghcr.io/stefanprodan/podinfo:6.17.0` | manifest |
| Helm | v4.3.2 | `~/.local/bin/helm` |
| OpenTofu | 1.13.10 | `~/.local/bin/tofu` |
| ansible-core | 2.22.0 | `~/.local/share/ansible-venv` |
| Multipass provider | todoroff/multipass 2.3.1 | applied workspace `tofu/` |
| vSphere provider | vmware/vsphere 2.18.0 | validated, not applied |
| Proxmox provider | bpg/proxmox 0.124.0 | validated, not applied |
| kubectl | v1.36.6 | `~/.local/bin/kubectl` |

## Namespaces and RBAC

| Namespace | Who can deploy |
| --- | --- |
| team-a, team-b, team-c | ServiceAccount of the same name, Role `team-deployer`, RoleBinding in that namespace only |
| kube-system | Cilium, Hubble, CoreDNS, local-path, metrics-server |
| ingress | HAProxy |
| cert-manager | cert-manager and the platform CA |
| gitea | Gitea |
| argocd | Argo CD |
| vault | Vault |
| observability | Alloy, Mimir, Loki, Grafana, kube-state-metrics, node-exporter |
| harbor | Harbor |
| kyverno | Kyverno |
| falco | Falco |
| opencost | OpenCost |

`team-deployer` is a namespaced Role with full access inside that namespace. There is no ClusterRoleBinding for the team accounts.

Proven with a token-only client (no admin client certificate):

- `system:serviceaccount:team-a:team-a` created ConfigMap `rbac-ok2` in `team-a`.
- The same token was rejected in `team-b`: `configmaps "rbac-no2" is forbidden`.
- `kubectl auth can-i create configmaps -n team-b --as=system:serviceaccount:team-a:team-a` returned `no`.

## Network policy

Cilium `policyEnforcementMode` is `default`. `restricted-baseline` selects every pod, including `kube-system`. Same-namespace traffic is allowed by a `CiliumNetworkPolicy` named `allow-same-namespace` in each platform namespace, including `kube-system`. Manifests: `platform/network/policies.yaml`. Argo CD app: `gitops/apps/network.yaml`.

`kube-system-platform` allows the API, the node, health checks, and the ingress controller. `kube-dns-from-cluster` allows every pod to query CoreDNS and allows CoreDNS to reach the host resolver on UDP/TCP 53. Cilium itself is host-networked, so this policy does not sit on the dataplane. After the change, `cilium status --brief` still printed `OK` and `nslookup kubernetes.default` still returned `10.43.0.1`.

Default for every other namespace:

- Ingress only from entities `host`, `remote-node`, `health`, `kube-apiserver`, and from namespaces `ingress` and `observability`.
- Egress only to those entities, and to `kube-dns` on UDP/TCP 53.
- Same-namespace traffic allowed.

Further exceptions, each one a named policy:

| Policy | What it allows |
| --- | --- |
| ingress-controller | World and host to HAProxy ports 80, 443, 8080, 8443, 1042. HAProxy egress to every pod, so Ingress backends work, including Hubble in `kube-system` |
| argocd-git-and-charts | Argo CD to Gitea TCP 3000, and to the Helm chart names only (`helm.cilium.io`, `cilium.github.io`, `helm.releases.hashicorp.com`, `helm.goharbor.io`, `charts.jetstack.io`, `github.com`, `*.github.io`, `*.githubusercontent.com`) on 80 and 443. A DNS rule is required so those names are learned |
| gitea-from-argocd | Gitea accepts TCP 3000 from Argo CD |
| vault-from-teams | Vault TCP 8200 from team-a, team-b, team-c, and ingress |
| teams-to-vault | Team pods egress to Vault TCP 8200 |
| registry-pulls | Any non-kube-system pod to Harbor TCP 80, 443, 5000 |
| pods-no-world-https | Every pod except Harbor and Argo CD is denied TCP 443 to the world. This is not a hostname list | 
| node-no-world-https | The node root namespace, including host-network pods, is denied TCP 443 to the world. Other node traffic stays allowed | 
| harbor-proxy-upstream | Harbor pods may egress to the world on TCP 443. That is the only registry upstream |
| opencost-to-mimir / mimir-from-opencost | OpenCost to Mimir TCP 9009 |
| kube-system-platform | kube-system to the API, node, and health endpoints, and ingress to Hubble |
| kube-dns-from-cluster | Cluster pods to CoreDNS :53, and CoreDNS egress to the host resolver :53 |

Node image pulls go through Harbor, not the public registries. Ansible role `registry_proxy` writes `/etc/rancher/k3s/registries.yaml` on both nodes. containerd rewrites these hosts to a Harbor pull-through project: `docker.io` and `registry.hub.docker.com` (`dockerhub`), `ghcr.io` (`ghcr`), `quay.io` (`quay`), `registry.k8s.io` (`k8s`), `k8s.gcr.io` (`k8s-gcr`), `gcr.io` (`gcr`), `mirror.gcr.io` (`mirror-gcr`), `ecr-public.aws.com` and `public.ecr.aws` (`ecr-public`), `reg.kyverno.io` (`kyverno`), `registry.gitlab.com` (`gitlab`), `docker.elastic.co` (`elastic`), `mcr.microsoft.com` (`mcr`), `nvcr.io` (`nvcr`), and `us-docker.pkg.dev`, `europe-docker.pkg.dev`, `asia-docker.pkg.dev` (`gar-us`, `gar-eu`, `gar-asia`). The node resolves `harbor.lab.test` to the ingress: `127.0.0.1` on the server, `10.114.66.182` on the agent. kubelet and `k3s crictl` use that mirror. `k3s ctr` does not, so a raw `ctr` pull is reset instead of proxied.

Cilium is the control, not the hostname list. `hostFirewall.enabled` is true and attaches to `eth0`. `node-no-world-https` allows other node egress and then denies TCP 443 to entity `world`. `pods-no-world-https` denies that same port for every pod except Harbor and Argo CD. Harbor stays on `harbor-proxy-upstream`. The iptables list in `registry-block.list` is still installed, but a name that is missing from it is still denied: `registry.fedoraproject.org` is not on the list, and `curl -4 https://registry.fedoraproject.org/v2/` from both nodes timed out. The sample pod in `team-a` timed out to the same URL. Argo CD opened `helm.cilium.io` and `github.com`, and did not open `registry.fedoraproject.org`. `k3s crictl pull docker.io/library/busybox:1.36` on `k3s-agent` succeeded, and Harbor fetched `dockerhub/library/busybox` because it was not cached. Both nodes stayed Ready through the Cilium restart. `/readyz` stayed ok.

Proven from the sample pod in `team-a`:

- `http://sample.team-a.svc.cluster.local:9898/healthz` succeeded.
- `http://listener.team-b.svc.cluster.local:8080/` timed out.
- `http://1.1.1.1/` timed out.
- `nslookup kubernetes.default.svc.cluster.local` returned `10.43.0.1`.
- `https://sample.lab.local` still returned HTTP 200 after the policies were applied.

## TLS and DNS

`ClusterIssuer/selfsigned-bootstrap` issues Certificate `platform-ca` (namespace `cert-manager`, secret `platform-ca-secret`, CN `lab-platform-ca`, ECDSA P-256, CA). `ClusterIssuer/platform-ca` is the issuer workloads use. The CA is exported to `credentials/platform-ca.crt`.

The only lab suffix is `lab.test`. `.local` is not used, because systemd-resolved will not answer it. CoreDNS keeps the stock Corefile. `platform/coredns/custom.yaml` adds a `template` plugin (the stock file already has `hosts`, and a second `hosts` plugin crash-loops CoreDNS). Every `*.lab.test` name answers `10.114.66.182` inside the cluster.

On the host, container `lab-dns` (dnsmasq, `platform/dns/lab-test.conf`) listens on `127.0.0.1:5354` and answers `*.lab.test` with `10.114.66.182`. `/etc/systemd/resolved.conf.d/lab-test.conf` sends only that suffix there. `/etc/hosts` has the same names as a fallback. A name that is not in the hosts file, such as `notinhosts.lab.test`, still resolves. The platform CA is in the host trust store and in the Firefox profile `dkj2x7yf.default`.

```text
curl -sS -o /dev/null -w '%{http_code}\n' https://sample.lab.test/healthz
200
```

HAProxy is a DaemonSet with host ports. Ingress objects have an empty load-balancer status, so Argo CD uses a health check that treats those Ingresses as Healthy. HTTP on the node IP without a Host header returns 404.

## How to reach the platform

| Service | URL | Login |
| --- | --- | --- |
| API | https://10.114.66.182:6443 | `~/.kube/config`, context `default`. Repo copy: `credentials/kubeconfig` |
| Sample app | https://sample.lab.test | none. podinfo 6.17.0, HTTP/2 |
| Argo CD | https://argocd.lab.test | user `admin`, password file `credentials/argocd-admin-password` |
| Grafana | https://grafana.lab.test | user `admin`, `GRAFANA_ADMIN_PASSWORD` in `credentials/lab-secrets.env` |
| Hubble UI | https://hubble.lab.test | none |
| Gitea | https://gitea.lab.test | user `labadmin`, `GITEA_ADMIN_PASSWORD` in `credentials/lab-secrets.env`. SSH is off |
| Harbor | https://harbor.lab.test | user `admin`, `HARBOR_ADMIN_PASSWORD` in `credentials/lab-secrets.env` |
| Vault | https://vault.lab.test | root token `VAULT_ROOT_TOKEN` in `credentials/lab-secrets.env` |
| OpenCost | https://opencost.lab.test | none |

`credentials/lab-secrets.env` is mode 600 and gitignored. It holds the Vault root token and the unseal key. The Harbor encryption key is in Secret `harbor/harbor-secret-key`, and the unseal material is in Secret `vault/vault-unseal`. Neither value is in the git values, so a sync does not reset them.

Git remote used by Argo CD: `http://gitea.gitea.svc.cluster.local:3000/labadmin/local-kubenrtetes.git`. The host remote `origin` is `http://127.0.0.1:3000/labadmin/local-kubenrtetes.git` and only works while `kubectl -n gitea port-forward svc/gitea 3000:3000` is running. There is no GitHub remote.

## GitOps

`gitops/root.yaml` is Application `platform`. It syncs every file in `gitops/apps/` with prune and selfHeal. Helm apps point at the pinned chart and at `$values` in this repo. Path apps apply the manifests directly.

Repo server parallelism is 1 and `ARGOCD_EXEC_TIMEOUT` is 10m. Several charts pulled at once previously hit the 90s Helm timeout.

## Secrets

Vault 2.2.2 runs as pod `vault-0` on `k3s-server`, file storage on PVC `data-vault-0` (1Gi, local-path), unsealed, UI on. A postStart hook unseals from Secret `vault-unseal`, then writes the policies and Kubernetes auth roles from ConfigMap `vault-auth`. kv-v2 is mounted at `secret/`. The server holds this one small volume. A raft cluster was not added.

The Vault Agent Injector (`vault-agent-injector`, image `hashicorp/vault-k8s:1.7.6`) runs on `k3s-agent`. It is the ingestion path. A workload does not log in with the root token. The sidecar uses the pod's ServiceAccount against auth method `kubernetes`. The projected token audience is `https://kubernetes.default.svc.cluster.local`, and the roles expect that audience.

Policy `team-a` reads `secret/data/teams/team-a/*`. Role `team-a` is bound to ServiceAccount `team-a` in namespace `team-a`. Policy `team-b` reads only `secret/data/teams/team-b/*`, and role `team-b` is bound to ServiceAccount `team-b` in `team-b`.

Turn ingestion on for a pod:

1. Give its ServiceAccount a role whose policy can read the path. Set the role audience to `https://kubernetes.default.svc.cluster.local`.
2. Add these annotations. The name after `agent-inject-secret-` and `agent-inject-template-` is the filename under `/vault/secrets/`.

```yaml
vault.hashicorp.com/agent-inject: "true"
vault.hashicorp.com/role: "team-a"
vault.hashicorp.com/agent-inject-secret-demo: "secret/data/teams/team-a/demo"
vault.hashicorp.com/agent-inject-template-demo: |
  {{- with secret "secret/data/teams/team-a/demo" -}}
  {{ .Data.data.value }}
  {{- end }}
```

3. The init container renders the file before the app starts. The sidecar re-renders static KV secrets every 30 seconds (`agentDefaults.templateConfig.staticSecretRenderInterval`). Read the file again to see a new value. A process that copied the file into its own environment needs a restart.

The sample Deployment in `team-a` uses that annotation set. `kubectl -n team-a exec deploy/sample -c sample -- cat /vault/secrets/demo` returns the same `value` field as `secret/data/teams/team-a/demo`. The sample container has no Vault token. The sidecar token is the Kubernetes login for `team-a`, policies `default` and `team-a`. A pod that uses role `team-b` and asks for the team-a path gets `403 permission denied` from the agent init container, and the app container never starts. Argo CD ignores `caBundle` on `vault-agent-injector-cfg` because the injector fills that field after each sync.

## Observability

Alloy runs on both nodes, remote-writes to Mimir, and ships pod logs to Loki. Grafana, Mimir, and Loki run on `k3s-agent`. Grafana datasources are `mimir` (Prometheus URL `http://mimir.observability.svc.cluster.local:9009/prometheus`) and `loki`. Dashboards `Cluster`, `Hubble`, `Sample app`, and `Falco` are ConfigMap `lab-dashboards` (label `grafana_dashboard=1`). The Falco panel queries `{namespace="falco"} |= "Critical"`. The sample panel is `go_goroutines{namespace="team-a"}`.

Mimir uses PVC `mimir-data` (2Gi), Loki uses `loki-data` (2Gi), and Grafana uses its chart PVC (1Gi), all local-path on `k3s-agent`. A pushed Loki line `persist-proof-…` and Mimir sample `sum(kube_pod_status_phase{phase="Running"})` at timestamp `1795695586` were still queryable after those pods were deleted.

Proven:

- Mimir instant query `sum(kube_pod_status_phase{phase="Running"})` returned a value (45 at the time).
- Loki range query `{namespace="team-a"}` returned the sample pod line `Starting HTTP Server` on `:9898`.
- `https://grafana.lab.local/login` returned HTTP 200 with the platform CA.

## Supply chain, policy, runtime

Gitea accepted the platform commits on `main` (`57d216a`, `d40f26f`, and the bootstrap/docs commit). The repo is private, owner `labadmin`.

Harbor project `lab` was created. `crane` on `k3s-agent` copied `docker.io/library/busybox:1.36` (linux/amd64) to `harbor.lab.local/lab/busybox:lab` (`sha256:b7f3d86d6e84…`). Trivy is off to save RAM. Harbor nginx is not used; expose type is Ingress.

Kyverno v1.20 rejects the old `kyverno.io/v1` ClusterPolicy. Policies are `policies.kyverno.io/v1` ValidatingPolicy objects, `validationActions: [Deny]`, namespaces team-a/b/c:

- `require-team-app-label` denied a pod with no `app.kubernetes.io/name`.
- `disallow-privileged-teams` denied a pod with `securityContext.privileged: true`.

Falco 0.45.2 is a DaemonSet, `driver.kind=modern_ebpf`, on both nodes. Startup logged `Loading rules from /etc/falco/falco_rules.yaml | schema validation: ok` and `Opening 'syscall' source with modern BPF probe`. A real alert was emitted: rule `Drop and execute new binary in container`, priority Critical, for `gitea hook` in the Gitea pod during the git push. Alerts go to the Falco container log, which Alloy ships to Loki. Grafana dashboard `Falco` queries `{namespace="falco"} |= "Critical"`. That query returned a new Critical alert for `/tmp/review-gitea` in the Gitea pod.

OpenCost queries Mimir. The exporter mounts PVC `opencost-pvc` at `/var/configs` with `fsGroup` 1001. `default.json` is on that volume, and `GET /allocation?window=1d&aggregate=namespace` still returns HTTP 200.

## What was validated

- Both nodes `Ready`, version `v1.36.6+k3s1`.
- Cilium agent, Hubble relay, and Hubble UI running. Same-namespace HTTP worked, cross-namespace HTTP timed out, external HTTP timed out, cluster DNS worked, sample Ingress stayed HTTPS 200.
- HAProxy served `https://sample.lab.local` with a certificate issued by `lab-platform-ca`. Grafana, Harbor, Hubble, and Argo CD also answered HTTPS 200.
- Team token applied in its own namespace and was forbidden in the other.
- Vault unsealed. Team login read its own path and was denied the other team's path.
- A Mimir metric and a Loki log line were queried. Grafana login returned 200.
- Gitea received git pushes. Harbor accepted an image push. Kyverno denied two bad pods. Falco loaded rules and emitted an alert. OpenCost returned an allocation.
- The existing Multipass nodes were imported into OpenTofu. `tofu plan` reports no changes and does not mention `agent-sandbox`. A destroy plan lists only `k3s-server` and `k3s-agent`; it was not applied.
- Ansible's second run reported `changed=0` on both nodes. After that, both nodes were still `Ready`, `https://sample.lab.local/healthz` returned 200 with the platform CA, and `https://argocd.lab.local` returned 200.
- `tofu validate` succeeded for `tofu/platforms/vmware` and `tofu/platforms/proxmox`. Neither hypervisor is installed, and neither workspace was applied.

## Argo CD status

The application controller was OOMKilled on `k3s-server` at a 512Mi limit and its syncs stuck in `Running` across an apiserver restart. `platform/argocd/values.yaml` now pins that StatefulSet to `k3s-agent` and raises the limit to 768Mi. After the move the pod was `1/1` on `k3s-agent`.

`platform` is tracking commit `8cdd863` (the bootstrap and network-policy commit). These apps were `Synced` / `Healthy`: alloy, argocd, cert-manager, falco, gitea, haproxy, issuers, kube-state-metrics, kyverno-policies, opencost, platform-ingress, sample, teams, vault.

Still reconciling when this was written, workloads already running:

- `grafana` and `node-exporter`: sync retrying. Comparison failed while the API was refusing discovery (`failed to discover server resources`). The Grafana Deployment and the node-exporter DaemonSet were `Running` the whole time. Health `Missing` is that failed comparison, not a deleted workload.
- `kyverno`: retrying because hook Job `kyverno-check-legacy-policies` had been deleted. Admission policies were still loaded and still denying.
- `cilium`, `harbor`, `observability`: `OutOfSync` / `Healthy`, sync in progress. Cilium's extra diff is the rotated TLS secrets; `ignoreDifferences` is set for those three Secrets.
- `network` and `coredns-custom`: declared by `platform` and not finished applying.

Re-check with `kubectl -n argocd get applications`.

## Gaps

- One server and one agent. There is still no second control-plane node. The host cannot spare another VM next to `agent-sandbox`.
- The 4 GiB server has restarted k3s under memory pressure. The Argo CD controller, Mimir, Loki, Grafana, and Harbor run on `k3s-agent`. Vault stays on the server with a 1Gi file volume. Avoid extra workloads on `k3s-server`.
- Grafana stays 12.3.1. On 26 Nov 2026 the newest chart in `https://grafana.github.io/helm-charts` is 10.5.15, appVersion 12.3.1. That index has no appVersion 13. The community chart that ships Grafana 13 (12.0.0 and later) also changes the admin-password file env and, at 13.0.0, switches to a distroless image. That is a values rewrite, so it was not applied.
- Harbor stays 2.15.4 from chart 1.19.4. `https://helm.goharbor.io` has no 2.15.5 chart. Chart 1.19.5 is not published. The 2.15.5 images exist, and pinning them made Harbor pull `harbor-db:v2.15.5` through itself while the database was down. The proxy returned 500, the database pod could not start, and the pin was reverted. After the rollback the UI at `https://harbor.lab.test/` returned 200.
- Hubble relay still serves the UI without TLS. Turning server TLS on made relay fail to verify the Cilium CA, so that change was reverted. The envoy DaemonSet stays. See `docs/hubble-envoy.md`.
- The API is slow while Argo CD renders several Helm charts. `make apply` does not wait for every app to become Healthy.
- Multipass addresses are DHCP. Imported memory and disk stay as byte counts in state, with `ignore_changes`, so a plan does not stop the VMs to rewrite `4G` or `30G`. That ignore list was left in place.
- VMware and Proxmox modules are real and validate. They have not been applied. Inventory for them is `python3 scripts/render-inventory.py tofu/platforms/vmware` or `tofu/platforms/proxmox`.
- Moving Harbor onto the agent recreated the registry, Redis, and jobservice volumes, because those disks were bound to `k3s-server`. The database volume was already on the agent and was kept. The old `lab/busybox:lab` tag was on the recreated registry disk.
