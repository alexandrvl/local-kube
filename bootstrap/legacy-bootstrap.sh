#!/usr/bin/env bash
# Previous imperative bootstrap. Do not use it to create or delete VMs.
# OpenTofu owns k3s-server and k3s-agent. The recreate path is `make apply`.
# This file remains only as a record of the original Helm/Gitea steps.
# Idempotent lab bootstrap. Multipass runs on this host. The script never
# stops, resizes, or deletes the agent-sandbox VM.
#
# It brings up k3s, Cilium, cert-manager, HAProxy, Gitea, and Argo CD, pushes
# this repo to Gitea, and applies the app-of-apps. Argo CD then syncs Vault,
# observability, Harbor, Kyverno, Falco, OpenCost, teams, and the sample app.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

# shellcheck disable=SC1091
source "${ROOT}/versions/pins.env"
# shellcheck disable=SC1091
source "${ROOT}/versions/charts.env"

mkdir -p "${ROOT}/credentials"
chmod 700 "${ROOT}/credentials"
SECRETS="${ROOT}/credentials/lab-secrets.env"

if [[ ! -s "$SECRETS" ]]; then
  python3 - <<PY
import secrets, pathlib
p = pathlib.Path("${SECRETS}")
def tok(n):
    return secrets.token_urlsafe(n)[:n]
text = """GITEA_ADMIN_USER=labadmin
GITEA_ADMIN_PASSWORD={g}
GITEA_ADMIN_EMAIL=labadmin@lab.local
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD={gr}
HARBOR_ADMIN_USER=admin
HARBOR_ADMIN_PASSWORD={h}
HARBOR_SECRET_KEY={hk}
VAULT_ROOT_TOKEN={v}
ARGOCD_ADMIN_USER=admin
""".format(g=tok(24), gr=tok(20), h=tok(20), hk=tok(16), v=tok(24))
p.write_text(text)
p.chmod(0o600)
PY
  echo "wrote ${SECRETS}"
fi
# shellcheck disable=SC1090
set -a
source "$SECRETS"
set +a
chmod 600 "$SECRETS"

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing $1" >&2; exit 1; }
}
require multipass
require helm
require kubectl
require python3

if multipass info agent-sandbox >/dev/null 2>&1; then
  echo "agent-sandbox is present and will be left running"
fi

launch() {
  local name="$1" cpus="$2" mem="$3" disk="$4"
  if multipass info "$name" >/dev/null 2>&1; then
    echo "${name} already exists"
  else
    multipass launch "$UBUNTU_IMAGE" --name "$name" --cpus "$cpus" --memory "$mem" --disk "$disk" \
      --cloud-init "${ROOT}/bootstrap/cloud-init.yaml"
  fi
  multipass exec "$name" -- cloud-init status --wait
}

launch k3s-server "$K3S_SERVER_CPUS" "$K3S_SERVER_MEMORY" "$K3S_SERVER_DISK"
launch k3s-agent "$K3S_AGENT_CPUS" "$K3S_AGENT_MEMORY" "$K3S_AGENT_DISK"

vm_ip() {
  multipass info "$1" --format json | python3 -c '
import json, sys
ips = json.load(sys.stdin)["info"][sys.argv[1]]["ipv4"]
for ip in ips:
    if not ip.startswith("10.42."):
        print(ip)
        break
' "$1"
}
SERVER_IP="$(vm_ip k3s-server)"
AGENT_IP="$(vm_ip k3s-agent)"
echo "server ${SERVER_IP} agent ${AGENT_IP}"

SERVER_IP="$SERVER_IP" ROOT="$ROOT" python3 - <<'PY'
import os, re
from pathlib import Path
ip = os.environ["SERVER_IP"]
root = Path(os.environ["ROOT"])
cilium = root / "platform/cilium/values.yaml"
out = []
for line in cilium.read_text().splitlines(True):
    if line.startswith("k8sServiceHost:"):
        out.append(f"k8sServiceHost: {ip}\n")
    else:
        out.append(line)
cilium.write_text("".join(out))
dns = root / "platform/coredns/custom.yaml"
dns.write_text(re.sub(
    r'(answer "\{\{ \.Name \}\} 60 IN A )[0-9.]+',
    r"\g<1>" + ip,
    dns.read_text(),
    count=1,
))
PY

if ! multipass exec k3s-server -- systemctl is-active k3s >/dev/null 2>&1; then
  multipass exec k3s-server -- sudo env INSTALL_K3S_VERSION="$K3S_VERSION" bash -lc \
    "curl -sfL https://get.k3s.io | sh -s - server --cluster-init --flannel-backend=none --disable-network-policy --disable=traefik --disable=servicelb --disable-kube-proxy --tls-san=${SERVER_IP} --node-ip=${SERVER_IP} --write-kubeconfig-mode=644 --cluster-cidr=${CLUSTER_POD_CIDR} --service-cidr=${CLUSTER_SERVICE_CIDR}"
fi

TOKEN="$(multipass exec k3s-server -- sudo cat /var/lib/rancher/k3s/server/node-token)"
umask 077
cat > "${ROOT}/credentials/state.env" <<EOF
K3S_SERVER_IP=${SERVER_IP}
K3S_AGENT_IP=${AGENT_IP}
K3S_TOKEN=${TOKEN}
EOF
chmod 600 "${ROOT}/credentials/state.env"

