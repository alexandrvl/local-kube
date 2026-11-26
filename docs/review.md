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

## Works, improve later

- Host names depend on `/etc/hosts` because `.local` never reaches DNS. A name that is not `.local`, or a small DNS resolver on the host, would survive a missing hosts line. The browser does not use CoreDNS, so this is the only path from the laptop.
- The 4 GiB server has already OOM-killed the Argo CD controller and dropped the API under memory pressure. Keep heavy addons on `k3s-agent`, or add a node when the host has room. A dead API stalls every GitOps sync.
- Vault is dev mode and Mimir, Loki, and Grafana use empty disks. A pod restart forgets secrets, metrics, and logs. That is acceptable for this lab and wrong if a review needs history after a bounce.
- `kube-system` is outside the default-deny policy. Hubble and CoreDNS need that exception, and it also means a pod in `kube-system` is not constrained by the team policy.
- Containerd on the node pulls public images outside NetworkPolicy. Pod egress to the internet is denied; image pulls are not.
- Imported Multipass memory and disk are byte counts, and `ignore_changes` hides a rewrite to `4G`/`30G`. A real resize in Multipass would also stay invisible to `tofu plan`.
- `scripts/render-inventory.py` always reads `tofu/`. Pointing it at `tofu/platforms/vmware` or `tofu/platforms/proxmox` is the remaining step before those workspaces can drive Ansible.
- Falco alerts are in the container log, which Loki can store, but there is no Grafana panel for them. An alert is easy to miss without `kubectl logs`.
- OpenCost logs a permission error on `/var/configs` and keeps config in memory. The allocation API works; the error is noise and the config is not durable.
- The Vault root token and the Harbor secret key are in the git values so Argo CD does not reset them. That is fine only while this repo stays private.
- Grafana is chart app 12.3.1, not upstream 13.3.2. Harbor is chart app 2.15.4, not upstream 2.15.5. Both are current for the charts that are pinned.
- k3s is started with `--cluster-init`, but there is still one server. The cluster is not actually highly available.
