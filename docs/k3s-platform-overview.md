# Local k3s platform

A two-node k3s lab on Multipass. OpenTofu declares the VMs. Ansible configures the OS, k3s, and the image-pull proxy. Cilium is the CNI and the kube-proxy replacement. HAProxy is the ingress. cert-manager issues a local CA. Argo CD syncs every addon from a Gitea repository inside the cluster. Team namespaces are isolated by RBAC, Kyverno, Vault, and Cilium network policy.

VM creation, Ansible, and a later hypervisor switch are in `docs/infra.md`.

## Architecture

```text
host (Multipass on KVM)
  OpenTofu (tofu/) declares k3s-server and k3s-agent
  Ansible configures the OS, k3s, and the registry mirror
  Argo CD syncs gitops/ from Gitea
        |
  k3s-server   control plane, etcd, Vault
  k3s-agent    worker for Argo CD, Harbor, Grafana, Mimir, Loki
        |
        +-- Cilium (VXLAN, Hubble) replaces flannel and kube-proxy
        +-- HAProxy DaemonSet on host ports 80 and 443
        +-- cert-manager CA (ClusterIssuer platform-ca)
        +-- Gitea --> Argo CD app-of-apps --> the rest of the platform
```

k3s starts with `--cluster-init`, so a second server can join the embedded etcd later. This lab has one server because the host already runs `agent-sandbox`. The host is itself a KVM guest. Nested virt is on, and Multipass launches the nodes on the host, not inside `agent-sandbox`.

## Nodes

| Node | Role | vCPU | RAM | Disk | IPv4 | Zone |
| --- | --- | --- | --- | --- | --- | --- |
| k3s-server | control-plane, etcd | 2 | 5G | 30G | 10.114.66.211 | zone1 |
| k3s-agent | agent | 2 | 5G | 30G | 10.114.66.179 | zone1 |

Ubuntu image `24.04`. k3s `v1.36.6+k3s1`. Pod CIDR `10.42.0.0/16`. Service CIDR `10.43.0.0/16`. API `https://10.114.66.211:6443`.

`agent-sandbox` (`10.114.66.250`, Ubuntu 26.04, 6 vCPU, 12 GiB) is not part of the cluster. It is not in the OpenTofu state, and `make destroy` refuses a plan that mentions it.

The host has 4 CPUs and about 20 GiB. After the sandbox is running and the k3s VMs are stopped, about 12 GiB is available. Each node is 5G so at least 2 GiB stays for the host. Do not add a third VM. Vault stays on `k3s-server` (1Gi file volume). Argo CD, Harbor, Mimir, Loki, and Grafana stay on `k3s-agent`.

## Apply and destroy

From this checkout, on the host:

```bash
make apply
make destroy
```

`make apply` is tofu apply, SSH key setup, inventory render, and Ansible. `make destroy` deletes only `k3s-server` and `k3s-agent`. `./bootstrap/bootstrap.sh` and `./bootstrap/destroy.sh` exec those targets. `bootstrap/legacy-bootstrap.sh` is the Helm and Gitea sequence used after a fresh k3s install. Do not use it to create or delete VMs.

A fresh cluster has no Harbor and no Argo CD yet, so the Ansible Harbor-project task and `kubectl apply` of `gitops/root.yaml` fail until those are installed. Install Cilium, cert-manager, HAProxy, Gitea, and Argo CD first, push `main`, create the out-of-git Secrets, then apply `gitops/root.yaml`.

Day-to-day kubeconfig is `~/.kube/config`, mode 600, context `default`. The repo copy is `credentials/kubeconfig`. kubectl uses `~/.kube/config` when `KUBECONFIG` is unset.

Addresses are DHCP. After a recreate, update `k8sServiceHost` in `platform/cilium/values.yaml`, the A record in `platform/coredns/custom.yaml`, `platform/dns/lab-test.conf`, the host `lab-dns` container, and `/etc/hosts`, then refresh `~/.kube/config`.

## Versions

Pinned in `versions/pins.env` and `versions/charts.env`.

