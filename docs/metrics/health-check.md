# Health Check Metrics

> 시스템 가용성 및 상태 모니터링 (7개 메트릭)

## 📋 목적

시스템이 **정상 동작하는지 즉시 확인**하기 위한 메트릭입니다. 대시보드 최상단에 위치하여 Red/Green 상태로 표시됩니다.

## 📊 메트릭 목록

### 1. 시스템 가용성 (3개)

#### 1.1 Database Connection Status

```python
codehub_db_up
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | max |
| **현재 값** | 1.0 (UP) |
| **레이블** | 없음 |
| **수집 위치** | `src/codehub/control/coordinator/metrics.py:94` |

**의미**:
- `1.0`: DB 연결됨 (정상)
- `0.0`: DB 단절 (시스템 전체 중단)

**알림 조건**:
```promql
codehub_db_up == 0
```

**평가**: ✅ **필수** - 시스템 전체 헬스의 핵심 지표

---

#### 1.2 Circuit Breaker State

```python
codehub_circuit_breaker_state{name="external"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | max |
| **현재 값** | 0.0 (CLOSED) |
| **레이블** | `name` (external) |
| **수집 위치** | `src/codehub/core/circuit_breaker.py:81,138,154,172,182` |

**의미**:
- `0.0`: CLOSED (정상 - 외부 서비스 정상)
- `1.0`: HALF_OPEN (회복 중 - 테스트 요청 진행)
- `2.0`: OPEN (차단 - 외부 서비스 오류)

**알림 조건**:
```promql
codehub_circuit_breaker_state{name="external"} == 2
```

**평가**: ✅ **필수** - Docker/S3 외부 서비스 보호 상태

---

#### 1.3 Active WebSocket Connections

```python
codehub_ws_active_connections
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | livesum |
| **현재 값** | 0.0 |
| **레이블** | 없음 |
| **수집 위치** | `src/codehub/app/proxy/transport.py:167,187` |

**의미**:
- 현재 활성 사용자 WebSocket 세션 수
- 모든 워커의 연결 합계

**평가**: ✅ **유효** - 활성 사용자 수 추적

---

### 2. 리더십 상태 (1개)

#### 2.1 Coordinator Leader Status

```python
codehub_coordinator_leader_status{coordinator_type="wc|observer|ttl|gc|metrics"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | max |
| **현재 값** | 5/5 (모두 리더) |
| **레이블** | `coordinator_type` |
| **수집 위치** | `src/codehub/control/coordinator/base.py:169-171` |

**의미**:
- `1.0`: 해당 코디네이터가 리더 역할 수행 중
- `0.0`: 팔로워 (리더 선출 실패)

**5개 코디네이터**:
1. `wc`: Workspace Controller
2. `observer`: Observer (리소스 감시)
3. `ttl`: TTL Manager
4. `gc`: Garbage Collector
5. `metrics`: Metrics Collector

**집계 쿼리**:
```promql
# 리더 수 합계 (정상: 5/5)
sum(codehub_coordinator_leader_status)
```

**알림 조건**:
```promql
sum(codehub_coordinator_leader_status) < 5
```

**평가**: ✅ **필수** - 코디네이터 리더십 확인

---

### 3. 리소스 상태 (3개)

#### 3.1 Database Pool - Checked In (유휴 연결)

```python
codehub_db_pool_checkedin
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | livesum |
| **현재 값** | 0.0 ⚠️ |
| **레이블** | 없음 |
| **수집 위치** | `src/codehub/control/coordinator/metrics.py:95` |

**의미**:
- Pool에서 대기 중인 유휴 연결 수 (모든 워커 합계)
- 0 = 여유 연결 없음 (모두 사용 중)

**평가**: ⚠️ **경고** - 현재 유휴 연결 없음 (Pool 크기 증가 권장)

---

#### 3.2 Database Pool - Checked Out (사용 중 연결)

```python
codehub_db_pool_checkedout
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | livesum |
| **현재 값** | 6.0 |
| **레이블** | 없음 |
| **수집 위치** | `src/codehub/control/coordinator/metrics.py:96` |

**의미**:
- 현재 사용 중인 DB 연결 수 (모든 워커 합계)

**평가**: ✅ **유효** - 사용 중 연결 추적

---

#### 3.3 Database Pool - Overflow (오버플로우 연결)

```python
codehub_db_pool_overflow
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | livesum |
| **현재 값** | 0.0 |
| **레이블** | 없음 |
| **수집 위치** | `src/codehub/control/coordinator/metrics.py:98` |

**의미**:
- Pool 크기를 초과하여 생성된 임시 연결 수
- 0 = 정상 (오버플로우 없음)

**평가**: ✅ **정상** - 오버플로우 발생 없음

---

## 📈 대시보드 활용

### 1. System Status Panel (Stat)

```json
{
  "title": "System Status",
  "targets": [
    {
      "expr": "codehub_db_up",
      "legendFormat": "DB Status"
    },
    {
      "expr": "sum(codehub_coordinator_leader_status)",
      "legendFormat": "Leader Count"
    },
    {
      "expr": "codehub_circuit_breaker_state{name=\"external\"}",
      "legendFormat": "Circuit Breaker"
    }
  ],
  "thresholds": {
    "mode": "absolute",
    "steps": [
      { "value": 0, "color": "red" },
      { "value": 1, "color": "green" }
    ]
  }
}
```

### 2. DB Pool Usage (Gauge)

```promql
# Pool 사용률 계산
codehub_db_pool_checkedout
/
(codehub_db_pool_checkedout + codehub_db_pool_checkedin)
```

**Threshold**:
- `< 80%`: Green
- `80-90%`: Yellow
- `> 90%`: Red

### 3. Active Connections (Stat with Sparkline)

```promql
codehub_ws_active_connections
```

---

## 🚨 알림 규칙 (Prometheus AlertManager)

### Critical Alerts

```yaml
groups:
  - name: codehub_critical
    interval: 30s
    rules:
      # DB Down
      - alert: DatabaseDown
        expr: codehub_db_up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Database connection lost"
          description: "DB UP metric is 0 for more than 1 minute"

      # Circuit Breaker Open
      - alert: CircuitBreakerOpen
        expr: codehub_circuit_breaker_state{name="external"} == 2
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Circuit Breaker is OPEN"
          description: "External services (Docker/S3) are unavailable"

      # Coordinator Leader Missing
      - alert: CoordinatorLeaderMissing
        expr: sum(codehub_coordinator_leader_status) < 5
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Some coordinators have no leader"
          description: "Expected 5 leaders, got {{ $value }}"
```

### Warning Alerts

```yaml
      # DB Pool Usage High
      - alert: DBPoolUsageHigh
        expr: |
          codehub_db_pool_checkedout
          /
          (codehub_db_pool_checkedout + codehub_db_pool_checkedin)
          > 0.9
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "DB Pool usage above 90%"
          description: "Consider increasing pool_size"
```

---

## 🔍 Troubleshooting

### DB UP = 0

**원인**:
1. PostgreSQL 서비스 중단
2. 네트워크 문제
3. Connection pool 고갈

**조치**:
```bash
# 1. PostgreSQL 상태 확인
docker compose ps db

# 2. DB 로그 확인
docker compose logs db

# 3. 연결 테스트
psql -h localhost -U codehub -d codehub
```

### Leader Count < 5

**원인**:
1. Redis 연결 문제 (Leader Election 실패)
2. Coordinator 프로세스 중단
3. DB 연결 문제

**조치**:
```bash
# 1. Redis 상태 확인
docker compose ps redis

# 2. Control-plane 로그 확인
docker compose logs control-plane | grep -i "leader"

# 3. 재시작
docker compose restart control-plane
```

### Circuit Breaker OPEN

**원인**:
1. Docker API 오류
2. S3 연결 문제
3. 외부 서비스 과부하

**조치**:
```bash
# 1. Docker 상태 확인
docker ps

# 2. S3 연결 테스트 (MinIO)
docker compose ps minio

# 3. Circuit Breaker 로그 확인
docker compose logs control-plane | grep -i "circuit"
```

---

## 📊 현재 상태 요약

| 메트릭 | 현재 값 | 상태 | 비고 |
|--------|---------|------|------|
| DB UP | 1.0 | ✅ 정상 | - |
| Circuit Breaker | 0.0 (CLOSED) | ✅ 정상 | - |
| Active WS Connections | 0.0 | ✅ 정상 | 유휴 상태 |
| Leader Count | 5/5 | ✅ 정상 | 모든 코디네이터 리더 |
| DB Pool Checked In | 0.0 | ⚠️ 경고 | **Pool 크기 증가 권장** |
| DB Pool Checked Out | 6.0 | ✅ 정상 | - |
| DB Pool Overflow | 0.0 | ✅ 정상 | - |

**종합 평가**: ✅ **7/7 메트릭 수집 중** (1개 경고 - DB Pool 사용률 100%)
