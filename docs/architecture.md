# Architecture

> 용어 정의는 [glossary.md](./glossary.md), 상세 스펙은 [spec.md](./spec.md) 참조

---

## 1. 시스템 개요

```mermaid
graph TB
    User[사용자]

    subgraph ControlPlane[Control Plane]
        API["/api/v1/*"]
        Proxy["/w/{workspace_id}/*"]
        DB[(Database)]
    end

    subgraph StorageProvider[Storage Provider]
        SP[local-dir / object-store]
    end

    subgraph InstanceController[Instance Controller - Local Docker]
        IC[Lifecycle Manager]
    end

    subgraph Infrastructure
        Docker[Docker Engine]
        HomeStore[Home Store<br/>host directory]
    end

    User --> API
    User --> Proxy
    API --> DB
    Proxy --> DB
    Proxy --> Docker
    API --> SP
    SP --> HomeStore
    API --> IC
    IC --> Docker
    Docker --> HomeStore
```

---

## 2. 요청 흐름

### Workspace 접속 (`/w/{workspace_id}/`)

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

### Trailing Slash 규칙

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane

    U->>CP: GET /w/{workspace_id}
    CP-->>U: 308 Redirect
    U->>CP: GET /w/{workspace_id}/
    CP->>CP: prefix strip → upstream /
```

### StartWorkspace (`POST /api/v1/workspaces/{id}:start`)

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

### StopWorkspace (`POST /api/v1/workspaces/{id}:stop`)

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

### DeleteWorkspace (`DELETE /api/v1/workspaces/{id}`)

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
    CP-->>U: 204 No Content
```

> 컨테이너 삭제 후 스토리지 해제 (생성의 역순)
> MVP에서는 Home Store 데이터 삭제 안 함 (Purge 호출 X)

---

## 3. Workspace 상태

```mermaid
stateDiagram-v2
    [*] --> CREATED: CreateWorkspace

    CREATED --> PROVISIONING: start
    STOPPED --> PROVISIONING: start

    PROVISIONING --> RUNNING: healthy
    PROVISIONING --> ERROR: timeout/fail

    RUNNING --> STOPPING: stop
    RUNNING --> ERROR: 인프라 오류

    STOPPING --> STOPPED: success
    STOPPING --> ERROR: fail

    CREATED --> DELETING: delete
    STOPPED --> DELETING: delete
    ERROR --> DELETING: delete

    DELETING --> DELETED: success
    DELETING --> ERROR: fail

    ERROR --> PROVISIONING: start (재시도)
    ERROR --> STOPPING: stop (재시도)

    DELETED --> [*]
```

> 프록시는 상태 확인 없이 바로 연결 시도. 컨테이너 미실행 시 502 에러. 사용자는 대시보드에서 Start API 호출 후 접속.

### 상태 × 액션 매트릭스

| 현재 상태 | Start | Stop | Delete | 프록시 접속 |
|-----------|-------|------|--------|------------|
| CREATED | → PROVISIONING | 409 | → DELETING | 502 |
| PROVISIONING | 409 | 409 | 409 | 502 |
| RUNNING | 409 | → STOPPING | 409 | ✓ 연결 |
| STOPPING | 409 | 409 | 409 | 502 |
| STOPPED | → PROVISIONING | 409 | → DELETING | 502 |
| DELETING | 409 | 409 | 409 | 502 |
| ERROR | → PROVISIONING | → STOPPING | → DELETING | 502 |
| DELETED | 404 | 404 | 404 | 404 |

> 409 = INVALID_STATE, 404 = WORKSPACE_NOT_FOUND, 502 = UPSTREAM_UNAVAILABLE

---

## 4. 컴포넌트 구조

```mermaid
graph LR
    subgraph ControlPlane[Control Plane]
        direction TB
        Auth[Auth Middleware]
        API[API Server]
        ProxyGW[Proxy Gateway]

        Auth --> API
        Auth --> ProxyGW
    end

    subgraph StorageProvider[Storage Provider]
        direction TB
        subgraph StorageBackends[Backends]
            LocalDir[local-dir]
            ObjectStore[object-store<br/>추후]
        end
    end

    subgraph InstanceController[Instance Controller]
        direction TB
        LM[Lifecycle Manager]

        subgraph InstanceBackends[Backends]
            LocalDocker[local-docker]
            K8s[k8s<br/>추후]
        end

        LM --> LocalDocker
    end

    ControlPlane --> StorageProvider
    ControlPlane --> InstanceController
```