| Component | Version |
| --- | --- |
| k3s / Kubernetes | v1.36.6+k3s1 (Cilium 1.20.5 tests 1.33–1.36) |
| Cilium / Hubble | chart 1.20.5 |
| HAProxy ingress | chart 1.57.1, app 3.4.1 |
| cert-manager | v1.21.3 |
| Argo CD | chart 10.20.0, app v3.6.3 |
| Gitea | `docker.io/gitea/gitea:28.4.0` (manifest; chart 12.7.0 still packages 1.27.0) |
| Vault | chart 0.34.1, server 2.2.2, injector `hashicorp/vault-k8s:1.7.6` |
| Alloy | chart 1.16.0, v1.22.0 |
| Mimir | `grafana/mimir:3.2.6` |
| Loki | `grafana/loki:3.7.10` |
| Grafana | chart 10.5.15, app 12.3.1 |
| kube-state-metrics | chart 9.1.0, app 2.20.0 |
| node-exporter | chart 6.0.0, app 1.13.1 |
| Harbor | chart 1.19.4, app 2.15.4 |
| Kyverno | chart 3.10.0, app v1.20.0 |
| Falco | chart 9.2.2, app 0.45.2, `modern_ebpf` |
| OpenCost | chart 2.5.35, app 1.121.5 |
| KrakenD | `docker.io/library/krakend:3.0.5` |
| sample | `ghcr.io/stefanprodan/podinfo:6.17.0` |

Grafana stays on chart 10.5.15. The index at `https://grafana.github.io/helm-charts` has no appVersion 13. A community chart that ships Grafana 13 changes the admin-password file and the image layout, so it is not a pin. Harbor stays on chart 1.19.4. `https://helm.goharbor.io` has no 2.15.5 chart, and pinning those images makes Harbor pull its own database through the proxy while the database is down.

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

`team-deployer` is a namespaced Role. There is no ClusterRoleBinding for the team accounts. `system:serviceaccount:team-a:team-a` can create objects in `team-a` and is forbidden in `team-b`.

## Network policy

Cilium `policyEnforcementMode` is `default`. `hostFirewall.enabled` is true and attaches to `eth0`. Manifests: `platform/network/policies.yaml`. Argo CD app: `gitops/apps/network.yaml`.

`restricted-baseline` selects every pod. Same-namespace traffic is allowed by `allow-same-namespace`. Other ingress is only from entities `host`, `remote-node`, `health`, `kube-apiserver`, and from namespaces `ingress` and `observability`. Other egress is only to those entities and to `kube-dns` on UDP/TCP 53.

| Policy | What it allows |
| --- | --- |
| ingress-controller | World and host to HAProxy 80, 443, 8080, 8443, 1042. HAProxy egress to every pod |
| argocd-git-and-charts | Argo CD to Gitea TCP 3000, and to chart hosts on 80 and 443: `helm.cilium.io`, `cilium.github.io`, `helm.releases.hashicorp.com`, `helm.goharbor.io`, `charts.jetstack.io`, `github.com`, `*.github.io`, `*.githubusercontent.com`. A DNS L7 rule (`matchPattern: "*"`) is required so those names are learned. `helm.cilium.io` CNAMEs to `cilium.github.io` |
| gitea-from-argocd | Gitea accepts TCP 3000 from Argo CD |
| vault-from-teams / teams-to-vault | Vault TCP 8200 between team namespaces and Vault |
| registry-pulls | Non-kube-system pods to Harbor TCP 80, 443, 5000 |
| pods-no-world-https | Every pod except Harbor and Argo CD is denied TCP 443 to the world |
| node-no-world-https | The node root namespace is denied TCP 443 to the world. Other node egress stays allowed (`toEntities: [all]`, then the deny) |
| harbor-proxy-upstream | Harbor pods may egress to the world on TCP 443. That is the only registry upstream |
| kube-system-platform | kube-system to the API, node, and health endpoints |
| kube-dns-from-cluster | Cluster pods to CoreDNS :53, and CoreDNS to the host resolver :53 |

## Image-pull proxy

Ansible role `registry_proxy` writes `/etc/rancher/k3s/registries.yaml` on both nodes. containerd rewrites these hosts to a Harbor pull-through project: `docker.io` and `registry.hub.docker.com` (`dockerhub`), `ghcr.io` (`ghcr`), `quay.io` (`quay`), `registry.k8s.io` (`k8s`), `k8s.gcr.io` (`k8s-gcr`), `gcr.io` (`gcr`), `mirror.gcr.io` (`mirror-gcr`), `ecr-public.aws.com` and `public.ecr.aws` (`ecr-public`), `reg.kyverno.io` (`kyverno`), `registry.gitlab.com` (`gitlab`), `docker.elastic.co` (`elastic`), `mcr.microsoft.com` (`mcr`), `nvcr.io` (`nvcr`), and `us-docker.pkg.dev`, `europe-docker.pkg.dev`, `asia-docker.pkg.dev` (`gar-us`, `gar-eu`, `gar-asia`).

