# Architecture Overview

> Control Plane + Data Plane 전체 아키텍처

---

## 전체 구조

```mermaid
flowchart TB
    subgraph CP["Control Plane (Coordinator)"]
        OB["Observer<br/>(1s/15s)"]
        WC["WC<br/>(1s/15s)"]
        TTL["TTL Runner<br/>(60s)"]
        GC["GC Runner<br/>(4h)"]
    end

    subgraph DB["PostgreSQL"]
        WS["workspaces<br/>conditions, phase,<br/>operation, archive_key..."]
    end

    subgraph Agent["Agent (Data Plane)"]
        API["FastAPI<br/>/api/v1/workspaces"]
        RT["Docker Runtime"]
        IC["Instances<br/>(Container)"]
        VOL["Volumes"]
        STG["Storage<br/>(S3)"]
    end

    subgraph Infra["Infrastructure"]
        DC["Docker<br/>Containers"]
        DV["Docker<br/>Volumes"]
        S3["S3 Bucket"]
    end

    OB -->|"observe()"| API
    WC -->|"start/stop/archive/restore"| API
    GC -->|"run_gc()"| API

    OB -->|"conditions 저장"| DB
    WC -->|"phase, operation 저장"| DB
    TTL -->|"desired_state 변경"| DB
    GC -->|"보호 목록 조회"| DB

    API --> RT
    RT --> IC & VOL & STG
    IC --> DC
    VOL --> DV
    STG --> S3
```

---

## 컴포넌트 역할

| 컴포넌트 | 역할 | 주기 |
|----------|------|------|
| **Observer** | 리소스 관측 → conditions DB 저장 | 1s/15s |
| **WC** | phase 계산 + operation 실행 | 1s/15s |
| **TTL Runner** | 비활성 워크스페이스 상태 전환 | 60s |
| **GC Runner** | orphan archive 정리 | 4h |
| **Agent** | Docker/S3 실제 작업 수행 | on-demand |

---

## 컬럼 소유권 (Single Writer)

| Coordinator | 소유 컬럼 |
|-------------|----------|
| **Observer** | conditions, observed_at |
| **WC** | phase, operation, op_started_at, archive_op_id, archive_key, error_count, error_reason, home_ctx |
| **TTL Runner** | last_access_at (sync), desired_state (TTL 만료 시) |
| **API** | desired_state (사용자 요청) |

---

## Observer + WC 분리

```mermaid
flowchart TB
    subgraph Observer["Observer Coordinator"]
        OB_LIST["Agent.observe()<br/>list_all()"]
        OB_COND["conditions 구성"]
        OB_SAVE["DB 저장<br/>(conditions, observed_at)"]

        OB_LIST --> OB_COND --> OB_SAVE
    end

    subgraph WC["Workspace Controller"]
        WC_READ["DB에서 conditions 읽기"]
        WC_JUDGE["Judge:<br/>calculate_phase()"]
        WC_CTRL["Control:<br/>Plan → Execute"]
        WC_SAVE["DB 저장<br/>(phase, operation, ...)"]

        WC_READ --> WC_JUDGE --> WC_CTRL --> WC_SAVE
    end

    subgraph Agent["Agent"]
        API["REST API"]
        RT["Docker Runtime"]
    end

    OB_LIST -->|"HTTP"| API
    WC_CTRL -->|"HTTP"| API

    OB_SAVE -.->|"DB"| WC_READ

    style Observer fill:#e1f5fe
    style WC fill:#fff3e0
```

---

## 참조

- [wc.md](./wc.md) - WorkspaceController 상세
- [wc-observer.md](./wc-observer.md) - Observer Coordinator
- [ttl-manager.md](./ttl-manager.md) - TTL Runner
- [gc-runner.md](./gc-runner.md) - GC Runner
