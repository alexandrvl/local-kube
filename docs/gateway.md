# Team API gateways

Decision record for the three team APIs running in this lab. It follows the manifests in `platform/gateways/`, the token controller, the service code, the Gitea workflows, and the review plan those were built from. The short operator summary stays in `docs/k3s-platform-overview.md`.

A token belongs to one exposed API. It does not belong to a domain, and it does not belong to a team. Trading and customer are domain groups. They show up as the hostname suffix. They are not a shared process.

## One KrakenD per team

Each team runs its own KrakenD in its own namespace. Team A cannot edit team B's gateway. A shared process was rejected because one config, one JWKS, and one outage would then cover every API. A mistake in that shared config could make team A's signing key verify a token on team C. Separate processes keep the audience, the signing key, and the failure inside the team that owns the API.

The domain is only the suffix of the hostname. `trading.lab.test` groups the two trading APIs. `customer.lab.test` is the customer API. `*.lab.test` already resolves to the cluster, so these names needed no DNS change. Each team owns the Ingress, the TLS certificate, and the KrakenD config for its host. HAProxy sends the hostname only to that team's gateway Service.

There is one Argo CD Application, `gateways`, which syncs `platform/gateways` from `main`. The separation is the namespace and the process, not a separate Git repository per team.

A later API on an existing host, such as `physical-trading/orders`, would be another route and another `CustomerToken` on the team-a gateway. It would not get a new KrakenD, and it would not share tokens with time series. That second API is not built.

## Hosts, API ids, and names

| Domain | Team | Host | API id | Path |
| --- | --- | --- | --- | --- |
| trading | team-a | `physical-trading.trading.lab.test` | `physical-trading/time-series` | `/time-series` |
| trading | team-b | `energy-prices.trading.lab.test` | `energy-prices` | `/prices` |
| customer | team-c | `customers.customer.lab.test` | `customers` | `/customers` |

The API id is `spec.api` on the `CustomerToken` and the `aud` claim of the JWT. The slash form is allowed so one host can expose more than one API later and each API stays a distinct audience.

A Kubernetes object name cannot contain a slash. The sample object is therefore `acme-physical-trading-time-series` in namespace `team-a`, while `spec.api` is `physical-trading/time-series`. `spec.domain` is `trading` or `customer`. The controller does not copy `domain` into the JWT. The audience is `spec.api`, and the issuer is the public `https` host of that gateway.

The same customer who calls both trading APIs has two objects and two tokens: `acme-physical-trading-time-series` in team-a, and `acme-energy-prices` in team-b. Globex has the same pair, plus `acme-customers` and `globex-customers` in team-c.

Team C's Service is named `customers-api` so it is not the same name as the Ingress. KrakenD calls `http://customers-api:8080`. Team A calls `http://timeseries:8080`. Team B calls `http://prices:8080`.

## User, Token, and X-Customer

Callers send two headers. `User` is the customer id. `Token` is the raw JWT, not a `Bearer` token. KrakenD 3.0.5 reads it with `auth_header_name: Token`.

The controller signs RS256. The claims are:

| Claim | Value |
| --- | --- |
| `sub` | `spec.customer` |
| `aud` | `spec.api` |
| `iss` | the gateway's public `https` host |
| `iat`, `nbf` | time of issuance |
| `exp` | issuance plus `spec.ttl` |
| `jti` | a new UUID for that issuance |
| `kid` | SHA-256 of the signing certificate's Subject Public Key Info, base64url, in the JWT header |

KrakenD checks the signature against that team's JWKS, then checks `aud` and `iss`. `propagate_claims` copies `sub` into `X-Customer`. That write replaces a client-supplied `X-Customer`. A CEL expression then requires exactly one `User`, exactly one `X-Customer`, and those two values equal. The service never reads a customer id from the JSON body. Ownership uses `X-Customer` only.

This is why a caller cannot act as someone else by setting the header. On the physical-trading API, a POST with `User: acme`, a valid acme token, and `X-Customer: globex` was stored with owner `acme`.

A missing `Token` is HTTP 401 from KrakenD, and the Python service is not called. A `User` that does not match `sub` fails the CEL check. KrakenD aborts that request with HTTP 500 and body `Error #01: request aborted by evaluator #0`. That is still a gateway rejection: the service is not called. A token for `physical-trading/time-series` sent to `https://energy-prices.trading.lab.test` is HTTP 401. The gateways do not share a JWKS, and the audiences differ.

`output_encoding` and the backend encoding are `no-op`, so a 409 or 404 from the service is returned as the service sent it. `/.well-known/jwks.json` is proxied with no auth, so an external customer can fetch the public keys. KrakenD itself fetches JWKS over in-cluster HTTP (`disable_jwk_security: true`), because a team pod cannot open TCP 443 to the world and cannot call back through the public Ingress. The cache is 60 seconds, and a failed key waits 10 seconds before another fetch. `USAGE_DISABLE=1` stops KrakenD from calling its usage endpoint, which the same 443 deny would block.