The node resolves `harbor.lab.test` to the ingress: `127.0.0.1` on the server, the server IP on the agent. kubelet and `k3s crictl` use that mirror. `k3s ctr` does not.

Cilium is the control that closes registries the hostname list misses. `node-no-world-https` and `pods-no-world-https` deny world TCP 443. Harbor is the exception. An iptables list (`registry-block.list`) is still installed. A pull check is `sudo k3s crictl pull` of a small public image on the agent, which shows up in Harbor as the matching proxy project.

## TLS and DNS

`ClusterIssuer/selfsigned-bootstrap` issues Certificate `platform-ca` (namespace `cert-manager`, secret `platform-ca-secret`, CN `lab-platform-ca`). `ClusterIssuer/platform-ca` issues workload certificates. The CA is exported to `credentials/platform-ca.crt` and installed on the host trust store.

The only lab suffix is `lab.test`. CoreDNS keeps the stock Corefile. `platform/coredns/custom.yaml` adds a `template` plugin so every `*.lab.test` name answers the server IP. A second `hosts` plugin crash-loops CoreDNS.

On the host, container `lab-dns` (dnsmasq) mounts `platform/dns/lab-test.conf` and listens on `127.0.0.1:5354`. `/etc/systemd/resolved.conf.d/lab-test.conf` sends only `lab.test` there (`DNS=127.0.0.1:5354`, `Domains=~lab.test`). `/etc/hosts` lists the same names. HAProxy is a DaemonSet with host ports. Ingress objects have an empty load-balancer status, and Argo CD treats those Ingresses as Healthy.

## URLs and credentials

| Service | URL | Login |
| --- | --- | --- |
| API | https://10.114.66.211:6443 | `~/.kube/config`, context `default` |
| Sample app | https://sample.lab.test/healthz | none |
| Argo CD | https://argocd.lab.test | user `admin`, password file `credentials/argocd-admin-password` |
| Grafana | https://grafana.lab.test/login | user `admin`, `GRAFANA_ADMIN_PASSWORD` in `credentials/lab-secrets.env` |
| Hubble UI | https://hubble.lab.test | none |
| Gitea | https://gitea.lab.test | user `labadmin`, `GITEA_ADMIN_PASSWORD` in `credentials/lab-secrets.env` |
| Harbor | https://harbor.lab.test | user `admin`, `HARBOR_ADMIN_PASSWORD` in `credentials/lab-secrets.env` |
| Vault | https://vault.lab.test | root token `VAULT_ROOT_TOKEN` in `credentials/lab-secrets.env` |
| OpenCost | https://opencost.lab.test | none |

`credentials/` is mode 700 and gitignored. It holds `lab-secrets.env`, `lab-ssh`, `git-credentials`, `platform-ca.crt`, `kubeconfig`, `state.env`, and `argocd-admin-password`. Cluster Secrets `vault-unseal`, `harbor-secret-key`, `harbor-admin`, and `grafana-admin` are created from that env file and are not in git. A recreate has to create them again before those charts become Ready. A new Vault also needs `vault operator init`; the previous unseal key does not open the new data directory.

Git remote used by Argo CD: `http://gitea.gitea.svc.cluster.local:3000/labadmin/local-kubenrtetes.git`. The host remote `origin` is `http://127.0.0.1:3000/labadmin/local-kubenrtetes.git` and only works while `kubectl -n gitea port-forward svc/gitea 3000:3000` is running.

`gitops/root.yaml` is Application `platform`. It syncs `gitops/apps/` with prune and selfHeal. Repo server parallelism is 1.

## Team API gateways

Each team runs KrakenD in front of its own API. The API has no Ingress of its own. Callers send `User` and `Token`. `Token` is a JWT. `aud` is the API id, and `sub` must match `User`. KrakenD copies `sub` into `X-Customer`. The service uses that caller for ownership checks. A time-series type, a price, or a customer name already owned by someone else is rejected.

| API | URL | API id |
| --- | --- | --- |
| Physical trading time series | https://physical-trading.trading.lab.test/time-series | `physical-trading/time-series` |
| Energy prices | https://energy-prices.trading.lab.test/prices | `energy-prices` |
| Customers | https://customers.customer.lab.test/customers | `customers` |

