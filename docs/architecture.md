# Architecture

> 용어 정의는 [glossary.md](./glossary.md), 상세 스펙은 [spec.md](./spec.md) 참조

---

## 1. 시스템 개요

```mermaid
graph TB
    User[사용자]

    subgraph ControlPlane[Control Plane]
        API["/api/v1/*"]
        Proxy["/w/{workspace_id}/"]
        DB[(Database)]
    end

    subgraph Runner[Runner - Local Docker]
        Lifecycle[Lifecycle Manager]
        HSB[HomeStoreBackend]
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
    API --> Lifecycle
    Lifecycle --> Docker
    Lifecycle --> HSB
    HSB --> HomeStore
    Docker --> HomeStore
```

---

## 2. 요청 흐름

### Workspace 접속 (`/w/{workspace_id}/`)

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane
    participant R as Runner
    participant D as Docker

    U->>CP: GET /w/{workspace_id}/
    CP->>CP: 세션 확인 (401 if fail)
    CP->>CP: owner 인가 확인 (403 if fail)
    CP->>R: ResolveUpstream
    R-->>CP: {host, port}

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

    DELETED --> [*]
```

> 프록시는 상태 확인 없이 바로 연결 시도. 컨테이너 미실행 시 502 에러. 사용자는 대시보드에서 Start API 호출 후 접속.

---

## 4. 컴포넌트 구조

```mermaid
graph LR
    subgraph ControlPlane[Control Plane]
        direction TB
        API[API Server]
        Auth[Auth Middleware]
        ProxyGW[Proxy Gateway]

        API --> Auth
        ProxyGW --> Auth
    end

    subgraph Runner[Runner]
        direction TB
        LM[Lifecycle Manager]

        subgraph Backends[Backends]
            LocalDocker[local-docker]
        end

        subgraph HomeStoreBackends[HomeStore Backends]
            LocalDir[local-dir]
            ObjectStore[object-store<br/>추후]
        end

        LM --> LocalDocker
        LM --> LocalDir
    end

    ControlPlane --> Runner
```

---

## 5. 데이터 흐름

```mermaid
graph LR
    subgraph Request[요청]
        U[User]
    end

    subgraph Storage[저장소]
        DB[(Database)]
        HS[Home Store]
    end

    subgraph Runtime[런타임]
        WI[Workspace Instance<br/>code-server]
    end

    U -->|API 호출| DB
    U -->|/w/ 접속| WI
    WI -->|/home/coder| HS
    DB -->|메타데이터| WI
```