if ! multipass exec k3s-agent -- systemctl is-active k3s-agent >/dev/null 2>&1; then
  multipass exec k3s-agent -- sudo env INSTALL_K3S_VERSION="$K3S_VERSION" K3S_URL="https://${SERVER_IP}:6443" K3S_TOKEN="$TOKEN" bash -lc \
    "curl -sfL https://get.k3s.io | sh -s - agent --node-ip=${AGENT_IP}"
fi

multipass exec k3s-server -- sudo cat /etc/rancher/k3s/k3s.yaml | sed "s/127.0.0.1/${SERVER_IP}/" > "${ROOT}/credentials/kubeconfig"
chmod 600 "${ROOT}/credentials/kubeconfig"
export KUBECONFIG="${ROOT}/credentials/kubeconfig"

helm repo add cilium https://helm.cilium.io >/dev/null 2>&1 || true
helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo add haproxytech https://haproxytech.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update >/dev/null

if ! helm status cilium -n kube-system >/dev/null 2>&1; then
  helm upgrade --install cilium cilium/cilium --version "$CILIUM_CHART" -n kube-system -f "${ROOT}/platform/cilium/values.yaml"
fi
kubectl wait --for=condition=Ready node --all --timeout=300s

if ! helm status cert-manager -n cert-manager >/dev/null 2>&1; then
  helm upgrade --install cert-manager jetstack/cert-manager --version "$CERT_MANAGER_CHART" \
    -n cert-manager --create-namespace -f "${ROOT}/platform/cert-manager/values.yaml"
fi
kubectl -n cert-manager rollout status deploy/cert-manager --timeout=180s
kubectl apply -f "${ROOT}/platform/issuers/issuers.yaml"
kubectl -n cert-manager wait --for=condition=Ready certificate/platform-ca --timeout=180s
kubectl -n cert-manager get secret platform-ca-secret -o jsonpath='{.data.ca\.crt}' | base64 -d > "${ROOT}/credentials/platform-ca.crt"

if ! helm status haproxy-ingress -n ingress >/dev/null 2>&1; then
  helm upgrade --install haproxy-ingress haproxytech/kubernetes-ingress --version "$HAPROXY_INGRESS_CHART" \
    -n ingress --create-namespace -f "${ROOT}/platform/haproxy/values.yaml"
fi

kubectl apply -f "${ROOT}/platform/coredns/custom.yaml"
kubectl apply -f "${ROOT}/platform/teams/rbac.yaml"
kubectl apply -f "${ROOT}/platform/gitea/gitea.yaml"
kubectl -n gitea rollout status deploy/gitea --timeout=240s
if ! kubectl -n gitea exec deploy/gitea -- su git -c "gitea admin user list" | grep -q labadmin; then
  kubectl -n gitea exec deploy/gitea -- su git -c "gitea admin user create --admin --username ${GITEA_ADMIN_USER} --password '${GITEA_ADMIN_PASSWORD}' --email ${GITEA_ADMIN_EMAIL}"
fi

if ! helm status argocd -n argocd >/dev/null 2>&1; then
  helm upgrade --install argocd argo/argo-cd --version "$ARGO_CD_CHART" \
    -n argocd --create-namespace -f "${ROOT}/platform/argocd/values.yaml"
fi
kubectl -n argocd rollout status deploy/argocd-server --timeout=240s
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d > "${ROOT}/credentials/argocd-admin-password"
chmod 600 "${ROOT}/credentials/argocd-admin-password"

kubectl apply -f "${ROOT}/platform/ingress/platform-ingress.yaml"
for ns in observability vault harbor kyverno falco opencost; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done
kubectl apply -f "${ROOT}/platform/network/policies.yaml"

kubectl -n gitea port-forward svc/gitea 3000:3000 >/tmp/gitea-port-forward.log 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -sf -m 2 http://127.0.0.1:3000/api/healthz >/dev/null && break
  sleep 1
done

python3 - <<PY
import pathlib, urllib.parse
pw = urllib.parse.quote("""${GITEA_ADMIN_PASSWORD}""", safe="")
path = pathlib.Path("${ROOT}/credentials/git-credentials")
path.write_text(f"http://labadmin:{pw}@127.0.0.1:3000\n")
path.chmod(0o600)
PY

curl -sS -m 20 -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"local-kubenrtetes","private":true,"auto_init":false}' \
  http://127.0.0.1:3000/api/v1/user/repos >/tmp/gitea-repo.json || true

GITEA_REMOTE="http://127.0.0.1:3000/labadmin/local-kubenrtetes.git"
if git remote get-url origin >/dev/null 2>&1 && git remote get-url origin | grep -Eq '127.0.0.1:3000|gitea.gitea.svc'; then
  git remote set-url origin "$GITEA_REMOTE"
  PUSH_REMOTE=origin
else
  git remote add lab "$GITEA_REMOTE" 2>/dev/null || git remote set-url lab "$GITEA_REMOTE"
  PUSH_REMOTE=lab
fi
git -c credential.helper="store --file ${ROOT}/credentials/git-credentials" push -u "$PUSH_REMOTE" main

kubectl -n argocd apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: gitea-local-kubenrtetes
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: http://gitea.gitea.svc.cluster.local:3000/labadmin/local-kubenrtetes.git
  username: ${GITEA_ADMIN_USER}
  password: ${GITEA_ADMIN_PASSWORD}
EOF

kubectl apply -f "${ROOT}/gitops/root.yaml"
echo "bootstrap submitted the platform app. Argo CD syncs the remaining addons from Gitea."
echo "API https://${SERVER_IP}:6443"
echo "credentials ${SECRETS}"
