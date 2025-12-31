# SSE Events (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

상태 변경 시 UI(대시보드, 로딩 페이지)에 실시간 알림을 전달합니다.

| 항목 | 값 |
|------|---|
| 패턴 | CDC (Change Data Capture) |
| 트리거 | PostgreSQL NOTIFY |
| 팬아웃 | Redis Pub/Sub |

---

## 이벤트 전달 흐름

```mermaid
sequenceDiagram
    participant W as Writer (API, Reconciler, ...)
    participant DB as PostgreSQL
    participant C as Coordinator (EventListener)
    participant Redis as Redis Pub/Sub
    participant API as Control Plane
    participant UI as Dashboard

    W->>DB: UPDATE workspaces SET ...
    Note over DB: Trigger 실행
    DB-->>C: NOTIFY workspace_changes
    C->>Redis: PUBLISH workspace:{id}
    Redis-->>API: 메시지 수신
    API-->>UI: SSE event
```

---

## 이벤트 발행 구조

### PostgreSQL Trigger

| 항목 | 값 |
|------|---|
| 트리거 대상 | workspaces 테이블 UPDATE |
| 감시 컬럼 | observed_status, operation, error_info |
| 발행 | pg_notify('workspace_changes', payload) |

> Writer는 이벤트 발행을 모름 (Single Responsibility)

### Coordinator EventListener

| 항목 | 값 |
|------|---|
| 위치 | Coordinator 프로세스 내 |
| 연결 | PostgreSQL LISTEN 'workspace_changes' |
| 역할 | NOTIFY 수신 → Redis PUBLISH |

> Leader Election 불필요: LISTEN은 모든 인스턴스에서 수신해도 Redis PUBLISH는 멱등

---

## SSE 엔드포인트

```
GET /api/v1/workspaces/{id}/events
Accept: text/event-stream
```

| 항목 | 값 |
|------|---|
| 연결 | 클라이언트 유지 |
| Heartbeat | 30초 주기 |
| 재연결 | 클라이언트 자동 재연결 |

---

## Redis Pub/Sub

| 항목 | 값 |
|------|---|
| 채널 | `workspace:{workspace_id}` |
| 메시지 | JSON (이벤트 데이터) |

---

## 이벤트 타입

### state_changed

상태 또는 operation 변경 시 발행.

| 필드 | 타입 | 설명 |
|------|------|------|
| workspace_id | string | 워크스페이스 ID |
| status | string | 현재 상태 |
| operation | string | 진행 중인 작업 |
| desired_state | string | 목표 상태 |

### error

에러 발생 시 발행.

| 필드 | 타입 | 설명 |
|------|------|------|
| workspace_id | string | 워크스페이스 ID |
| error_message | string | 에러 메시지 |
| error_count | int | 연속 실패 횟수 |

### heartbeat

연결 유지용 (30초마다).

| 필드 | 타입 | 설명 |
|------|------|------|
| timestamp | string | ISO 8601 |

---

## 발행 시점

| 상황 | 이벤트 |
|------|--------|
| operation 시작 | state_changed |
| operation 완료 | state_changed |
| 에러 발생 | error |
| 에러 복구 | state_changed |

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| 연결 실패 | EventSource 자동 재연결 |
| 메시지 유실 | REST API로 현재 상태 조회 |

> SSE는 실시간 알림 용도, 상태 동기화는 REST API

---

## 참조

- [states.md](./states.md) - 상태 정의
- [glossary.md](./glossary.md) - CDC 용어 정의
- [components/coordinator.md](./components/coordinator.md) - Coordinator (EventListener 포함)
