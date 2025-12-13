# Architecture

> 용어 정의는 [glossary.md](./glossary.md), 상세 스펙은 [spec.md](./spec.md) 참조

---

## 1. 시스템 개요

```mermaid
graph TB
    User[사용자]

    subgraph ControlPlane[Control Plane]
        API["/api/v1/*"]
        Proxy["/w/{workspace_id}"]
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
    Proxy --> Docker
    API --> Lifecycle
    Lifecycle --> Docker
    Lifecycle --> HSB
    HSB --> HomeStore
    Docker --> HomeStore
```

---

## 2. 요청 흐름

### Workspace 접속 (`/w/{workspace_id}`)

```mermaid
sequenceDiagram
    participant U as User
    participant CP as Control Plane
    participant R as Runner
    participant HS as HomeStore
    participant D as Docker

    U->>CP: GET /w/{workspace_id}
    CP->>CP: 세션 확인
    CP->>CP: owner 인가 확인

    alt STOPPED 상태
        CP->>R: StartWorkspace
        R->>HS: PrepareHome
        HS-->>R: home_mount
        R->>D: 컨테이너 생성
        D-->>R: container_id
        R-->>CP: RUNNING
    end

    CP->>R: ResolveUpstream
    R-->>CP: {host, port}
    CP->>D: 프록시 연결 (WebSocket 포함)
    D-->>U: code-server 응답
```

---

## 3. Workspace 상태

```mermaid
stateDiagram-v2
    [*] --> STOPPED: CreateWorkspace

    STOPPED --> RUNNING: start
    RUNNING --> STOPPED: stop

    RUNNING --> ERROR: 오류 발생
    ERROR --> STOPPED: stop
    ERROR --> RUNNING: start (재시도)

    STOPPED --> DELETED: delete
    ERROR --> DELETED: delete

    DELETED --> [*]
```

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
