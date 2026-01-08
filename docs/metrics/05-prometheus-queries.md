# Useful Prometheus Queries

자주 사용하는 PromQL 쿼리 모음입니다.

## Workspace 건강도

### 전체 워크스페이스 수

```promql
sum(codehub_workspace_count_by_state)
```

### RUNNING 워크스페이스 수

```promql
codehub_workspace_count_by_state{phase="RUNNING"}
```

### 시간당 상태 전환 횟수

```promql
sum(rate(codehub_workspace_state_transitions_total[1h])) * 3600
```

### 작업 성공률 (최근 5분)

```promql
sum(rate(codehub_workspace_operations_total{status="success"}[5m]))
/ sum(rate(codehub_workspace_operations_total[5m]))
```

### ERROR 상태로 전환된 워크스페이스 (최근 1시간)

```promql
sum(increase(codehub_workspace_state_transitions_total{to_state="ERROR"}[1h]))
```

### 가장 실패가 많은 작업 TOP 3

```promql
topk(3, sum by (operation) (
  rate(codehub_workspace_operations_total{status=~"failure|timeout"}[5m])
))
```

---

## Workspace 성능

### 작업별 평균 소요 시간

```promql
sum by (operation) (rate(codehub_workspace_operation_duration_seconds_sum[5m]))
/ sum by (operation) (rate(codehub_workspace_operation_duration_seconds_count[5m]))
```

### RESTORING 작업 P95 latency

```promql
histogram_quantile(0.95, rate(
  codehub_workspace_operation_duration_seconds_bucket{operation="RESTORING"}[5m]
))
```

### 가장 느린 작업 (P99 기준)

```promql
topk(3, histogram_quantile(0.99, sum by (operation, le) (
  rate(codehub_workspace_operation_duration_seconds_bucket[5m])
)))
```

### 작업 타임아웃 발생률

```promql
sum by (operation) (
  rate(codehub_workspace_operations_total{status="timeout"}[5m])
)
```

---

## Coordinator 성능

### WC tick P95 latency

```promql
histogram_quantile(0.95, rate(
  codehub_coordinator_tick_duration_seconds_bucket{coordinator_type="wc"}[5m]
))
```

### Coordinator별 평균 tick 시간

```promql
sum by (coordinator_type) (
  rate(codehub_coordinator_tick_duration_seconds_sum[5m])
) / sum by (coordinator_type) (
  rate(codehub_coordinator_tick_duration_seconds_count[5m])
)
```

### Coordinator별 에러율

```promql
sum by (coordinator_type) (
  rate(codehub_coordinator_tick_total{status="error"}[5m])
) / sum by (coordinator_type) (
  rate(codehub_coordinator_tick_total[5m])
)
```

### 시간당 tick 실행 횟수

```promql
sum by (coordinator_type) (
  rate(codehub_coordinator_tick_total{status="success"}[1h]) * 3600
)
```

### 현재 리더 상태 (각 coordinator 타입별)

```promql
sum by (coordinator_type) (codehub_coordinator_leader_status)
```

**정상**: 각 coordinator 타입당 값이 1
**비정상**: 0 (리더 없음) 또는 2+ (스플릿 브레인)

### Reconcile 큐 깊이

```promql
codehub_coordinator_wc_reconcile_queue
```

### 분당 CAS 실패 횟수

```promql
rate(codehub_coordinator_wc_cas_failures_total[1m]) * 60
```

---

## Observer 성능

### Docker API P95 응답 시간

```promql
histogram_quantile(0.95, rate(
  codehub_coordinator_observer_api_duration_seconds_bucket{resource_type="containers"}[5m]
))
```

### 리소스 타입별 API 에러율

```promql
sum by (resource_type) (
  rate(codehub_coordinator_observer_api_errors_total[5m])
) / (
  sum by (resource_type) (
    rate(codehub_coordinator_observer_api_errors_total[5m])
  ) + sum by (resource_type) (
    rate(codehub_coordinator_observer_api_duration_seconds_count[5m])
  )
)
```

### 에러 타입별 발생률

```promql
sum by (error_type) (
  rate(codehub_coordinator_observer_api_errors_total[5m])
)
```

---

## WebSocket 성능

### 활성 연결 수

```promql
codehub_ws_active_connections
```

### 메시지 P95 latency (양방향)

```promql
histogram_quantile(0.95, sum by (direction, le) (
  rate(codehub_ws_message_latency_seconds_bucket[5m])
))
```

### 방향별 평균 latency 비교

```promql
sum by (direction) (rate(codehub_ws_message_latency_seconds_sum[5m]))
/ sum by (direction) (rate(codehub_ws_message_latency_seconds_count[5m]))
```