The Python services have no Ingress. `CiliumNetworkPolicy/api-from-gateway` in each team namespace allows ingress only from pods labeled `app.kubernetes.io/component: gateway`, and denies the ingress namespace and every other pod. A `wget` from the team-a sample pod to `timeseries.team-a.svc:8080` timed out.

## Signing certificates

The certificate that terminates HTTPS is not the certificate that signs tokens. The TLS secret (`physical-trading-tls`, and the same pattern for the other two hosts) is issued for the hostname and is presented to every client. The signing secret is a different cert-manager `Certificate`, one per gateway: `physical-trading-signer`, `energy-prices-signer`, and `customers-signer`. Each is RSA 2048, usage `digital signature`, duration one year, and `issuerRef` `ClusterIssuer/platform-ca`. The CA's common name is `lab-platform-ca`.

Reusing the TLS key would tie token validity to HTTPS renewal, and anyone who can read the TLS secret could mint tokens. Sharing one signing key across teams would let a token signed for team A verify on team C. Teams may share the CA. They do not share a signing key.

Later, the same `Certificate` can point `issuerRef` at an organisation issuer. The token shape does not change: `kid` still names the public key in that gateway's JWKS, and the private key still stays in the cert-manager Secret. The controller is the only principal allowed to read that Secret. The delivery team and the customer never see it.

JWKS is the public half. The controller writes ConfigMap `gateway-jwks`, which is not in Git. The API serves it from `/etc/jwks/jwks.json`. If a Secret named `signer-previous` exists, its certificate is added to the document. KrakenD caches that document and does not restart when it changes.

## Token rotation and signing-key rotation

These are different events.

Token rotation is the normal schedule. The controller signs a new JWT with the same key. The previous JWT stays valid until its own `exp`. JWKS does not change. The controller polls every 10 seconds. While `now` is earlier than `exp` minus `overlap`, it does nothing. Inside the overlap it moves the current token string to `previous`, copies the old `jti`, `kid`, and expiry onto `status.previous`, and signs a new current token. After the old `exp`, the next pass issues a fresh token and stores an empty `previous`.

Signing-key rotation is rare. A new certificate replaces the signer Secret. New tokens use the new `kid`. The previous public certificate has to remain in JWKS until every token it signed has expired, which is what `signer-previous` is for. That rotation has not been run. The Secret is named in the controller Role so it can be read when it exists. It is not present in the lab.

The agreed schedule is `ttl: 720h` and `overlap: 168h`: the replacement is issued one week before expiry, and both tokens work during that week. The controller accepts that form. The objects in Git use `ttl: 3m` and `overlap: 90s` so the window can be seen in one run. `overlap` must be shorter than `ttl`.

On 26 November 2026 the physical-trading acme token was rotated without restarting KrakenD. The previous token expired at 18:47:49 Europe/Tallinn. The replacement expired at 18:49:21 Europe/Tallinn. During the overlap, POST `/time-series` returned 200 for both the previous token and the current token. After 18:47:49 the previous token returned 401 and the current token returned 200.

## Where the token string lives

The platform issues the token. The customer stores the string and sends it back. The customer does not create a key and does not upload one.

The string is a kv-v2 secret:

`secret/teams/<team>/tokens/<api>/<customer>`

The slash in `physical-trading/time-series` is a path separator, so the acme time-series token is `secret/teams/team-a/tokens/physical-trading/time-series/acme`. The object has two keys, `current` and `previous`. Energy prices and customers have one fewer segment, for example `secret/teams/team-b/tokens/energy-prices/acme`. Issuer policies allow create, read, and update on those token paths only. Human read policies allow read, and list on the metadata. A team policy cannot read another team's path. That was checked with a team-b userpass token and with a team-a Kubernetes login: each was denied on the other team's path and could still read its own.

The string is not in Git, not in `CustomerToken` status, and not managed by Argo CD. Status stores `jti`, `kid`, `issuedAt`, `expiresAt`, and the same four fields under `previous`. The CRD has no field for the token. Argo CD syncs the spec from `platform/gateways`. The controller writes the Vault object after that.

A delivery team was supposed to copy the string from the Vault UI, without kubectl, and send it to the customer out of band. The customer does not get a Vault, Argo CD, or Grafana account.

A human Vault login for a delivery team is still missing. The controller logs in with Kubernetes auth as the `token-controller` ServiceAccount and role `team-a-issuer` (and the matching roles in team-b and team-c). The Vault UI is up, and the operator created userpass users `team-a`, `team-b`, and `team-c` bound to the team read policies. Those passwords stay in the operator's `vault-ui` Secret and in `credentials/lab-secrets.env`. They were not given to a delivery team. There is also no Argo CD or Grafana account for a delivery team, so the team cannot yet confirm expiry in Argo CD or copy the token themselves. The root token stays with the operator.

