# Platform review

Reviewed 26 Nov 2026, 13:51 Europe/Tallinn, from the host against the running cluster. No redesign. One break was fixed so the host check could run: `/etc/hosts` had no `lab.local` names.

## Counts

26 pass, 0 fail.

## Broken now

None, after the hosts and CA fix below.

## Fixed so a test could run

`https://sample.lab.local` was an unknown host. `/etc/hosts` was still the stock file from 23 Aug 2026, so `getent hosts sample.lab.local` returned nothing and curl reported `Could not resolve host`. `systemd-resolved` does not answer `*.local`, and `nsswitch.conf` is `files mdns4_minimal [NOTFOUND=return] dns`, so a missing hosts line never reaches another DNS server. The node was fine the whole time: TCP `10.114.66.182:443` was open, HAProxy host port 443 is container port 8443, and `curl --resolve sample.lab.local:443:10.114.66.182` returned HTTP 200. The certificate SAN is `DNS:sample.lab.local`, issuer `CN=lab-platform-ca`.

The hosts line was written, the CA was added to `/usr/local/share/ca-certificates/lab-platform-ca.crt`, and the same CA was imported into Firefox profile `dkj2x7yf.default` and `~/.pki/nssdb`. Restart Firefox if it was already open.

Check that now succeeds, with no `--resolve` and no `--cacert`:

```text
curl -sS -o /dev/null -w '%{http_code}\n' https://sample.lab.local/healthz
200
```

## Feature results

| Feature | Result | Evidence |
| --- | --- | --- |
| Nodes | pass | `k3s-agent` and `k3s-server` both `Ready` |
| k3s version | pass | both nodes `v1.36.6+k3s1` |
| Cilium | pass | both agents `1/1`, `cilium status --brief` printed `OK` |
| Hubble | pass | relay `1/1`, UI `2/2`, `https://hubble.lab.local/` returned 200 |
| Same-namespace | pass | wget `sample.team-a.svc:9898/healthz` exit 0 |
| Cross-namespace deny | pass | wget `listener.team-b.svc:8080` returned `Host is unreachable` |
| External egress deny | pass | wget `http://1.1.1.1/` timed out, exit 1 |
| DNS allow | pass | nslookup `kubernetes.default.svc.cluster.local` returned `10.43.0.1` |
| Sample ingress | pass | `https://sample.lab.local/healthz` returned 200, cert SAN `sample.lab.local` |
| Other UIs | pass | argocd 200, grafana 302, gitea 200, harbor 200, vault 307, opencost 200 |
| Argo CD apps | pass | all 23 applications `Synced` `Healthy`, including network and coredns-custom |
| Team RBAC | pass | team-a created a ConfigMap in team-a and was Forbidden in team-b; team-c created one in team-c and was Forbidden in team-a; team-b cannot create in team-c |
| Vault | pass | team-a read `secret/data/teams/team-a/demo` (`team-a-ok`) and got HTTP 403 on `teams/team-b/demo` |
| Metric | pass | Mimir `sum(kube_pod_status_phase{phase="Running"})` returned 45 |
| Log | pass | Loki `{namespace="team-a"}` returned `Starting HTTP Server` on `:9898` |
| Alloy | pass | both DaemonSet pods `2/2` |
| Grafana | pass | `https://grafana.lab.local/api/datasources` returned 200 with the admin user |
| Gitea | pass | `https://gitea.lab.local/api/v1/version` returned 200 |
| Harbor | pass | artifact `lab/busybox:lab` present, digest `sha256:b7f3d86d6e84…` |
| Kyverno | pass | unlabeled pod in team-a denied by `require-team-app-label` |
| Falco | pass | rules loaded from `falco_rules.yaml` (`schema validation: ok`); a Critical alert for the Gitea hook is in the log |
| OpenCost | pass | `/allocation?window=1d&aggregate=namespace` code 200, 12 namespaces |
| OpenTofu plan | pass | `tofu plan -detailed-exitcode` exit 0, no changes |
| Ansible | pass | second `ansible-playbook ansible/site.yml`: both hosts `changed=0` |
| Destroy guard | pass | destroy plan is only `k3s-server` and `k3s-agent` (2 to destroy). Guard printed `plan guard ok` and was not applied. `agent-sandbox` stayed Running at `10.114.66.250`. A plan that mentions `agent-sandbox` is refused by `scripts/plan-guard.py` |

## Improvements

Checked again on 26 Nov 2026, 14:22 Europe/Tallinn, after the changes below. The cluster stayed up. No VM was destroyed. `agent-sandbox` was still Running at `10.114.66.250`.

