# Workspace Lifecycle Metrics

워크스페이스의 상태 전환과 작업 실행을 추적하는 메트릭입니다.

## 메트릭 목록

### `codehub_workspace_state_transitions_total`

**타입**: Counter (누적)
**레이블**: `from_state`, `to_state`

워크스페이스 상태 전환 횟수를 추적합니다.

**목적**: 정상/비정상 상태 전환 패턴을 파악하여 시스템 건강도를 모니터링합니다.

**사용 예시**:

```promql
# 5분간 ERROR로 전환된 워크스페이스 수
sum(rate(codehub_workspace_state_transitions_total{to_state="ERROR"}[5m]))

# 가장 많이 발생하는 상태 전환 TOP 5
topk(5, sum by (from_state, to_state) (
  rate(codehub_workspace_state_transitions_total[1h])
))

# RUNNING으로 전환 비율 (성공적인 워크스페이스 시작)
sum(rate(codehub_workspace_state_transitions_total{to_state="RUNNING"}[5m]))
```

**문제 감지**:
- `RUNNING→ERROR` 전환 급증 → Docker 데몬 문제 또는 이미지 pull 실패
- `STANDBY→RUNNING` 전환 느림 → 컨테이너 시작 지연
- `ARCHIVED→ERROR` 급증 → 아카이브 복원 실패 (S3 문제 또는 손상된 아카이브)

**계측 위치**: `src/codehub/control/coordinator/wc.py:460`

---

### `codehub_workspace_operations_total`

**타입**: Counter (누적)
**레이블**: `operation`, `status` (success/failure/timeout)

워크스페이스 작업 실행 결과를 추적합니다.

**작업 타입**:
- `PROVISIONING`: 새 워크스페이스 생성 (빈 아카이브)
- `RESTORING`: 아카이브에서 볼륨 복원
- `STARTING`: 컨테이너 시작
- `STOPPING`: 컨테이너 중지
- `ARCHIVING`: 볼륨을 S3로 아카이브
- `DELETING`: 워크스페이스 삭제

**목적**: 작업 성공률을 측정하여 인프라 안정성을 파악합니다.

**사용 예시**:

```promql
# 작업 성공률 (최근 5분)
sum(rate(codehub_workspace_operations_total{status="success"}[5m]))
/ sum(rate(codehub_workspace_operations_total[5m]))

# 실패가 많은 작업 찾기
sum by (operation) (
  rate(codehub_workspace_operations_total{status=~"failure|timeout"}[5m])
)

# 작업별 성공/실패 분포
sum by (operation, status) (
  rate(codehub_workspace_operations_total[5m])
)
```

**문제 감지**:
- `STARTING` 실패율 > 10% → Docker 네트워크 또는 이미지 문제
- `ARCHIVING` 타임아웃 증가 → S3/MinIO 연결 지연 또는 대용량 데이터
- `RESTORING` 실패 급증 → 손상된 아카이브 또는 S3 접근 권한 문제

**계측 위치**: `src/codehub/control/coordinator/wc.py:483`

---

### `codehub_workspace_operation_duration_seconds`

**타입**: Histogram (분포)
**레이블**: `operation`
**버킷**: 1s, 5s, 10s, 30s, 60s, 120s, 300s

작업이 완료되기까지 걸린 시간을 측정합니다.

**목적**: 작업 응답 시간을 분석하여 성능 저하를 조기에 감지합니다.

**사용 예시**:

```promql
# RESTORING 작업 P95 소요 시간
histogram_quantile(0.95, rate(
  codehub_workspace_operation_duration_seconds_bucket{operation="RESTORING"}[5m]
))

# 평균 작업 시간 (작업별)
sum by (operation) (rate(codehub_workspace_operation_duration_seconds_sum[5m]))
/ sum by (operation) (rate(codehub_workspace_operation_duration_seconds_count[5m]))

# 작업 시간 분포 (heatmap)
sum by (operation, le) (
  rate(codehub_workspace_operation_duration_seconds_bucket[5m])
)
```

**문제 감지**:
- `RESTORING` P95 > 120초 → S3 다운로드 속도 문제
- `STARTING` 평균 > 10초 → Docker 이미지 pull 느림
- `ARCHIVING` P99 > 300초 → 대용량 워크스페이스 또는 압축 느림

**계측 위치**: `src/codehub/control/coordinator/wc.py:469`

---

### `codehub_workspace_count_by_state`

**타입**: Gauge (현재 값)
**레이블**: `phase` (PENDING, ARCHIVED, STANDBY, RUNNING, ERROR)