### WebSocket 에러율

```promql
sum(rate(codehub_ws_errors_total[5m]))
/ (sum(rate(codehub_ws_errors_total[5m])) + rate(codehub_ws_active_connections[5m]))
```

### 에러 타입별 분포

```promql
sum by (error_type) (rate(codehub_ws_errors_total[5m]))
```

### 최대 동시 연결 수 (지난 1시간)

```promql
max_over_time(codehub_ws_active_connections[1h])
```

---

## Database 성능

### 연결 풀 사용률

```promql
codehub_db_pool_checkedout
/ (codehub_db_pool_checkedout + codehub_db_pool_checkedin)
```

**권장**: < 0.8 (80% 미만 유지)

### 오버플로 연결 발생 여부

```promql
codehub_db_pool_overflow > 0
```

**의미**: 풀 크기를 초과한 연결이 생성됨 (풀 크기 증가 고려)

### DB 가용성

```promql
codehub_db_up
```

**값**: 1 (연결됨), 0 (연결 끊김)

---

## 복합 쿼리 (고급)

### 작업 처리 속도 (분당 완료 작업 수)

```promql
sum(rate(codehub_workspace_operations_total{status="success"}[5m])) * 60
```

### 워크스페이스 생성부터 RUNNING까지 평균 시간

이 쿼리는 직접 측정하지 않으므로 로그 분석이 필요합니다.
대신 개별 작업 시간을 합산하여 추정:

```promql
# PROVISIONING + RESTORING + STARTING 평균 시간
(
  sum(rate(codehub_workspace_operation_duration_seconds_sum{operation="PROVISIONING"}[5m]))
  / sum(rate(codehub_workspace_operation_duration_seconds_count{operation="PROVISIONING"}[5m]))
) + (
  sum(rate(codehub_workspace_operation_duration_seconds_sum{operation="RESTORING"}[5m]))
  / sum(rate(codehub_workspace_operation_duration_seconds_count{operation="RESTORING"}[5m]))
) + (
  sum(rate(codehub_workspace_operation_duration_seconds_sum{operation="STARTING"}[5m]))
  / sum(rate(codehub_workspace_operation_duration_seconds_count{operation="STARTING"}[5m]))
)
```

### 시스템 부하 점수 (0-100)

여러 메트릭을 조합한 종합 점수:

```promql
(
  # 작업 성공률 (40점)
  40 * (
    sum(rate(codehub_workspace_operations_total{status="success"}[5m]))
    / sum(rate(codehub_workspace_operations_total[5m]))
  )
) + (
  # Reconcile 큐 부하 (30점, 큐 0개 = 30점, 50개 이상 = 0점)
  30 * clamp_max(1 - (codehub_coordinator_wc_reconcile_queue / 50), 1)
) + (
  # Coordinator 에러율 (30점, 에러 0% = 30점)
  30 * (1 - clamp_max(
    sum(rate(codehub_coordinator_tick_total{status="error"}[5m]))
    / sum(rate(codehub_coordinator_tick_total[5m])),
    1
  ))
)
```

---

## Grafana 대시보드용 변수

### Workspace Operation 선택

```promql
label_values(codehub_workspace_operations_total, operation)
```

### Coordinator Type 선택

```promql
label_values(codehub_coordinator_tick_total, coordinator_type)
```

### Phase 선택

```promql
label_values(codehub_workspace_count_by_state, phase)
```

---

## 쿼리 최적화 팁

### rate() vs increase()

- `rate()`: 초당 증가율 (그래프용)
- `increase()`: 기간 동안 총 증가량 (카운트용)

```promql
# 5분간 상태 전환 횟수
increase(codehub_workspace_state_transitions_total[5m])

# 초당 상태 전환 비율
rate(codehub_workspace_state_transitions_total[5m])
```

### histogram_quantile() 사용

Histogram은 버킷별로 저장되므로 quantile 계산 시 `sum by (le)`가 필요합니다:

```promql
histogram_quantile(0.95, sum by (operation, le) (
  rate(codehub_workspace_operation_duration_seconds_bucket[5m])
))
```

### 레이블 필터링

불필요한 시리즈를 줄여 쿼리 성능 향상:

```promql
# 좋음: 특정 작업만 조회
codehub_workspace_operations_total{operation="STARTING"}

# 나쁨: 모든 작업 조회 후 필터
codehub_workspace_operations_total
```

---

## 참고

- [Prometheus 쿼리 기본](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [Histogram과 Summary](https://prometheus.io/docs/practices/histograms/)
- [Recording Rules](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/) - 자주 사용하는 복잡한 쿼리를 미리 계산
