# ARCHITECTURE-RUNTIME.md — Live Verification

This document describes the actual runtime architecture and includes built-in verification to detect drift between documentation and deployed services.

## Current Architecture

**Base Host:** 192.168.2.180 (Single-host Docker Compose deployment)

### Service Topology

| Service | Port | Container | Health Check | Status |
|---------|------|-----------|--------------|--------|
| **router** | 4000 | router-proxy | GET /health | ? |
| **memgraphrag** | 8010 | memgraphrag-memgraphrag-1 | GET /health | ? |
| **monitoring** | 3000 | monitoring-grafana / monitoring-prometheus | GET / | ? |
| **gitlab** | 8929 | gitlab | GET /health | ? |
| **registry** | 5050 | (не подтверждено на этом хосте — возможно вне docker/на другом хосте) | GET /v2/ | ? |

*Status markers are updated by verify-runtime.sh. Каждый сервис — независимый
docker-compose стек в СВОЁМ репозитории (router/, monitoring/, MemGraphRAG/ —
см. .gitignore), а не один общий docker-compose.yml в корне этого репозитория,
как утверждалось ниже до 2026-08-06.*

## Component Descriptions

### 1. Router (nemohermes-router:latest)
- **Purpose:** Request routing, load balancing, request filtering
- **Port:** 4000 (HTTP)
- **Dependencies:** memgraphrag (for query routing)
- **Expected uptime:** 99.9%+
- **Logs:** `docker-compose logs router`

### 2. MemGraphRAG (memgraphrag:latest)
- **Purpose:** Memory graph storage and retrieval (RAG system)
- **Port:** 8010 (HTTP API)
- **Database:** MemGraph (in-memory)
- **Health Endpoint:** GET http://memgraphrag:8010/health
- **Expected uptime:** 99.9%+
- **Logs:** `docker-compose logs memgraphrag`

### 3. Monitoring (prometheus/grafana:latest)
- **Purpose:** Metrics collection and visualization
- **Port:** 3000 (Grafana), 9090 (Prometheus)
- **Scrapers:** docker-compose services
- **Expected uptime:** 95%+ (non-critical)
- **Logs:** `docker-compose logs monitoring`

### 4. GitLab / GitLab Runner
- **Purpose:** CI/CD orchestration, build jobs, artifact storage
- **Port:** 8929 (Web), 8988 (Runner API)
- **Volumes:** /var/gitlab/data (persistent)
- **Expected uptime:** 99%+
- **Logs:** `docker-compose logs gitlab`

### 5. Docker Registry
- **Purpose:** Private image repository (HTTP only, insecure)
- **Port:** 5050 (HTTP registry API)
- **Storage:** /var/registry/data (persistent)
- **SSL:** None (intentional — internal only)
- **Expected uptime:** 99%+
- **Logs:** `docker-compose logs registry`

## Drift Detection

### What is "drift"?
Drift occurs when:
1. **Documentation says X, but runtime runs Y** (e.g., docs say port 8000, reality is 8010)
2. **Service is down but docs list it as running**
3. **Dependencies changed (e.g., memgraphrag now in pod 192.168.2.181) but docs are stale**
4. **Configuration parameters changed** (e.g., memory limits increased)

### Automated Detection
Run the verification script to detect drift:

```bash
bash ci/verify-runtime.sh
```

**What it checks:**
- All services listed in docker-compose.yml are running
- All documented ports are accessible
- Health endpoints respond
- Service versions match documentation
- No unexpected services are running

**Output:**
- ✓ All checks pass → Architecture is in sync
- ✗ Checks fail → Drift detected, investigate with `docker-compose ps`

## Maintenance

### When Documentation Should Update
1. **Service Added:** Add to docker-compose.yml AND update this file
2. **Port Changed:** Update both the YAML and this document
3. **Service Removed:** Update both files
4. **Health Endpoint Changed:** Update health check column
5. **Dependency Changed:** Update Dependencies section

### Update Workflow
```bash
# 1. Update docker-compose.yml
vi docker-compose.yml

# 2. Run verification (should fail)
bash ci/verify-runtime.sh

# 3. Update this document to match reality
vi ARCHITECTURE-RUNTIME.md

# 4. Run verification again (should pass)
bash ci/verify-runtime.sh

# 5. Commit both changes together
git add docker-compose.yml ARCHITECTURE-RUNTIME.md
git commit -m "Update: service XYZ configuration"
```

## Compliance

- **SOC 2:** Documentation matches runtime (Drift Detection requirement)
- **OWASP:** All services documented for security scanning
- **CIS Benchmark:** Asset inventory maintained in this document
- **NIST:** Configuration baseline for audit trails

## References

- **ARCHITECTURE.md** — Conceptual design and rationale
- **docker-compose.yml** — Service definitions (source of truth)
- **ci/verify-runtime.sh** — Automated drift detection
- **ci/gates.yaml** — Quality gates (deployment requirements)

---

**Last Verified:** Run `bash ci/verify-runtime.sh` to check current status.

**Maintenance Owner:** DevOps team

**Drift Detection Enabled:** ✓ Yes (Run verify-runtime.sh in CI/CD pre-deploy)