> Instance Controller는 컨테이너 lifecycle 담당 (시작 시 home_mount를 /home/coder에 마운트), Storage Provider는 스토리지 프로비저닝 담당

---

## 5. 데이터 흐름

```mermaid
graph LR
    subgraph Request[요청]
        U[User]
    end

    subgraph ControlPlane[Control Plane]
        CP[API / Proxy]
    end

    subgraph Storage[저장소]
        DB[(Database)]
        HS[Home Store]
    end

    subgraph Runtime[런타임]
        WI[Workspace Instance<br/>code-server]
    end

    U -->|API 호출| CP
    U -->|/w/ 접속| CP
    CP <-->|메타데이터| DB
    CP -->|프록시| WI
    WI <-->|/home/coder| HS
```

> User는 항상 Control Plane을 통해 접근. API 호출 시 DB 조회/수정, 프록시 접속 시 Workspace Instance로 연결.

---

## 6. Startup Recovery (MVP)

서버 크래시로 인한 stuck 상태를 서버 시작 시 자동 복구합니다.

### 동작 흐름

```mermaid
sequenceDiagram
    participant S as Server Process
    participant SR as Startup Recovery
    participant DB as Database
    participant IC as Instance Controller

    S->>SR: 서버 시작
    SR->>DB: 전이 상태 조회
    DB-->>SR: [PROVISIONING, STOPPING, DELETING]

    loop 각 stuck 워크스페이스
        SR->>IC: GetStatus(workspace_id)
        IC-->>SR: {exists, running, healthy}
        SR->>SR: 복구 상태 결정
        SR->>DB: 상태 업데이트
    end

    SR-->>S: 복구 완료
    S->>S: HTTP 서버 시작
```

### 복구 매트릭스

| DB 상태 | Instance 상태 | 복구 결과 |
|---------|--------------|----------|
| PROVISIONING | running + healthy | RUNNING |
| PROVISIONING | 그 외 | ERROR |
| STOPPING | not running | STOPPED |
| STOPPING | running | RUNNING |
| DELETING | not exists | DELETED |
| DELETING | exists | ERROR |

> ⚠️ Startup Recovery는 상태 전이가 아닌 "DB 보정"입니다. 서버 크래시로 인해 DB 상태와 실제 컨테이너 상태가 불일치할 때, DB를 현실에 맞춰 수정합니다. 따라서 상태 다이어그램에는 표현되지 않습니다.

> MVP에서는 Startup Recovery로 크래시 복구. 프로덕션 규모에서 주기적 복구가 필요하면 Reconciler 도입.

---

## 7. (추후) Reconciler 패턴 도입

현재는 명령적(Imperative) 방식으로 동작하지만, GetStatus 메서드를 통해 Reconciler 패턴으로 확장 가능하도록 설계되어 있습니다.

### 현재 방식 (명령적)

```
API 호출 → Storage Provider.Provision → Instance Controller.Start → 완료
```

- Control Plane이 순차적으로 호출
- 중간 실패 시 부분 완료 상태 발생 가능
- 롤백 로직이 복잡해질 수 있음

### Reconciler 방식 (선언적)

```mermaid
graph LR
    subgraph Reconciler[Reconciler Loop]
        GS[GetStatus 호출]
        CMP[현재 vs 원하는<br/>상태 비교]
        ACT[조정 액션]
    end

    GS --> CMP
    CMP -->|차이 있음| ACT
    ACT --> GS
    CMP -->|일치| GS
```

**Reconciler 도입 시:**
1. 백그라운드 워커가 주기적으로 모든 워크스페이스 순회
2. `GetStatus`로 현재 상태 조회 (Storage Provider, Instance Controller)
3. DB의 desired_status와 비교
4. 차이 있으면 기존 메서드(Provision/Start 등)로 조정
5. 상태 불일치 자동 복구

### 설계 원칙

| 원칙 | 설명 |
|------|------|
| **멱등성** | 모든 조정 메서드는 여러 번 호출해도 안전 |
| **상태 조회 분리** | GetStatus는 부수효과 없이 현재 상태만 반환 |
| **점진적 확장** | MVP는 명령적, 추후 Reconciler로 감싸기 가능 |

> MVP에서는 명령적 방식으로 충분하며, 프로덕션 규모에서 상태 불일치 문제가 발생하면 Reconciler 도입 검토
