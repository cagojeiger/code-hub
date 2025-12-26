# 주요 플로우 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

M2의 핵심 플로우를 정의합니다. 모든 상태 전환은 Reconciler를 통해 이루어집니다.

---

## 1. Workspace 생성

### 시퀀스

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant DB as Database
    participant Redis as Redis
    participant R as Reconciler

    U->>API: POST /workspaces
    API->>DB: INSERT (status=PENDING, desired_state=RUNNING)
    API->>Redis: PUBLISH reconciler:hints {workspace_id}
    API->>U: 201 Created

    Redis-->>R: 힌트 수신
    R->>DB: status != desired_state 감지
    R->>R: step_up (PENDING → COLD)
    R->>DB: status = INITIALIZING
    R->>R: 초기화 완료
    R->>DB: status = COLD

    R->>R: step_up (COLD → WARM)
    R->>DB: status = RESTORING
    R->>R: Volume 프로비저닝
    R->>DB: status = WARM

    R->>R: step_up (WARM → RUNNING)
    R->>DB: status = STARTING
    R->>R: 컨테이너 시작
    R->>DB: status = RUNNING
```

### 상태 변화

```
PENDING → INITIALIZING → COLD → RESTORING → WARM → STARTING → RUNNING
```

---

## 2. Auto-wake (WARM → RUNNING)

### 트리거
- 프록시가 WARM 상태의 워크스페이스 접속 감지

### 시퀀스

```mermaid
sequenceDiagram
    participant U as User
    participant P as Proxy
    participant API as Control Plane
    participant DB as Database
    participant Redis as Redis
    participant R as Reconciler

    U->>P: GET /w/{workspace_id}/
    P->>DB: status 확인
    DB-->>P: status = WARM

    P->>U: 로딩 페이지 (SSE 연결)
    P->>API: PATCH /workspaces/{id} (desired_state=RUNNING)
    API->>DB: desired_state = RUNNING
    API->>Redis: PUBLISH reconciler:hints {workspace_id}

    Redis-->>R: 힌트 수신
    R->>DB: status != desired_state 감지
    R->>R: step_up (WARM → RUNNING)
    R->>DB: status = STARTING
    R->>R: 컨테이너 시작
    R->>DB: status = RUNNING

    R->>P: SSE: state_changed (RUNNING)
    P->>U: 리다이렉트
    U->>P: GET /w/{workspace_id}/
    P->>Container: 프록시
```

### 로딩 페이지

```html
<!-- 로딩 페이지 예시 -->
<div class="loading">
  <h1>Workspace 시작 중...</h1>
  <p>잠시만 기다려주세요.</p>
  <div class="spinner"></div>
</div>

<script>
const es = new EventSource('/api/v1/workspaces/{id}/events');
es.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.status === 'RUNNING') {
    window.location.reload();
  }
};
</script>
```

---

## 3. TTL 기반 상태 전환

### 3.1 RUNNING → WARM (idle timeout)

```mermaid
sequenceDiagram
    participant R as Reconciler
    participant DB as Database
    participant I as Instance Controller

    Note over R: 주기적 폴링 (1분)
    R->>DB: SELECT * FROM workspaces<br/>WHERE status='RUNNING'<br/>AND last_access_at + warm_ttl < NOW()

    loop 대상 워크스페이스마다
        R->>DB: desired_state = WARM
        R->>R: step_down (RUNNING → WARM)
        R->>DB: status = STOPPING
        R->>I: 컨테이너 정지
        R->>DB: status = WARM
    end