각 상태별 현재 워크스페이스 수를 측정합니다.

**목적**: 시스템 리소스 사용량과 비용을 예측합니다.

**사용 예시**:

```promql
# 현재 RUNNING 워크스페이스 수
codehub_workspace_count_by_state{phase="RUNNING"}

# 상태별 분포 (비율)
codehub_workspace_count_by_state
/ scalar(sum(codehub_workspace_count_by_state))

# 전체 워크스페이스 수
sum(codehub_workspace_count_by_state)
```

**문제 감지**:
- `ERROR` 상태 워크스페이스 급증 → 시스템 전반적 문제
- `RUNNING` > 100개 → Docker 호스트 리소스 부족 가능성
- `ARCHIVED` 비율 > 80% → 대부분 미사용 (TTL 정책 검토 필요)

**업데이트 주기**: 10초 (설정 가능)
**계측 위치**: `src/codehub/app/main.py:301`

---

### `codehub_workspace_count_by_operation`

**타입**: Gauge (현재 값)
**레이블**: `operation`

현재 진행 중인 작업별 워크스페이스 수를 측정합니다.

**목적**: 병목 현상과 동시 처리량을 파악합니다.

**사용 예시**:

```promql
# 현재 RESTORING 중인 워크스페이스 수
codehub_workspace_count_by_operation{operation="RESTORING"}

# 가장 많은 작업 타입
topk(3, codehub_workspace_count_by_operation)
```

**문제 감지**:
- `STARTING` > 10개 유지 → Docker 데몬 응답 지연
- `ARCHIVING` 계속 증가 → S3 업로드 병목
- `RESTORING` > 5개 유지 → S3 다운로드 대역폭 부족

**업데이트 주기**: 10초 (설정 가능)
**계측 위치**: `src/codehub/app/main.py:321`

---

### `codehub_workspace_ttl_expiry_total`

**타입**: Counter (누적)
**레이블**: `ttl_type` (standby, archive)

TTL 만료로 인한 자동 상태 전환 횟수를 추적합니다.

**TTL 타입**:
- `standby`: RUNNING → STANDBY (비활동 워크스페이스 중지)
- `archive`: STANDBY → ARCHIVED (장기 미사용 아카이브)

**목적**: 자동 리소스 관리 효과를 측정합니다.

**사용 예시**:

```promql
# 시간당 TTL 만료 워크스페이스 수
sum(rate(codehub_workspace_ttl_expiry_total[1h])) * 3600

# TTL 타입별 만료 비율
sum by (ttl_type) (rate(codehub_workspace_ttl_expiry_total[1h]))
```

**문제 감지**:
- `standby` TTL 급증 → 사용자가 워크스페이스를 사용하지 않음 (사용 패턴 변화)
- `archive` TTL이 0 → TTL 정책이 작동하지 않거나 기간이 너무 김

**계측 위치**: 구현 예정 (`src/codehub/control/coordinator/ttl.py`)

---

## 대시보드 예시

### Row: 시스템 개요

**Workspace 상태 분포 (Pie Chart)**:
```promql
codehub_workspace_count_by_state
```

**작업 성공률 (Gauge)**:
```promql
sum(rate(codehub_workspace_operations_total{status="success"}[5m]))
/ sum(rate(codehub_workspace_operations_total[5m]))
```

### Row: 상태 전환 추이

**상태 전환 비율 (Graph)**:
```promql
sum by (from_state, to_state) (
  rate(codehub_workspace_state_transitions_total[5m])
)
```

### Row: 작업 성능

**작업 소요 시간 P95 (Graph)**:
```promql
histogram_quantile(0.95, sum by (operation, le) (
  rate(codehub_workspace_operation_duration_seconds_bucket[5m])
))
```

**작업 실행 현황 (Stacked Bar)**:
```promql
sum by (operation, status) (
  rate(codehub_workspace_operations_total[5m])
)
```

## 알림 규칙 예시

```yaml
# 작업 실패율 높음
- alert: HighWorkspaceOperationFailureRate
  expr: |
    sum(rate(codehub_workspace_operations_total{status=~"failure|timeout"}[5m]))
    / sum(rate(codehub_workspace_operations_total[5m])) > 0.3
  for: 10m
  severity: critical
  summary: "Workspace operation failure rate > 30%"

# ERROR 상태 워크스페이스 급증
- alert: HighErrorStateWorkspaces
  expr: codehub_workspace_count_by_state{phase="ERROR"} > 10
  for: 5m
  severity: warning
  summary: "{{ $value }} workspaces in ERROR state"
```
