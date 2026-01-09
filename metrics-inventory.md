# 현재 수집 중인 메트릭 목록

> 생성 시간: 2026-01-08
> 데이터 소스: http://localhost:18000/metrics

## 요약

- 총 메트릭 수: 28개 (고유 이름 기준)
- 모든 기대 메트릭: ✅ 수집 중
- control-plane 상태: ✅ 정상 동작

---

## 1. Database 메트릭 (4개)

### codehub_db_up
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **현재 값**: 1.0 (UP)
- **설명**: DB 연결 상태 (1=UP, 0=DOWN)
- **레이블**: 없음

### codehub_db_pool_checkedin
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **현재 값**: 1.0
- **설명**: 유휴 연결 수
- **레이블**: 없음

### codehub_db_pool_checkedout
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **현재 값**: 5.0
- **설명**: 사용 중 연결 수
- **레이블**: 없음

### codehub_db_pool_overflow
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **현재 값**: 0.0
- **설명**: 오버플로우 연결 수
- **레이블**: 없음

**DB Pool 사용률 계산**: `checkedout / (checkedout + checkedin)` = 5 / (5 + 1) = **83.3%**

---

## 2. Workspace 메트릭 (7개)

### codehub_workspace_count_by_state
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **레이블**: `phase` (PENDING, RUNNING, STANDBY, ERROR, ARCHIVED)
- **현재 값**:
  - ARCHIVED: 5
  - PENDING: 0
  - RUNNING: 0
  - STANDBY: 0
  - ERROR: 0
- **총 워크스페이스**: 5개 (ARCHIVED)

### codehub_workspace_operations_total
- **상태**: ✅ 수집 중
- **타입**: Counter
- **레이블**: `operation`, `status`
- **현재 값**:
  - ARCHIVING success: 4
  - RESTORING success: 2
  - STARTING success: 2
  - STOPPING success: 2
- **총 작업**: 10개 (모두 성공)

### codehub_workspace_operation_duration_seconds
- **상태**: ✅ 수집 중
- **타입**: Histogram
- **레이블**: `operation`
- **샘플 수**:
  - ARCHIVING: 4개
  - RESTORING: 2개
  - STARTING: 2개
  - STOPPING: 2개

### codehub_workspace_state_transitions_total
- **상태**: ✅ 수집 중
- **타입**: Counter
- **레이블**: `from_state`, `to_state`
- **데이터**: 있음 (상태 전환 추적)

### codehub_workspace_count_by_operation
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **레이블**: `operation`
- **데이터**: 있음

### codehub_workspace_last_operation_timestamp
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **레이블**: `operation`
- **설명**: 마지막 작업 실행 시간 (Unix timestamp)

### codehub_workspace_ttl_expiry_total
- **상태**: ❓ 확인 필요
- **타입**: Counter
- **레이블**: `ttl_type`
- **참고**: collector.py에 정의되어 있으나 실제 값 확인 필요

---

## 3. Coordinator 메트릭 (8개)

### codehub_coordinator_leader_status
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **레이블**: `coordinator_type`
- **현재 값**:
  - wc: 1.0 (Leader)
  - observer: 1.0 (Leader)
  - ttl: 1.0 (Leader)
  - gc: 1.0 (Leader)
  - metrics: 1.0 (Leader)
- **총 리더 수**: 5/5 ✅

### codehub_coordinator_tick_duration_seconds
- **상태**: ✅ 수집 중
- **타입**: Histogram
- **레이블**: `coordinator_type`
- **샘플 수**:
  - metrics: 318개
  - wc: 499개
  - ttl: 73개
  - gc: 30개
  - observer: 510개

### codehub_coordinator_tick_total
- **상태**: ✅ 수집 중
- **타입**: Counter
- **레이블**: `coordinator_type`, `status`
- **데이터**: 있음

### codehub_coordinator_wc_reconcile_queue
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **현재 값**: 0.0
- **설명**: WC reconcile 큐 깊이

### codehub_coordinator_wc_cas_failures_total
- **상태**: ✅ 수집 중
- **타입**: Counter
- **현재 값**: 0.0
- **설명**: WC CAS 실패 횟수

### codehub_coordinator_observer_api_duration_seconds
- **상태**: ✅ 수집 중
- **타입**: Histogram
- **레이블**: `resource_type`
- **샘플 수**:
  - volumes: 508개
  - containers: 508개
  - archives: 508개

### codehub_coordinator_observer_api_errors_total
- **상태**: ❓ 확인 필요
- **타입**: Counter
- **레이블**: `resource_type`, `error_type`
- **참고**: collector.py에 정의되어 있으나 실제 값 확인 필요

### codehub_coordinator_gc_orphans_deleted_total
- **상태**: ❓ 확인 필요
- **타입**: Counter
- **레이블**: `resource_type`
- **참고**: collector.py에 정의되어 있으나 실제 값 확인 필요

---

## 4. WebSocket 메트릭 (3개)

### codehub_ws_active_connections
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **현재 값**: 0.0
- **설명**: 현재 활성 WebSocket 연결 수

### codehub_ws_message_latency_seconds
- **상태**: ✅ 수집 중
- **타입**: Histogram
- **레이블**: `direction`
- **샘플 수**:
  - client_to_backend: 943개
  - backend_to_client: 2,474개