| Item | Status | Evidence |
| --- | --- | --- |
| Host DNS | done | One suffix, `lab.test`. dnsmasq on `127.0.0.1:5354` answers it, and `notinhosts.lab.test` resolves without a hosts line. `curl https://sample.lab.test/healthz` returned 200 with no `--resolve`. Certificate `sample-tls` is Ready for `sample.lab.test`. Old `.local` names were not kept. |
| Memory | done | Argo CD controller, Mimir, Loki, Grafana, and every Harbor pod are on `k3s-agent`. None of those are on `k3s-server`. No third VM. Vault stays on the server with its small volume. |
| Persistence | done | Vault file PVC `data-vault-0` on the server. After a pod delete the sample account read `vault-persist-ok`. Loki still returned `persist-proof-1795695586028826315` and Mimir still returned 45 at time `1795695586` after those pods were deleted. Grafana has a 1Gi PVC. |
| kube-system policy | done | `restricted-baseline` now selects every namespace. `kube-system-platform` and `kube-dns-from-cluster` allow the API, health, Hubble, and DNS, including CoreDNS egress to the host resolver on port 53. `cilium status --brief` printed `OK`. The first cut blocked that upstream query and lookups stalled; the port 53 allow fixed it, so the exemption was not restored. |
| Inventory | done | `scripts/render-inventory.py` takes a workspace and defaults to `tofu/`. `python3 scripts/render-inventory.py` rewrote the Multipass inventory. VMware and Proxmox were not applied. The commands are in `docs/infra.md`. |
| Falco panel | done | Dashboard `Falco` in `lab-dashboards`. `{namespace="falco"} \|= "Critical"` returned a new Critical alert for `/tmp/review-gitea`. |
| OpenCost | done | PVC `opencost-pvc` is mounted at `/var/configs`. `default.json` is owned by uid 1001. Allocation API returned HTTP 200. The permission error is gone from the new pod log. |
| Secrets in git | done | `platform/vault/values.yaml` has no root token. Harbor uses Secret `harbor-secret-key` and the values file has no `secretKey`. A sync does not render either value. The unseal key and root token are in `credentials/lab-secrets.env` and Secret `vault-unseal`. |
| Second control-plane node | still open | Left on purpose. The host has no room for another VM, and this pass did not add one. |
| Public image pulls | done | Both nodes pull through Harbor. `k3s crictl pull docker.io/library/alpine:3.18` on `k3s-agent` stored a new image, and Harbor fetched `dockerhub/library/alpine@sha256:de0eb0b3f2a4…` from upstream. `curl -4 https://registry-1.docker.io/v2/` was reset on the server and failed to connect on the agent. Registries absent from `registry-block.list` are still a direct exception. |
| Grafana 13 and Harbor 2.15.5 | still open | The pinned charts ship Grafana 12.3.1 and Harbor 2.15.4. Those charts were not replaced. |
| Multipass `ignore_changes` | still open | Left on purpose. Dropping it would plan an in-place resize and stop the VMs. |

Harbor’s registry, Redis, and jobservice disks were on `k3s-server`. Pinning those pods to the agent required new volumes. The database volume was already on the agent and was kept. The UI at `https://harbor.lab.test/` returned 200. The previous `lab/busybox:lab` tag was on the disk that had to be recreated.

The image-pull proof ran at 15:12 Europe/Tallinn while `k3s` on the server was the process that started at 15:09:28. `NRestarts` stayed 3 and `/readyz` stayed ok. No new pod was scheduled on the server for the pull.

## cert-manager Hubble drops

Checked 26 Nov 2026, 14:39 Europe/Tallinn. The only drops were Alloy in `observability` to cert-manager pods, TCP 9402, `POLICY_DENIED`. That is the Prometheus port. Webhook, ACME, cainjector, and leader election were not dropped. Certificates stayed Ready.

Allowed 26 Nov 2026, 15:05 Europe/Tallinn, and only that scrape. `alloy-cert-manager-metrics` lets Alloy egress to cert-manager on TCP 9402. `cert-manager-metrics-from-observability` lets cert-manager accept TCP 9402 from `observability`. No other port or namespace was added. `restricted-baseline` is unchanged.

A scrape from the Alloy pod returned `certmanager_clock_time_seconds`. A fresh Hubble window on `k3s-agent` had no `DROPPED` flows for `cert-manager`, and the same port was forwarded:

```text
Nov 26 13:07:30.893: observability/alloy-w7mfr:50868 -> cert-manager/cert-manager-7fd5ddbf9b-8flc5:9402 to-overlay FORWARDED (TCP Flags: SYN)
```
