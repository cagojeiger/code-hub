# 주요 플로우 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

사용자 시나리오별 플로우를 정의합니다. 세부 동작은 레이어 문서를 참조하세요:

| 레이어 | 문서 | 설명 |
|--------|------|------|
| State Transitions | [states.md](./states.md) | 상태 전환 규칙 |
| Storage | [storage.md](./storage.md) | archive/restore |
| Instance | [instance.md](./instance.md) | 컨테이너 시작/정지 |
| Events | [events.md](./events.md) | SSE 이벤트 |

---

## 1. Workspace 생성

사용자가 새 워크스페이스를 생성하면 RUNNING까지 자동 전환.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: POST /workspaces
    API->>API: INSERT (desired_state=RUNNING)
    API->>U: 201 Created

    R->>R: step_up (PENDING → STANDBY)
    Note right of R: PROVISIONING (빈 Volume 생성)
    R->>R: step_up (STANDBY → RUNNING)
    Note right of R: STARTING (instance.md 참조)
```

### 상태 변화

```
PENDING → STANDBY → RUNNING
```

> 새 워크스페이스는 archive_key가 없으므로 PROVISIONING 실행

---

## 2. Auto-wake (STANDBY → RUNNING)

프록시가 STANDBY 상태 접속을 감지하면 자동으로 시작.

```mermaid
sequenceDiagram
    participant U as User
    participant P as Proxy
    participant API as Control Plane
    participant R as Reconciler

    U->>P: GET /w/{workspace_id}/
    P->>P: status = STANDBY 확인
    P->>U: 로딩 페이지 (SSE 연결)
    P->>API: desired_state = RUNNING

    R->>R: step_up (STANDBY → RUNNING)
    Note right of R: STARTING (instance.md 참조)

    R-->>U: SSE: status=RUNNING
    Note right of U: events.md 참조
    U->>P: 리다이렉트
```

> **Note**: PENDING(ARCHIVED) 상태에서는 auto-wake 없음. 수동 복원 필요.

---

## 3. TTL 기반 자동 전환

> 상세 활동 감지 메커니즘은 [activity.md](./activity.md) 참조

### 3.1 RUNNING → STANDBY

| 조건 | 값 |
|------|-----|
| 트리거 | WebSocket 연결 없음 후 5분 |
| 감지 방식 | Redis 기반 (ws_conn, idle_timer) |
| standby_ttl 기본값 | 300초 (5분) |

```mermaid
flowchart LR
    R[RUNNING] -->|WebSocket 끊김 후 5분| S[STANDBY]
```

Reconciler가 step_down 실행 → [instance.md](./instance.md) STOPPING 참조

### 3.2 STANDBY → PENDING (ARCHIVED)

| 조건 | 값 |
|------|-----|
| 트리거 | `last_access_at + archive_ttl_seconds` 경과 |
| 감지 방식 | DB 기반 |
| archive_ttl 기본값 | 86400초 (1일) |

```mermaid
flowchart LR
    S[STANDBY] -->|archive_ttl 만료| P[PENDING]
    P --> D{archive_key?}
    D -->|있음| A[Display: ARCHIVED]
    D -->|없음| A2[Display: PENDING]
```

Reconciler가 step_down 실행 → [storage.md](./storage.md) ARCHIVING 참조

---

## 4. Manual Restore (ARCHIVED → RUNNING)

사용자가 아카이브된 워크스페이스를 복원.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: POST /workspaces/{id}:restore
    API->>API: desired_state = RUNNING
    API->>U: 202 Accepted

    R->>R: step_up (PENDING → STANDBY)
    Note right of R: RESTORING (archive_key 있음, storage.md 참조)
    R->>R: step_up (STANDBY → RUNNING)
    Note right of R: STARTING (instance.md 참조)
```

> PENDING 상태에서 archive_key가 있으면 RESTORING, 없으면 PROVISIONING

---

## 5. Manual Stop (RUNNING → STANDBY)

사용자가 워크스페이스를 정지.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: PATCH /workspaces/{id} {desired_state: "STANDBY"}
    API->>U: 200 OK

    R->>R: step_down (RUNNING → STANDBY)
    Note right of R: STOPPING (instance.md 참조)
```

---

## 6. Manual Archive (STANDBY → ARCHIVED)

사용자가 워크스페이스를 아카이브.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: PATCH /workspaces/{id} {desired_state: "PENDING"}
    API->>U: 200 OK

    R->>R: step_down (STANDBY → PENDING)
    Note right of R: ARCHIVING (storage.md 참조)
    Note right of R: 완료 후 archive_key 저장 → Display: ARCHIVED
```

> desired_state="PENDING" + ARCHIVING 완료 → archive_key 생성 → Display: ARCHIVED

---

## 7. Workspace 삭제

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: DELETE /workspaces/{id}
    API->>API: deleted_at = NOW()
    API->>U: 204 No Content

    R->>R: operation = DELETING
    Note right of R: 상태별 정리
    R->>R: RUNNING → Container 삭제 (instance.md)
    R->>R: STANDBY → Volume 삭제 (storage.md)
    R->>R: PENDING → Volume 없음 (skip)
    R->>R: status = DELETED
    Note right of R: Archive는 GC가 정리 (storage-gc.md)
```

> Volume/Container만 즉시 삭제, Archive는 GC가 나중에 정리

---

## 8. 에러 복구

```mermaid
flowchart TD
    A[operation 실패] --> B[status = ERROR]
    B --> C{error_count < 3?}
    C -->|Yes| D[자동 재시도]
    C -->|No| E[관리자 개입]
    D --> F[성공]
    D --> B
```

---

## 참조

- [states.md](./states.md) - 상태 전환 규칙
- [storage.md](./storage.md) - 스토리지 동작
- [instance.md](./instance.md) - 인스턴스 동작
- [events.md](./events.md) - SSE 이벤트
