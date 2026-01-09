# Business Logic Metrics

> 비즈니스 작업 및 상태 추적 (9개 메트릭)

## 📋 목적

**운영 의사결정을 지원**하기 위한 비즈니스 지표입니다. 작업 성공률, 상태 전환, TTL 관리, GC 효율성을 추적합니다.

## 📊 메트릭 목록

### 1. Workspace 상태 (2개)

#### 1.1 Workspace Count by State

```python
codehub_workspace_count_by_state{phase="PENDING|RUNNING|STANDBY|ARCHIVED|ERROR"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | livesum |
| **레이블** | `phase` |
| **수집 위치** | `src/codehub/control/coordinator/metrics.py:57,62` |

**현재 분포**:
- `RUNNING`: 1개 (활성)
- `ARCHIVED`: 4개 (보관됨)
- `PENDING`: 0개
- `STANDBY`: 0개
- `ERROR`: 0개

**총 워크스페이스**: 5개

**분석**:
- ✅ **안정적인 상태 분포** (ERROR 없음)
- 80% 보관됨, 20% 활성 (정상 비율)

**PromQL 쿼리**:
```promql
# 총 워크스페이스 수
sum(codehub_workspace_count_by_state{phase!~"DELETED|DELETING"})

# 상태별 비율
sum by (phase) (codehub_workspace_count_by_state)
/
sum(codehub_workspace_count_by_state)
```

**평가**: ✅ **필수** - 상태별 분포 추적

---

#### 1.2 Workspace Count by Operation

```python
codehub_workspace_count_by_operation{operation="ARCHIVING|RESTORING|STARTING|STOPPING"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Gauge |
| **Multiprocess Mode** | livesum |
| **레이블** | `operation` |
| **수집 위치** | `src/codehub/control/coordinator/metrics.py:78,83` |

**의미**:
- 현재 진행 중인 작업별 워크스페이스 수

**PromQL 쿼리**:
```promql
# 작업 중인 총 워크스페이스 수
sum(codehub_workspace_count_by_operation)
```

**평가**: ✅ **유효** - 진행 중 작업 분포

---

### 2. 작업 성공률 (2개)

#### 2.1 Workspace Operations Total

```python
codehub_workspace_operations_total{operation="ARCHIVING|RESTORING|STARTING|STOPPING",status="success|failure|timeout"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `operation`, `status` |
| **수집 위치** | `src/codehub/control/coordinator/wc.py:484-487` |

**현재 작업 성공률**: **100%** (12/12 성공)
- `ARCHIVING success`: 4회
- `RESTORING success`: 3회
- `STARTING success`: 3회
- `STOPPING success`: 2회
- **실패 작업**: 0회 ✅

**PromQL 쿼리**:
```promql
# 전체 성공률
sum(rate(codehub_workspace_operations_total{status="success"}[5m]))
/
sum(rate(codehub_workspace_operations_total[5m]))
or vector(1)

# 작업별 성공률
sum by (operation) (rate(codehub_workspace_operations_total{status="success"}[5m]))
/
sum by (operation) (rate(codehub_workspace_operations_total[5m]))
```

**알림 조건**:
```promql
# 성공률 < 95%이면 경고
sum(rate(codehub_workspace_operations_total{status="success"}[1h]))
/
sum(rate(codehub_workspace_operations_total[1h]))
< 0.95
```

**평가**: ✅ **필수** - 서비스 품질 지표

---

#### 2.2 Workspace State Transitions Total

```python
codehub_workspace_state_transitions_total{from_state="...",to_state="..."}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `from_state`, `to_state` |
| **수집 위치** | `src/codehub/control/coordinator/wc.py:462-465` |

**현재 전환 패턴**:
- `STANDBY → ARCHIVED`: 4회
- `ARCHIVED → STANDBY`: 3회
- `STANDBY → RUNNING`: 3회
- `RUNNING → STANDBY`: 2회

**분석**:
- ✅ **논리적 흐름 확인됨** (역전환 없음)
- ✅ 정상적인 생명주기 (RUNNING ↔ STANDBY ↔ ARCHIVED)

**PromQL 쿼리**:
```promql
# 시간별 전환 추적
sum by (from_state, to_state) (rate(codehub_workspace_state_transitions_total[5m]))
```

**평가**: ✅ **유효** - 상태 머신 분석

---

### 3. TTL 관리 (1개) ✨ 새로 추가

#### 3.1 Workspace TTL Expiry Total

```python
codehub_workspace_ttl_expiry_total{ttl_type="standby|archive"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `ttl_type` |
| **현재 값** | 0 (이벤트 없음) |
| **수집 위치** | `src/codehub/control/coordinator/ttl.py:168,205` |

**의미**:
- `standby`: RUNNING → STANDBY TTL 만료 횟수
- `archive`: STANDBY → ARCHIVED TTL 만료 횟수

**타당성**:
- ✅ **비용 관리**: TTL 만료는 유휴 리소스 정리의 핵심
- ✅ **SLA 측정**: TTL 정책이 제대로 동작하는지 확인
- ✅ **용량 계획**: 만료율을 보고 리소스 회전율 예측

**PromQL 쿼리**:
```promql
# 시간당 TTL 만료 건수
sum by (ttl_type) (rate(codehub_workspace_ttl_expiry_total[1h]))

# standby vs archive 비율
sum(codehub_workspace_ttl_expiry_total{ttl_type="standby"})
/
sum(codehub_workspace_ttl_expiry_total)
```

**대시보드 활용**:
- **Time Series**: 시간별 만료 추세
- **Bar Chart**: ttl_type별 누적 건수
- **Stat**: 최근 1시간 만료 건수

**구현 위치**:
```python
# src/codehub/control/coordinator/ttl.py:168
if updated_ids:
    logger.info("[%s] standby_ttl expired for %d workspaces", self.name, len(updated_ids))
    WORKSPACE_TTL_EXPIRY.labels(ttl_type="standby").inc(len(updated_ids))

# src/codehub/control/coordinator/ttl.py:205
if updated_ids:
    logger.info("[%s] archive_ttl expired for %d workspaces", self.name, len(updated_ids))
    WORKSPACE_TTL_EXPIRY.labels(ttl_type="archive").inc(len(updated_ids))
```

**평가**: ✅ **필수** - 비용 관리의 핵심 지표

---

### 4. 리소스 정리 (1개) ✨ 새로 추가

#### 4.1 Coordinator GC Orphans Deleted Total

```python
codehub_coordinator_gc_orphans_deleted_total{resource_type="archive|container|volume"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `resource_type` |
| **현재 값** | 0 (고아 리소스 없음) |
| **수집 위치** | `src/codehub/control/coordinator/gc.py:104,133,141` |

**의미**:
- GC가 삭제한 고아 리소스 수 (리소스 타입별)
- 고아 리소스 = DB에는 없지만 실제 시스템에 남아있는 리소스

**타당성**:
- ✅ **데이터 무결성**: 고아 리소스 발생은 버그의 징후
- ✅ **비용 누수 방지**: 고아 리소스는 불필요한 비용
- ✅ **GC 효율성**: 삭제 빈도를 보고 GC 주기 조정

**PromQL 쿼리**:
```promql
# 시간당 GC 삭제 건수
sum by (resource_type) (rate(codehub_coordinator_gc_orphans_deleted_total[1h]))

# 누적 삭제 건수
sum by (resource_type) (codehub_coordinator_gc_orphans_deleted_total)
```

**대시보드 활용**:
- **Bar Chart**: resource_type별 누적 삭제 건수
- **Time Series**: 시간별 삭제 추세 (고아 발생 패턴 파악)

**구현 위치**:
```python
# Archive 고아 삭제 - src/codehub/control/coordinator/gc.py:104
deleted = await self._delete_archives(orphans)
logger.info("[%s] Deleted %d/%d orphan archives", self.name, deleted, len(orphans))
COORDINATOR_GC_ORPHANS_DELETED.labels(resource_type="archive").inc(deleted)

# Container 고아 삭제 - src/codehub/control/coordinator/gc.py:133
await self._ic.delete(ws_id)
COORDINATOR_GC_ORPHANS_DELETED.labels(resource_type="container").inc()

# Volume 고아 삭제 - src/codehub/control/coordinator/gc.py:141
await self._storage.delete_volume(ws_id)
COORDINATOR_GC_ORPHANS_DELETED.labels(resource_type="volume").inc()
```

**알림 조건**:
```promql
# GC 삭제 건수 > 10/시간이면 버그 의심
sum(rate(codehub_coordinator_gc_orphans_deleted_total[1h])) > 10
```

**평가**: ✅ **필수** - 데이터 무결성 지표

---

### 5. 오류 추적 (3개) - 정상 상황으로 값 없음

#### 5.1 Coordinator Observer API Errors Total

```python
codehub_coordinator_observer_api_errors_total{resource_type="volumes|containers|archives",error_type="timeout|exception"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `resource_type`, `error_type` |
| **현재 값** | 0 (API 오류 없음) |
| **수집 위치** | `src/codehub/control/coordinator/observer.py:58-60,68-70` |

**의미**:
- Observer가 Docker API 호출 시 발생한 오류 횟수

**현재 상태**: ✅ **정상** - API 호출 모두 성공

**PromQL 쿼리**:
```promql
# 오류율
sum by (resource_type, error_type) (rate(codehub_coordinator_observer_api_errors_total[5m]))
```

**평가**: ✅ **유효** - 오류 발생 시 자동 기록

---

#### 5.2 Circuit Breaker Failures Total

```python
codehub_circuit_breaker_failures_total{name="external"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `name` |
| **현재 값** | 0 (CB 실패 없음) |
| **수집 위치** | `src/codehub/core/circuit_breaker.py:164` |

**의미**:
- Circuit Breaker가 OPEN 상태로 전환된 횟수

**현재 상태**: ✅ **정상** - CB OPEN 없음

**평가**: ✅ **유효** - 외부 서비스 오류 추적

---

#### 5.3 Circuit Breaker Rejections Total

```python
codehub_circuit_breaker_rejections_total{name="external"}
```

| 속성 | 값 |
|------|-----|
| **타입** | Counter |
| **레이블** | `name` |
| **현재 값** | 0 (CB 거부 없음) |
| **수집 위치** | `src/codehub/core/circuit_breaker.py:114` |

**의미**:
- Circuit Breaker가 OPEN 상태일 때 거부한 요청 수

**현재 상태**: ✅ **정상** - CB OPEN 상태 아님

**평가**: ✅ **유효** - Circuit Breaker 동작 추적

---

## 📈 대시보드 활용

### 1. Workspace State Distribution (Pie Chart)

```json
{
  "title": "Workspace State Distribution",
  "targets": [
    {
      "expr": "sum by (phase) (codehub_workspace_count_by_state{phase!~\"DELETED|DELETING\"})",
      "legendFormat": "{{phase}}"
    }
  ],
  "pieChartType": "pie"
}
```

### 2. Operation Success Rate (Gauge)

```json
{
  "title": "Operation Success Rate (Last 1h)",
  "targets": [
    {
      "expr": "sum(rate(codehub_workspace_operations_total{status=\"success\"}[1h])) / sum(rate(codehub_workspace_operations_total[1h])) or vector(1)",
      "legendFormat": "Success Rate"
    }
  ],
  "thresholds": {
    "steps": [
      { "value": 0.90, "color": "red" },
      { "value": 0.95, "color": "yellow" },
      { "value": 0.99, "color": "green" }
    ]
  }
}
```

### 3. TTL Expiry Rate (Time Series) ✨

```json
{
  "title": "TTL Expiry Rate",
  "targets": [
    {
      "expr": "sum by (ttl_type) (rate(codehub_workspace_ttl_expiry_total[5m]))",
      "legendFormat": "{{ttl_type}}"
    }
  ],
  "yAxisLabel": "Expirations per second"
}
```

### 4. GC Orphan Deletion (Bar Chart) ✨

```json
{
  "title": "GC Orphan Deletion (Total)",
  "targets": [
    {
      "expr": "sum by (resource_type) (codehub_coordinator_gc_orphans_deleted_total)",
      "legendFormat": "{{resource_type}}"
    }
  ],
  "type": "bargauge"
}
```

### 5. State Transitions (Time Series)

```json
{
  "title": "State Transitions",
  "targets": [
    {
      "expr": "sum by (from_state, to_state) (rate(codehub_workspace_state_transitions_total[5m]))",
      "legendFormat": "{{from_state}} → {{to_state}}"
    }
  ]
}
```

---

## 🚨 알림 규칙

### Business Logic Alerts

```yaml
groups:
  - name: codehub_business
    interval: 1m
    rules:
      # Operation Success Rate Low
      - alert: OperationSuccessRateLow
        expr: |
          sum(rate(codehub_workspace_operations_total{status="success"}[1h]))
          /
          sum(rate(codehub_workspace_operations_total[1h]))
          < 0.95
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Operation success rate < 95%"
          description: "Success rate: {{ $value | humanizePercentage }}"

      # Too Many ERROR Workspaces
      - alert: TooManyErrorWorkspaces
        expr: codehub_workspace_count_by_state{phase="ERROR"} > 0
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "ERROR state workspaces detected"
          description: "Count: {{ $value }}"

      # GC Orphans Detected
      - alert: GCOrphansDetected
        expr: sum(rate(codehub_coordinator_gc_orphans_deleted_total[1h])) > 10
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "GC deleting too many orphans"
          description: "Possible bug - orphans/hour: {{ $value }}"
```

---

## 📊 운영 인사이트

### 비용 관리

**TTL 만료율로 리소스 회전율 예측**:
```promql
# 일일 TTL 만료 예상 건수
sum(rate(codehub_workspace_ttl_expiry_total[1h])) * 24
```

**활용**:
- Standby TTL: 4시간 → 하루 6번 회전
- Archive TTL: 7일 → 주 1회 회전
- 비용 절감: 유휴 워크스페이스 자동 정리

### 데이터 무결성

**고아 리소스 발생 패턴 분석**:
```promql
# 리소스 타입별 고아 발생률
sum by (resource_type) (rate(codehub_coordinator_gc_orphans_deleted_total[1d]))
```

**활용**:
- Container 고아 > 0: Workspace 삭제 로직 버그
- Volume 고아 > 0: Storage 정리 로직 버그
- Archive 고아 > 0: S3 동기화 버그

### SLA 측정

**작업 성공률 추이**:
```promql
# 7일 평균 성공률
avg_over_time((
  sum(rate(codehub_workspace_operations_total{status="success"}[1h]))
  /
  sum(rate(codehub_workspace_operations_total[1h]))
)[7d:1h])
```

**SLA 목표**:
- 작업 성공률: > 99%
- ERROR 상태: 0개
- 고아 리소스: < 1개/일

---

## 📊 현재 비즈니스 로직 요약

| 메트릭 | 현재 값 | 상태 | 비고 |
|--------|---------|------|------|
| Workspace Count | RUNNING:1, ARCHIVED:4 | ✅ 정상 | 안정적 분포 |
| Operation Success Rate | 100% (12/12) | ✅ 우수 | 실패 0 |
| State Transitions | 4가지 전환 | ✅ 정상 | 논리적 흐름 |
| TTL Expiry | 0 | ✅ 정상 | 이벤트 없음 |
| GC Orphans Deleted | 0 | ✅ 정상 | 고아 없음 |
| Observer API Errors | 0 | ✅ 정상 | 오류 없음 |
| Circuit Breaker Failures | 0 | ✅ 정상 | CB OPEN 없음 |
| Circuit Breaker Rejections | 0 | ✅ 정상 | 거부 없음 |

**종합 평가**: ✅ **9/9 메트릭 정상** (작업 성공률 100%, 고아 리소스 0)

---

## 🔗 관련 문서

- [TTL Manager Architecture](../architecture_v2/ttl-manager.md)
- [Garbage Collector Design](../architecture_v2/garbage-collector.md)
- [Workspace Controller](../architecture_v2/workspace-controller.md)