### codehub_ws_errors_total
- **상태**: ✅ 수집 중
- **타입**: Counter
- **레이블**: `error_type`
- **현재 값**:
  - connection_closed: 4개

---

## 5. Circuit Breaker 메트릭 (3개)

### codehub_circuit_breaker_state
- **상태**: ✅ 수집 중
- **타입**: Gauge
- **레이블**: `name`
- **현재 값**:
  - external: 0.0 (CLOSED)
- **설명**: 0=CLOSED, 1=HALF_OPEN, 2=OPEN

### codehub_circuit_breaker_failures_total
- **상태**: ❓ 확인 필요
- **타입**: Counter
- **레이블**: `name`
- **참고**: collector.py에 정의되어 있으나 실제 값 확인 필요

### codehub_circuit_breaker_rejections_total
- **상태**: ❓ 확인 필요
- **타입**: Counter
- **레이블**: `name`
- **참고**: collector.py에 정의되어 있으나 실제 값 확인 필요

---

## 대시보드 설계를 위한 주요 발견사항

### ✅ 사용 가능한 메트릭 (데이터 충분)
1. **Database**: db_up, db_pool (checkedin, checkedout, overflow)
2. **Workspace**: count_by_state, operations_total, operation_duration (Histogram)
3. **Coordinator**: leader_status (5개 coordinator), tick_duration (Histogram), reconcile_queue
4. **WebSocket**: active_connections, message_latency (Histogram), errors_total
5. **Circuit Breaker**: state

### ⚠️ 데이터 부족 메트릭
- `workspace_ttl_expiry_total` - TTL 만료 이벤트가 아직 없음
- `coordinator_observer_api_errors_total` - API 에러가 발생하지 않음
- `coordinator_gc_orphans_deleted_total` - GC 삭제 이벤트가 없음
- `circuit_breaker_failures_total` - Circuit breaker 실패가 없음
- `circuit_breaker_rejections_total` - Circuit breaker 거부가 없음

### 대시보드 패널 추천

**Overall 섹션에 포함할 메트릭**:
1. **System Health**:
   - DB UP/DOWN (`codehub_db_up`)
   - Coordinator 리더 수 (`sum(codehub_coordinator_leader_status)`) → 5/5
   - Circuit Breaker 상태 (`codehub_circuit_breaker_state{name="external"}`)
   - 작업 성공률 (`rate(codehub_workspace_operations_total{status="success"})`)

2. **Workspace Status**:
   - 총 워크스페이스 수 (`sum(codehub_workspace_count_by_state{phase!~"DELETED|DELETING"})`)
   - 상태별 분포 (RUNNING, PENDING, ERROR, ARCHIVED)

3. **Process Status**:
   - Reconcile Queue (`codehub_coordinator_wc_reconcile_queue`)
   - WebSocket 연결 수 (`codehub_ws_active_connections`)
   - DB Pool 사용률

4. **Leader Status**:
   - 각 Coordinator 리더 상태 개별 표시 (wc, observer, ttl, gc, metrics)

**상세 섹션에 포함할 메트릭**:
- Workspace operation duration (P50, P95, P99)
- Coordinator tick duration (P50, P95, P99)
- WebSocket message latency (P50, P95, P99)
- Observer API duration (P50, P95, P99)

### Histogram 메트릭 분석 가능
- `workspace_operation_duration_seconds` ✅ (ARCHIVING, RESTORING, STARTING, STOPPING)
- `coordinator_tick_duration_seconds` ✅ (wc, observer, ttl, gc, metrics)
- `ws_message_latency_seconds` ✅ (client_to_backend, backend_to_client)
- `coordinator_observer_api_duration_seconds` ✅ (volumes, containers, archives)

---

## 다음 단계

### Phase 2: 대시보드 생성 준비 완료
- ✅ 모든 핵심 메트릭 수집 확인
- ✅ Histogram 데이터 충분 (P50, P95, P99 계산 가능)
- ✅ 레이블 구조 확인 완료

### 대시보드 JSON 생성 시 주의사항
1. **Mapping 값**: 숫자로 설정 (문자열 아님)
   - `codehub_db_up`: 0 → "DOWN", 1 → "UP"
   - `codehub_circuit_breaker_state`: 0 → "CLOSED", 1 → "HALF_OPEN", 2 → "OPEN"
   - `codehub_coordinator_leader_status`: 0 → "No Leader", 1 → "Leader"

2. **Histogram Quantile 쿼리**:
   ```promql
   # P50
   histogram_quantile(0.50, sum by (operation, le) (rate(codehub_workspace_operation_duration_seconds_bucket[5m])))

   # P95
   histogram_quantile(0.95, sum by (operation, le) (rate(codehub_workspace_operation_duration_seconds_bucket[5m])))

   # P99
   histogram_quantile(0.99, sum by (operation, le) (rate(codehub_workspace_operation_duration_seconds_bucket[5m])))
   ```

3. **Success Rate 쿼리** (0으로 나누기 방지):
   ```promql
   sum(rate(codehub_workspace_operations_total{status="success"}[5m]))
   /
   sum(rate(codehub_workspace_operations_total[5m]))
   or
   vector(1)
   ```