`CustomerToken` objects live in the team namespace. A controller signs them with that gateway's cert-manager Certificate, which is separate from the TLS certificate, and writes the token string to `secret/teams/<team>/tokens/<api>/<customer>`. The object status stores `jti`, `kid`, and expiry. The lab objects use `ttl: 3m` and `overlap: 90s` so both tokens can be observed in one run. The controller also accepts the agreed `720h` and `168h` schedule.

Delivery teams copy tokens from the Vault UI. The userpass users are `team-a`, `team-b`, and `team-c`. Their passwords are `VAULT_UI_TEAM_A`, `VAULT_UI_TEAM_B`, and `VAULT_UI_TEAM_C` in `credentials/lab-secrets.env`. Customers do not get a Vault login.

Gitea Actions builds the service images. The runner is `gitea/act_runner` on the host, with the host Docker socket, because the k3s nodes have containerd only. Its job options mount the platform CA. The job adds that CA to the Alpine trust store, because the Docker 27 client checks Harbor itself. The options do not mount the socket again. Images land in private Harbor projects `team-a`, `team-b`, and `team-c`. The controller image is `lab/token-controller`. Pods run on `k3s-agent`.

## Vault ingestion

Vault 2.2.2 runs as `vault-0` on `k3s-server`, file storage on PVC `data-vault-0` (1Gi). A postStart hook unseals from Secret `vault-unseal` (keys `unseal-key` and `root-token`), then writes policies and Kubernetes auth roles from ConfigMap `vault-auth`. kv-v2 is mounted at `secret/`. The injector runs on `k3s-agent`. The projected token audience is `https://kubernetes.default.svc.cluster.local`.

Policy `team-a` reads `secret/data/teams/team-a/*` and is bound to ServiceAccount `team-a` in namespace `team-a`. Policy `team-b` reads only its own path. A pod that uses role `team-b` and asks for the team-a path gets `403` from the agent init container.

The sample Deployment in `team-a` uses:

```yaml
vault.hashicorp.com/agent-inject: "true"
vault.hashicorp.com/role: "team-a"
vault.hashicorp.com/agent-inject-secret-demo: "secret/data/teams/team-a/demo"
vault.hashicorp.com/agent-inject-template-demo: |
  {{- with secret "secret/data/teams/team-a/demo" -}}
  {{ .Data.data.value }}
  {{- end }}
```

The file is `/vault/secrets/demo`. The sidecar re-renders static KV secrets every 30 seconds. Write the secret with `vault kv put secret/teams/team-a/demo value=...` after the server is unsealed. `kubectl -n team-a exec deploy/sample -c sample -- cat /vault/secrets/demo` returns that value. The sample container has no Vault token.

## Hubble and envoy

Hubble relay is enabled. Its server TLS stays off (`disable-server-tls: true`): the UI talks to relay on port 4245 without TLS. The relay client connection to the agents is already TLS. Turning `hubble.relay.tls.server.enabled` on makes relay fail with `x509: certificate signed by unknown authority` while verifying the Cilium CA, and the UI returns 503. Leave server TLS off.

DaemonSet `cilium-envoy` stays. `enable-l7-proxy` and the external envoy are what resolve the Argo CD chart names. Removing the DaemonSet drops those lookups.

## Observability and supply chain

Alloy remote-writes to Mimir and ships pod logs to Loki. Grafana datasources are `mimir` (`http://mimir.observability.svc.cluster.local:9009/prometheus`) and `loki`. Dashboards `Cluster`, `Hubble`, `Sample app`, and `Falco` are ConfigMap `lab-dashboards`. Mimir PVC `mimir-data` is 2Gi, Loki `loki-data` is 2Gi, Grafana 1Gi, all local-path on `k3s-agent`.

Kyverno policies are `policies.kyverno.io/v1` ValidatingPolicy objects with `validationActions: [Deny]` on team-a/b/c: `require-team-app-label` and `disallow-privileged-teams`. The Kyverno Argo CD app uses server-side apply. Client-side apply puts the whole custom resource definition in `last-applied-configuration`, and the policy definitions exceed that annotation’s 256KiB limit. Falco is a DaemonSet with `driver.kind=modern_ebpf`. It uses the rules file shipped in the image. falcoctl artifact install is off, because a pod cannot open world TCP 443 to download the rules index. OpenCost queries Mimir. Harbor Trivy is off. Harbor expose type is Ingress, not the bundled nginx.
