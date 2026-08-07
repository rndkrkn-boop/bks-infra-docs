# Deployment Least-Privilege Principles

> [!CAUTION]
> **Устарело.** Документ целиком описывает Kubernetes RBAC-контур
> (`kubectl`, `k8s/rbac-roles.yaml`, namespaces `nemohermes-prod/staging/dev`).
> K3s декомиссирован 2026-07-06 (см. [`ARCHITECTURE.md` §2.5](../ARCHITECTURE.md)) —
> производство сейчас Docker Compose + OpenShell, без Kubernetes и без RBAC
> в этом виде. Принцип наименьших привилегий по-прежнему актуален, но его
> текущее воплощение — раздельные GitLab CI/CD Variables per-project и
> `gb10-shell` как единственный раннер с правом деплоя (см. `ARCHITECTURE.md`
> §0), не манифесты ниже. Оставлено как историческая справка, не как
> инструкция к действию.

## Problem (Bug gb10-shell)
Historically, deployment automation ran with excessive privileges:
- Shell access to production hosts
- Ability to modify RBAC/network policies
- Root access to docker daemon
- No privilege separation between services

Result: **One compromised CI secret = Full cluster compromise**

## Solution: Least-Privilege Deployment Model

### Principle 1: Role-Based Access Control (RBAC)

**Current (DANGEROUS):**
```bash
# ❌ BAD: Uses cluster-admin equivalent
kubectl apply -f deployment.yaml --as=system:admin
```

**Fixed (SAFE):**
```bash
# ✅ GOOD: Limited deployment-only service account
kubectl apply -f deployment.yaml --as=system:serviceaccount:default:ci-deployer
```

**See:** `k8s/rbac-roles.yaml` for deployment-deployer role definition

### Principle 2: Capability Separation

| What | Who | Via | Capability | Access |
|------|-----|-----|------------|--------|
| Build images | CI Runner | docker-in-docker | Build only | ECR/Registry write |
| Deploy code | CI Deployer | kubectl | Deploy only | Deployment/StatefulSet write |
| Scale services | Ops | kubectl | Scale only | Deployment replicas |
| Admin | SRE | kubectl | Cluster-admin | Everything |

**Never combine capabilities:**
- ❌ Build + Deploy in same service account (if build compromised, deployment is too)
- ❌ Deploy + Admin in same service account (if deploy compromised, cluster is too)
- ❌ Build + Shell access (if build fails, can't SSH into prod)

### Principle 3: Secret Minimization

**Current state (bad):**
```
AWS_ACCESS_KEY_ID=AKIA...  (full AWS account access)
DOCKER_PASSWORD=...         (push to any registry)
KUBECTL_TOKEN=...           (full cluster control)
```

**Fixed state (good):**
```
# Build job gets ONLY build credentials
ECR_PUSH_ROLE=arn:aws:iam::...role/build-ecr-push

# Deploy job gets ONLY deploy credentials
KUBECTL_DEPLOY_SA=ci-deployer

# Each service account has MINIMAL permissions
```

### Principle 4: Audit & Immutability

Every deployment action is logged:
```yaml
kubectl logs -l app=deployment-audit --all-namespaces
```

Logs include:
- Who deployed (service account)
- What changed (deployment manifests diff)
- When it happened (timestamp)
- Whether it succeeded/failed

**Immutability:**
- Production deployments cannot be rolled back without audit trail
- Configuration changes require approval

### Principle 5: Namespace Isolation

Separate namespaces for separate trust boundaries:
```
kube-system              ← Core Kubernetes (only SRE)
nemohermes-prod        ← Production services (ci-deployer only)
nemohermes-staging     ← Staging (ci-deployer, can experiment)
nemohermes-dev         ← Development (anyone)
```

**Service accounts are namespace-scoped:**
```yaml
# ci-deployer in prod namespace CAN'T see staging secrets
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ci-deployer
  namespace: nemohermes-prod  ← LIMITED TO THIS NAMESPACE
```

### Principle 6: Prevent Privilege Escalation

Explicit denials in RBAC:
```yaml
# ❌ BLOCK: ci-deployer cannot modify RBAC
- apiGroups: ["rbac.authorization.k8s.io"]
  resources: ["clusterroles", "clusterrolebindings"]
  verbs: ["*"]

# ❌ BLOCK: ci-deployer cannot reach cluster-admin
- apiGroups: [""]
  resources: ["secrets"]
  resourceNames: ["admin-token"]
  verbs: ["*"]
```

## Implementation Checklist

- [ ] Create `ci-deployer` service account in `k8s/rbac-roles.yaml`
- [ ] Apply RBAC roles to cluster: `kubectl apply -f k8s/rbac-roles.yaml`
- [ ] Update CI/CD to use service account token instead of kubeconfig
- [ ] Remove shell access from CI runners
- [ ] Audit current permissions: `kubectl describe rolebinding -A`
- [ ] Test deployment with new service account
- [ ] Document privilege changes in team runbooks
- [ ] Monitor audit logs for any privilege escalation attempts

## Verification

```bash
# Verify ci-deployer can deploy
kubectl auth can-i create deployments --as=system:serviceaccount:default:ci-deployer
# Expected: yes

# Verify ci-deployer CANNOT modify RBAC
kubectl auth can-i create clusterroles --as=system:serviceaccount:default:ci-deployer
# Expected: no

# Verify ci-deployer CANNOT read production secrets
kubectl auth can-i get secrets --namespace=nemohermes-prod --as=system:serviceaccount:default:ci-deployer --resource-name=db-password
# Expected: no
```

## References
- Kubernetes RBAC documentation: https://kubernetes.io/docs/reference/access-authn-authz/rbac/
- Pod Security Standards: https://kubernetes.io/docs/concepts/security/pod-security-standards/
- OWASP: Principle of Least Privilege
- CIS Kubernetes Benchmark (Control 5.1.x)