```

### 3.2 WARM → COLD (archive timeout)

```mermaid
sequenceDiagram
    participant R as Reconciler
    participant DB as Database
    participant S as Storage Provider
    participant V as Docker Volume
    participant M as MinIO

    R->>DB: SELECT * FROM workspaces<br/>WHERE status='WARM'<br/>AND last_access_at + cold_ttl < NOW()

    loop 대상 워크스페이스마다
        R->>DB: desired_state = COLD
        R->>R: step_down (WARM → COLD)
        R->>DB: status = ARCHIVING
        R->>S: archive(home_store_key)
        S->>V: Volume 데이터 읽기
        S->>M: PUT object (tar.gz)
        M-->>S: archive_key
        S->>V: Volume 삭제
        S-->>R: archive_key
        R->>DB: archive_key = {key}, status = COLD
    end
```

---

## 4. Manual Restore (COLD → RUNNING)

### 시퀀스

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant DB as Database
    participant Redis as Redis
    participant R as Reconciler
    participant S as Storage Provider
    participant M as MinIO
    participant V as Docker Volume
    participant I as Instance Controller

    U->>API: POST /workspaces/{id}:restore
    API->>DB: desired_state = RUNNING
    API->>Redis: PUBLISH reconciler:hints {workspace_id}
    API->>U: 202 Accepted

    Redis-->>R: 힌트 수신
    R->>DB: status != desired_state 감지
    R->>R: step_up (COLD → WARM)
    R->>DB: status = RESTORING
    R->>S: restore(archive_key)
    S->>M: GET object (tar.gz)
    M-->>S: archive data
    S->>V: Volume 생성 + 데이터 복원
    S-->>R: home_store_key
    R->>DB: status = WARM

    R->>R: step_up (WARM → RUNNING)
    R->>DB: status = STARTING
    R->>I: 컨테이너 시작 (Volume 마운트)
    I-->>R: 시작 완료
    R->>DB: status = RUNNING
```

---

## 5. Manual Stop (RUNNING → WARM)

### 시퀀스

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant DB as Database
    participant Redis as Redis
    participant R as Reconciler

    U->>API: PATCH /workspaces/{id}<br/>{desired_state: "WARM"}
    API->>DB: desired_state = WARM
    API->>Redis: PUBLISH reconciler:hints {workspace_id}
    API->>U: 200 OK

    Redis-->>R: 힌트 수신
    R->>DB: status != desired_state 감지
    R->>R: step_down (RUNNING → WARM)
    R->>DB: status = STOPPING
    R->>R: 컨테이너 정지
    R->>DB: status = WARM
```

---

## 6. Manual Archive (WARM → COLD)

### 시퀀스

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant DB as Database
    participant Redis as Redis
    participant R as Reconciler
    participant S as Storage Provider
    participant V as Docker Volume
    participant M as MinIO

    U->>API: PATCH /workspaces/{id}<br/>{desired_state: "COLD"}
    API->>DB: desired_state = COLD
    API->>Redis: PUBLISH reconciler:hints {workspace_id}
    API->>U: 200 OK

    Redis-->>R: 힌트 수신
    R->>DB: status != desired_state 감지
    R->>R: step_down (WARM → COLD)
    R->>DB: status = ARCHIVING
    R->>S: archive(home_store_key)
    S->>V: Volume 데이터 읽기
    S->>M: PUT object (tar.gz)
    M-->>S: archive_key
    S->>V: Volume 삭제
    S-->>R: archive_key
    R->>DB: archive_key = {key}, status = COLD
```

---

## 7. Workspace 삭제

### 시퀀스

```mermaid
sequenceDiagram
    participant U as User
    participant API as Control Plane
    participant DB as Database
    participant Redis as Redis
    participant R as Reconciler
    participant I as Instance Controller
    participant S as Storage Provider

    U->>API: DELETE /workspaces/{id}
    API->>DB: deleted_at = NOW()
    API->>Redis: PUBLISH reconciler:hints {workspace_id}
    API->>U: 204 No Content

    Redis-->>R: 힌트 수신
    R->>DB: deleted_at != NULL 감지
    R->>DB: status = DELETING

    alt status was RUNNING
        R->>I: 컨테이너 삭제
    end

    alt status was WARM
        R->>S: purge(home_store_key)
    end

    alt status was COLD
        R->>S: purge(archive_key)
    end

    R->>DB: status = DELETED
```