## Business rules

KrakenD decides whether the token is valid. The service then decides whether this caller may change this data. Gateway acceptance is not enough. The caller is `X-Customer`. If that header is missing, the service returns 401. Data is in memory and disappears when the pod restarts.

On physical trading, a series type has one owner. The first caller to POST a type claims it and may append points. Another caller POSTing that type gets 409 and the stored points stay. A GET of someone else's type is 404, so the existence of the type is not confirmed to them. The owner's GET is 200. Two callers may use the same instrument name under different types. They cannot share a type.

The body is `{type, instrument, ts, value}`. A customer id in that JSON is ignored.

Team B and team C use the same caller header and the same 409 / 404 split. A price is owned by the pair `(market, delivery)`. A customer record is owned by `name`. The first caller to write that key owns it. A later write by the owner replaces the value (prices) or keeps the record (customers). Another caller gets 409 and the record stays. A GET of a key that caller does not own is 404. Team B and team C do not apply the time-series type rule. They only change records that caller created.

These checks were run against the Actions-built images: an acme time-series write returned 200, a globex write of that same type returned 409, a globex GET of it returned 404, and an acme price write returned 200.

## Build and deploy

Gitea Actions builds the images. The runner is the host container `gitea/act_runner:0.2.12`, label `ubuntu-latest:docker://docker:27-cli`. It uses the host Docker socket because the k3s nodes have containerd only. A privileged Docker-in-Docker pod in a team namespace would be denied by Kyverno, so the runner is not a pod in `gitea` or in a team namespace. The job container is `docker:27-cli`, which has `sh` and not `bash`, so the workflows set `shell: sh`. The runner mounts the platform CA into the job and does not mount the Docker socket a second time. The job adds that CA to the Alpine trust store, because the Docker 27 client checks Harbor's certificate itself.

Workflows on `main` build only the tree that changed: `services/team-a`, `services/team-b`, `services/team-c`, or `services/controller`. They clone `main` from `https://gitea.lab.test` with the lab CA and push `:current`. Harbor credentials are Actions secrets, not files in Git.

| Image | Harbor name | Running digest when this record was written |
| --- | --- | --- |
| Time series | `harbor.lab.test/team-a/timeseries:current` | `sha256:05619ce66b1eabb503f565a5f6db4d9c0d04bd72df04d61c3f4b5d5e6dacff52` |
| Prices | `harbor.lab.test/team-b/prices:current` | `sha256:5a3990084ce34980c8546c70a7ad1159f5437d34c0566e0bb059edc9a1f5e140` |
| Customers | `harbor.lab.test/team-c/customers:current` | `sha256:c2fb2db494afbd6c8385f94e98d28b71dc2b0ab69f2504d3a4bedbae88446680` |
| Controller | `harbor.lab.test/lab/token-controller:current` | `sha256:991f06d5fd310c898376cf5e5aaad2ef821033fe4974869f64776da32cc0b2b9` |

Projects `team-a`, `team-b`, and `team-c` are private. Project `lab` is the public proxy project, which is where the controller image was pushed. Those four digests are the images the pods were running, and they are the images the workflows pushed.

Dockerfiles start `FROM harbor.lab.test/dockerhub/library/python:3.12-slim`. A pod cannot open world TCP 443 (`pods-no-world-https`), so the base image has to come through Harbor. The final stage is non-root, uid 65532. KrakenD is `docker.io/library/krakend:3.0.5`. containerd rewrites `docker.io` to the Harbor `dockerhub` project, so that pull does not go to Docker Hub either. The image's default user is root, and `runAsNonRoot` rejected it, so the gateway runs as uid 1000. Config version is 4. Version 3 is rejected by this KrakenD release.

The gateway, the API, and the controller are pinned to `k3s-agent`. Vault stays on `k3s-server`. The pre-existing sample pod is still on `k3s-server` and is not one of these APIs.

## What was deliberately not done

- No shared KrakenD. Domain grouping is the hostname suffix.
- TLS server certificates are not signing keys. There is no organisation issuer yet. `platform-ca` is the issuer, and the `Certificate` can point at another issuer later without a new token shape.
- Customers do not upload keys. Signing-key rotation was not run. The `signer-previous` hook is in the controller and unused.
- The lab objects are `3m` / `90s`, not the agreed `720h` / `168h`. The controller accepts both.
- The runner is a host container, not a pod in the `gitea` namespace.
- A human Vault login that a delivery team holds is not done. Operator userpass users exist and were not handed over. Delivery teams also have no Argo CD account and no Grafana view of expiry.
- No second API was added to an existing host. The services do not persist data across a restart.
- This record does not reopen Hubble relay TLS, envoy memory, Grafana or Harbor chart pins, or a second control plane.
