# Logging Guide

로깅 시스템 설계 및 사용 가이드.

## 로그 레벨 정책

| 레벨 | 용도 | 예시 |
|-----|------|------|
| ERROR | 실패 (원인/분류/다음 액션) | Operation failed, DB connection failed |
| WARN | SLO 위협 (느림/리트라이/큐 적체) | Slow reconcile, Circuit open |
| INFO | 요청/작업 요약 1줄 | Reconcile completed, Request completed |
| DEBUG | 단계별 내부 로그 (운영 비활성화) | Step-by-step tracing |

## 표준 필드

모든 로그에 자동 추가되는 필드:

| 필드 | 타입 | 설명 |
|-----|------|------|
| `timestamp` | string | ISO 8601 형식 (UTC) |
| `level` | string | LOG 레벨 (INFO, ERROR 등) |
| `logger` | string | 로거 이름 |
| `pid` | int | 프로세스 ID |
| `schema_version` | string | 로그 스키마 버전 (현재 "1.0") |
| `service` | string | 서비스명 (codehub-control-plane) |
| `trace_id` | string | 분산 추적 ID (설정된 경우) |

## 이벤트 타입 (event 필드)

`extra={"event": LogEvent.XXX}` 형태로 사용:

### Coordinator 이벤트
- `reconcile_complete`: Reconcile 완료
- `reconcile_slow`: Slow reconcile 경고
- `observation_complete`: 리소스 관측 완료
- `state_changed`: 상태 변경
- `operation_failed`: Operation 실패
- `operation_timeout`: Operation 타임아웃
- `operation_success`: Operation 성공

### Leadership 이벤트
- `leadership_acquired`: 리더십 획득
- `leadership_lost`: 리더십 상실

### Resource 이벤트
- `container_disappeared`: 컨테이너 사라짐 (OOM/crash 감지용)

### Container 이벤트
- `container_started`: 컨테이너 시작됨
- `container_stopped`: 컨테이너 정지됨
- `container_exited`: 컨테이너 종료 (exit_code 포함)

### Volume 이벤트
- `volume_created`: 볼륨 생성됨
- `volume_removed`: 볼륨 삭제됨

### Archive/Restore 이벤트
- `archive_success`: 아카이브 성공
- `archive_failed`: 아카이브 실패
- `restore_success`: 복원 성공
- `restore_failed`: 복원 실패

### Lifecycle 이벤트
- `app_started`: 애플리케이션 시작됨
- `app_stopped`: 애플리케이션 종료됨

### API 이벤트
- `request_complete`: 요청 완료
- `request_failed`: 요청 실패
- `request_slow`: 느린 요청 (threshold 초과)

### SSE 이벤트
- `sse_connected`: SSE 클라이언트 연결됨
- `sse_disconnected`: SSE 클라이언트 연결 해제
- `sse_received`: SSE 이벤트 수신

### WebSocket/Proxy 이벤트
- `ws_error`: WebSocket 오류
- `upstream_error`: Upstream 연결 오류

### Infrastructure 이벤트
- `db_connected`: PostgreSQL 연결 성공
- `db_error`: PostgreSQL 연결 실패
- `s3_connected`: S3 스토리지 연결 성공
- `s3_bucket_created`: S3 버킷 생성됨
- `s3_error`: S3 연결 실패
- `redis_subscribed`: Redis PUB/SUB 구독
- `redis_connection_error`: Redis 연결 오류

### CDC 이벤트
- `notify_received`: PG NOTIFY 수신
- `wake_published`: Wake 신호 발행
- `sse_published`: SSE 이벤트 발행

## 고카디널리티 규칙

### 로그에는 OK
- `ws_id`: 워크스페이스 ID (검색용)
- `user_id`: 사용자 ID (검색용)
- `request_id`: 요청 ID (추적용)
- `trace_id`: 분산 추적 ID

### 메트릭 라벨에는 NO
- 위의 필드들을 메트릭 라벨로 사용 금지 (카디널리티 폭발)

## 레이트 리밋

로그 폭풍 방지를 위한 레이트 리밋:

- **WARNING/INFO**: 동일 메시지 분당 100건 제한 (설정 가능)
- **ERROR**: 제한 없음 (항상 로깅)

설정:
```bash
LOGGING_RATE_LIMIT_PER_MINUTE=100
```

## Slow Threshold 경고

느린 작업 감지를 위한 임계값:

- 기본값: 1000ms (1초)
- 초과 시 WARNING 로그 발생

설정:
```bash
LOGGING_SLOW_THRESHOLD_MS=1000
```

## 사용 예시

### Coordinator 로그

```python
from codehub.core.logging_schema import LogEvent

# Reconcile 완료 (INFO)
logger.info(
    "[%s] Reconcile completed",
    self.name,
    extra={
        "event": LogEvent.RECONCILE_COMPLETE,
        "processed": 10,
        "changed": 2,
        "actions": {"STARTING": 1, "STOPPING": 1},
        "duration_ms": 1234.5,
    },
)

# Operation 실패 (ERROR)
logger.error(
    "[%s] Operation failed",
    self.name,
    extra={
        "event": LogEvent.OPERATION_FAILED,
        "ws_id": ws.id,
        "operation": "STARTING",
        "error": str(exc),
    },
)
```

### API 요청 로그

`LoggingMiddleware`를 사용하면 자동으로 요청당 1줄 로그가 남음:

```json
{
  "timestamp": "2026-01-10T15:30:00.123Z",
  "level": "INFO",
  "schema_version": "1.0",
  "service": "codehub-control-plane",
  "event": "request_complete",
  "method": "GET",
  "path": "/api/v1/workspaces",
  "status": 200,
  "duration_ms": 45.2,
  "trace_id": "abc-123"
}
```

## 환경 변수

| 변수 | 기본값 | 설명 |
|-----|-------|------|
| `LOGGING_LEVEL` | `INFO` | 로그 레벨 |
| `LOGGING_SCHEMA_VERSION` | `1.0` | 스키마 버전 |
| `LOGGING_SLOW_THRESHOLD_MS` | `1000` | Slow 경고 임계값 (ms) |
| `LOGGING_RATE_LIMIT_PER_MINUTE` | `100` | 분당 최대 로그 수 |
| `LOGGING_SERVICE_NAME` | `codehub-control-plane` | 서비스명 |

## 분산 추적

### Trace ID 전파

1. API 요청 시 `X-Trace-ID` 헤더로 전달
2. 없으면 자동 생성 (UUID)
3. 응답에 `X-Trace-ID` 헤더로 반환

### 코드에서 Trace ID 사용

```python
from codehub.app.logging import set_trace_id, get_trace_id, clear_trace_context

# Trace ID 설정
trace_id = set_trace_id()  # 자동 생성
# 또는
set_trace_id("custom-trace-id")

# Trace ID 조회
current_trace_id = get_trace_id()

# Context 정리 (요청 끝)
clear_trace_context()
```

## 로그 검색 예시

### Loki/Grafana 쿼리

```logql
# 특정 워크스페이스 로그
{service="codehub-control-plane"} | json | ws_id="ws-123"

# 실패한 operation
{service="codehub-control-plane"} | json | event="operation_failed"
```