---

## 8. 에러 복구

### 시퀀스

```mermaid
sequenceDiagram
    participant R as Reconciler
    participant DB as Database

    Note over R: 전환 중 실패 발생
    R->>DB: status = ERROR,<br/>error_message = "...",<br/>error_count += 1

    Note over R: 다음 Reconcile 사이클
    R->>DB: error_count < max_retry?

    alt 재시도 가능
        R->>R: 이전 전환 재시도
        R->>DB: error_count = 0 (성공 시)
    else 재시도 불가
        R->>R: 관리자 알림
    end
```

### 에러 상태 해제 조건

| 조건 | 동작 |
|------|------|
| error_count < 3 | 자동 재시도 |
| error_count >= 3 | 관리자 개입 필요, 수동 해제 |

---

## 9. SSE 이벤트 (실시간 상태 알림)

### 개요

UI(대시보드, 로딩 페이지)가 상태 변경을 실시간으로 확인할 수 있도록 SSE(Server-Sent Events) 제공.

### 시퀀스

```mermaid
sequenceDiagram
    participant UI as Dashboard/Loading Page
    participant API as Control Plane
    participant Redis as Redis Pub/Sub
    participant R as Reconciler
    participant DB as Database

    UI->>API: GET /workspaces/{id}/events
    Note over UI,API: SSE 연결 유지
    API->>Redis: SUBSCRIBE workspace:{id}

    R->>DB: status = STARTING
    R->>Redis: PUBLISH workspace:{id}<br/>{status: "STARTING"}
    Redis-->>API: 메시지 수신
    API-->>UI: event: state_changed<br/>data: {status: "STARTING"}

    R->>DB: status = RUNNING
    R->>Redis: PUBLISH workspace:{id}<br/>{status: "RUNNING"}
    Redis-->>API: 메시지 수신
    API-->>UI: event: state_changed<br/>data: {status: "RUNNING"}

    UI->>UI: 상태에 따라 UI 업데이트
```

### SSE 엔드포인트

```
GET /api/v1/workspaces/{id}/events
Accept: text/event-stream
```

### 이벤트 타입

| 이벤트 | 데이터 | 설명 |
|--------|--------|------|
| `state_changed` | `{workspace_id, status, desired_state}` | 상태 변경 |
| `error` | `{workspace_id, error_message, error_count}` | 에러 발생 |
| `progress` | `{workspace_id, phase, progress_pct}` | 진행 상황 (선택) |

### 예시

```
event: state_changed
data: {"workspace_id": "abc123", "status": "STARTING", "desired_state": "RUNNING"}

event: state_changed
data: {"workspace_id": "abc123", "status": "RUNNING", "desired_state": "RUNNING"}

event: error
data: {"workspace_id": "abc123", "error_message": "Container start failed", "error_count": 1}
```

### 클라이언트 코드

```javascript
const eventSource = new EventSource('/api/v1/workspaces/{id}/events');

eventSource.addEventListener('state_changed', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Status: ${data.status}`);

  if (data.status === 'RUNNING') {
    // 로딩 페이지 → 워크스페이스로 리다이렉트
    window.location.href = `/w/${data.workspace_id}/`;
  }
});

eventSource.addEventListener('error', (e) => {
  const data = JSON.parse(e.data);
  alert(`Error: ${data.error_message}`);
});
```

### 구현 방식

| 컴포넌트 | 역할 |
|----------|------|
| Reconciler | 상태 변경 시 Redis PUBLISH |
| API Server | Redis SUBSCRIBE → SSE 전달 |
| Redis | Pub/Sub 채널 (`workspace:{id}`) |

---

## 참조

- [states.md](./states.md) - 상태 정의
- [api.md](./api.md) - API 스펙
- [ADR-006: Reconciler 패턴](../adr/006-reconciler-pattern.md)
