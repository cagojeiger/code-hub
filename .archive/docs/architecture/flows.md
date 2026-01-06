# 요청 흐름

> [README.md](./README.md)로 돌아가기

---

## Workspace 접속 (`/w/{workspace_id}/`)

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane
    participant IC as Instance Controller
    participant D as Docker

    U->>CP: GET /w/{workspace_id}/
    CP->>CP: 세션 확인 (401 if fail)
    CP->>CP: owner 인가 확인 (403 if fail)
    CP->>IC: ResolveUpstream
    IC-->>CP: {host, port}

    alt upstream 연결 성공
        CP->>D: 프록시 연결 (WebSocket 포함)
        D-->>U: code-server 응답
    end

    alt upstream 연결 실패
        CP-->>U: 502 UPSTREAM_UNAVAILABLE
    end
```

> 프록시에서 상태 확인 안 함. 사용자는 대시보드(API)에서 start 후 접속.

---

## Trailing Slash 규칙

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane

    U->>CP: GET /w/{workspace_id}
    CP-->>U: 308 Redirect
    U->>CP: GET /w/{workspace_id}/
    CP->>CP: prefix strip → upstream /
```

---

## StartWorkspace (`POST /api/v1/workspaces/{id}:start`)

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane
    participant SP as Storage Provider
    participant IC as Instance Controller
    participant D as Docker

    U->>CP: POST /api/v1/workspaces/{id}:start
    CP->>CP: 상태 확인 (CREATED/STOPPED/ERROR)
    CP->>CP: DB 상태 → PROVISIONING
    CP-->>U: {id, status: "PROVISIONING"}

    Note over CP: 이후 백그라운드에서 진행

    CP->>SP: Provision(home_store_key, existing_ctx)

    alt existing_ctx 존재
        SP->>SP: 기존 ctx 정리 (내부)
    end

    SP-->>CP: {home_mount, home_ctx}
    CP->>CP: DB에 home_ctx 저장

    CP->>IC: StartWorkspace(workspace_id, image_ref, home_mount)
    IC->>D: docker create + start
    D-->>IC: container started

    loop GetStatus 폴링
        CP->>IC: GetStatus
        IC->>D: health probe
        D-->>IC: status
        IC-->>CP: {exists, running, healthy, port?}
    end

    CP->>CP: DB 상태 → RUNNING
```

> API는 PROVISIONING 상태를 즉시 반환. 클라이언트는 폴링으로 최종 상태 확인.
> Control Plane이 Storage Provider.Provision 호출 → home_mount 획득 → Instance Controller에 전달
> existing_ctx가 있으면 Provision 내부에서 자동 정리 (리소스 누수 방지)

---

## StopWorkspace (`POST /api/v1/workspaces/{id}:stop`)

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane
    participant SP as Storage Provider
    participant IC as Instance Controller
    participant D as Docker

    U->>CP: POST /api/v1/workspaces/{id}:stop
    CP->>CP: 상태 확인 (RUNNING/ERROR)
    CP->>CP: DB 상태 → STOPPING
    CP-->>U: {id, status: "STOPPING"}

    Note over CP: 이후 백그라운드에서 진행

    CP->>IC: StopWorkspace(workspace_id)
    IC->>D: docker stop
    D-->>IC: container stopped

    CP->>SP: Deprovision(home_ctx)

    alt object-store
        SP->>SP: PersistHome (내부)
        SP->>SP: CleanupHome (내부)
    end

    Note over SP: local-dir: no-op

    CP->>CP: DB home_ctx = NULL
    CP->>CP: DB 상태 → STOPPED
```

> API는 STOPPING 상태를 즉시 반환. 클라이언트는 폴링으로 최종 상태 확인.
> 백엔드 분기 없이 항상 Deprovision 호출. 백엔드 내부에서 적절히 처리.

---

## DeleteWorkspace (`DELETE /api/v1/workspaces/{id}`)

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane
    participant SP as Storage Provider
    participant IC as Instance Controller
    participant D as Docker

    U->>CP: DELETE /api/v1/workspaces/{id}
    CP->>CP: 상태 확인 (CREATED/STOPPED/ERROR)
    CP->>CP: DB 상태 → DELETING
    CP-->>U: 204 No Content

    Note over CP: 이후 백그라운드에서 진행

    CP->>IC: DeleteWorkspace(workspace_id)

    alt 컨테이너 존재
        IC->>D: docker rm -f
        D-->>IC: container removed
    end

    Note over IC: 컨테이너 없으면 성공 (no-op)

    IC-->>CP: success

    alt home_ctx 존재
        CP->>SP: Deprovision(home_ctx)
        SP-->>CP: success
        CP->>CP: DB home_ctx = NULL
    end

    CP->>CP: DB soft delete (deleted_at, status=DELETED)
```

> API는 DELETING 상태 전환 후 즉시 204 반환. 클라이언트는 폴링으로 최종 상태 확인.
> 컨테이너 삭제 후 스토리지 해제 (생성의 역순)
> MVP에서는 Home Store 데이터 삭제 안 함 (Purge 호출 X)
