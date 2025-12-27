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

    R->>R: step_up (PENDING → COLD)
    Note right of R: INITIALIZING
    R->>R: step_up (COLD → WARM)
    Note right of R: storage.md 참조
    R->>R: step_up (WARM → RUNNING)
    Note right of R: instance.md 참조
```

### 상태 변화

```
PENDING → COLD → WARM → RUNNING
```

---

## 2. Auto-wake (WARM → RUNNING)

프록시가 WARM 상태 접속을 감지하면 자동으로 시작.

```mermaid
sequenceDiagram
    participant U as User
    participant P as Proxy
    participant API as Control Plane
    participant R as Reconciler

    U->>P: GET /w/{workspace_id}/
    P->>P: status = WARM 확인
    P->>U: 로딩 페이지 (SSE 연결)
    P->>API: desired_state = RUNNING

    R->>R: step_up (WARM → RUNNING)
    Note right of R: instance.md 참조

    R-->>U: SSE: status=RUNNING
    Note right of U: events.md 참조
    U->>P: 리다이렉트
```

---

## 3. TTL 기반 자동 전환

> 상세 활동 감지 메커니즘은 [activity.md](./activity.md) 참조

### 3.1 RUNNING → WARM

| 조건 | 값 |
|------|-----|
| 트리거 | WebSocket 연결 없음 후 5분 |
| 감지 방식 | Redis 기반 (ws_conn, idle_timer) |
| warm_ttl 기본값 | 300초 (5분) |

```mermaid
flowchart LR
    R[RUNNING] -->|WebSocket 끊김 후 5분| W[WARM]
```

Reconciler가 step_down 실행 → [instance.md](./instance.md) STOPPING 참조

### 3.2 WARM → COLD

| 조건 | 값 |
|------|-----|
| 트리거 | `last_access_at + cold_ttl_seconds` 경과 |
| 감지 방식 | DB 기반 |
| cold_ttl 기본값 | 86400초 (1일) |

```mermaid
flowchart LR
    W[WARM] -->|cold_ttl 만료| C[COLD]
```

Reconciler가 step_down 실행 → [storage.md](./storage.md) ARCHIVING 참조

---

## 4. Manual Restore (COLD → RUNNING)

사용자가 아카이브된 워크스페이스를 복원.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: POST /workspaces/{id}:restore
    API->>API: desired_state = RUNNING
    API->>U: 202 Accepted

    R->>R: step_up (COLD → WARM)
    Note right of R: storage.md RESTORING 참조
    R->>R: step_up (WARM → RUNNING)
    Note right of R: instance.md STARTING 참조
```

---

## 5. Manual Stop (RUNNING → WARM)

사용자가 워크스페이스를 정지.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: PATCH /workspaces/{id} {desired_state: "WARM"}
    API->>U: 200 OK

    R->>R: step_down (RUNNING → WARM)
    Note right of R: instance.md STOPPING 참조
```

---

## 6. Manual Archive (WARM → COLD)

사용자가 워크스페이스를 아카이브.

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant R as Reconciler

    U->>API: PATCH /workspaces/{id} {desired_state: "COLD"}
    API->>U: 200 OK

    R->>R: step_down (WARM → COLD)
    Note right of R: storage.md ARCHIVING 참조
```

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
    R->>R: RUNNING → instance.md delete
    R->>R: WARM → storage.md purge
    R->>R: COLD → storage.md purge
    R->>R: status = DELETED
```

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
