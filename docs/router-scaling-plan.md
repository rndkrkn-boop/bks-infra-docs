# Router Scaling Plan

## Current State
- **Single instance:** router:4000 on 192.168.2.180
- **Load capacity:** ~500 req/sec (measured)
- **Bottleneck:** CPU-bound (single core)
- **Expected growth:** 10x in 2026

## Scaling Strategy

### Phase 1: Horizontal Scaling (Q3 2026)
**Objective:** Support 5,000 req/sec

**Architecture:**
```
                         HAProxy Load Balancer (192.168.2.190:4000)
                              ↓
         ┌─────────────────────┼─────────────────────┐
         ↓                     ↓                     ↓
    router-1:4001        router-2:4001        router-3:4001
    (192.168.2.180)       (192.168.2.181)       (192.168.2.182)
```

**Components:**
1. **HAProxy Load Balancer**
   - Health checks every 5 seconds
   - Sticky sessions for stateful requests
   - Auto-failover (remove unhealthy backends)
   
2. **Multiple Router Instances**
   - Each router in separate container
   - Shared MemGraphRAG backend (8010)
   - Independent connection pools

**Implementation:**
```bash
# 1. Deploy HAProxy
docker run -d \
  --name haproxy \
  --net host \
  -v /etc/haproxy/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg \
  haproxy:2.8

# 2. Scale router containers
docker-compose up -d --scale router=3
```

### Phase 2: Connection Pooling (Q4 2026)
**Objective:** Reduce connection overhead

**Implementation:**
- pgbouncer for database connection pooling (5→50 concurrent)
- redis-benchmark to test throughput
- Connection pool size tuning based on load

### Phase 3: Caching Layer (Q1 2027)
**Objective:** Reduce backend load

**Implementation:**
- Redis cache for frequent queries
- Cache invalidation strategy (TTL + event-based)
- Cache hit rate monitoring

## Monitoring

### Metrics to Track
- Requests per second (RPS)
- P50/P95/P99 latency
- Error rate (4xx, 5xx)
- CPU/Memory usage per instance
- Connection pool saturation

### Dashboards
```
Grafana (monitoring:3000)
  └─ Router Scaling
      ├─ RPS trend (target: linear growth to 5,000)
      ├─ Latency distribution
      ├─ Error rate
      └─ Instance health (3 backends)
```

## Rollout Plan

| Date | Phase | Action | Success Criteria |
|------|-------|--------|------------------|
| Q3 2026 | HAProxy setup | Deploy HAProxy load balancer | 99.99% availability, failover < 5s |
| Q3 2026 | Router scale | Deploy 3 router instances | RPS capacity 5,000, latency < 100ms P95 |
| Q4 2026 | Connection pooling | Setup pgbouncer | Connection overhead -40% |
| Q1 2027 | Caching | Deploy Redis layer | Cache hit rate > 60%, latency -30% |

## Estimated Costs
- Additional server: $200-300/month (for router-2, router-3)
- HAProxy overhead: negligible
- Redis instance: $100/month (Q1 2027)

**Total:** ~$500-600/month for 10x capacity increase

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| HAProxy single point of failure | CRITICAL | Setup active-active HAProxy pair (keepalived) |
| State loss in failover | HIGH | Session replication to Redis |
| Cache coherency issues | MEDIUM | Event-based invalidation + TTL |
| Network partition | MEDIUM | Circuit breaker pattern in router |

## Success Metrics
- ✓ Support 5,000+ req/sec sustainably
- ✓ P99 latency < 200ms
- ✓ 99.95%+ availability (max 2.2 hours downtime/month)
- ✓ Auto-failover works correctly
