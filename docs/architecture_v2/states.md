# Workspace 상태 아키텍처 (M2)

> [README.md](./README.md)로 돌아가기

---

## 상태 모델 개요

M2는 **Ordered State Machine** 패턴을 사용합니다.

```
레벨:    0         10        20        30
       PENDING → COLD → WARM → RUNNING
               ←      ←      ←
```

> 상세 스펙은 [spec_v2/states.md](../spec_v2/states.md) 참조

---

## 상태별 리소스

```mermaid
flowchart TB
    subgraph PENDING["PENDING (Level 0)"]
        P_DB[(DB Record)]
    end

    subgraph COLD["COLD (Level 10)"]
        C_DB[(DB Record)]
        C_OS[Object Storage]
    end

    subgraph WARM["WARM (Level 20)"]
        W_DB[(DB Record)]
        W_V[Docker Volume]
    end

    subgraph RUNNING["RUNNING (Level 30)"]
        R_DB[(DB Record)]
        R_V[Docker Volume]
        R_C[Container]
    end

    PENDING -->|step_up| COLD
    COLD -->|step_up| WARM
    WARM -->|step_up| RUNNING

    RUNNING -->|step_down| WARM
    WARM -->|step_down| COLD
    COLD -->|step_down| PENDING
```

---

## 전체 상태 다이어그램

```mermaid
stateDiagram-v2
    direction LR

    [*] --> PENDING: CreateWorkspace

    state "정상 흐름" as normal {
        PENDING --> INITIALIZING: step_up
        INITIALIZING --> COLD: done

        COLD --> RESTORING: step_up
        RESTORING --> WARM: done

        WARM --> STARTING: step_up
        STARTING --> RUNNING: done

        RUNNING --> STOPPING: step_down
        STOPPING --> WARM: done

        WARM --> ARCHIVING: step_down
        ARCHIVING --> COLD: done
    }

    state "삭제 흐름" as delete_flow {
        DELETING --> DELETED: done
        DELETED --> [*]
    }

    PENDING --> DELETING: delete
    COLD --> DELETING: delete
    WARM --> DELETING: delete
    RUNNING --> DELETING: delete

    state "에러 흐름" as error_flow {
        ERROR
    }

    INITIALIZING --> ERROR: fail
    RESTORING --> ERROR: fail
    STARTING --> ERROR: fail
    STOPPING --> ERROR: fail
    ARCHIVING --> ERROR: fail
    DELETING --> ERROR: fail

    ERROR --> INITIALIZING: retry
    ERROR --> RESTORING: retry
    ERROR --> STARTING: retry
    ERROR --> STOPPING: retry
    ERROR --> ARCHIVING: retry
```

---

## 상태 전환 규칙

### step_up (활성화 방향)

| 현재 | 전이 상태 | 다음 | 동작 |
|------|-----------|------|------|
| PENDING | INITIALIZING | COLD | 메타데이터 초기화 |
| COLD | RESTORING | WARM | archive_key → Volume |
| WARM | STARTING | RUNNING | Container 시작 |

### step_down (비활성화 방향)

| 현재 | 전이 상태 | 다음 | 동작 |
|------|-----------|------|------|
| RUNNING | STOPPING | WARM | Container 정지 |
| WARM | ARCHIVING | COLD | Volume → Object Storage |
| COLD | - | PENDING | (일반적으로 미사용) |

---

## TTL 기반 자동 전환

```mermaid
flowchart LR
    R[RUNNING]
    W[WARM]
    C[COLD]

    R -->|"last_access + warm_ttl 경과"| W
    W -->|"last_access + cold_ttl 경과"| C
```

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| warm_ttl_seconds | 1800 (30분) | RUNNING → WARM |
| cold_ttl_seconds | 604800 (7일) | WARM → COLD |

---

## Reconciler 수렴 동작

```mermaid
flowchart TD
    A[Workspace 조회] --> B{status == desired_state?}
    B -->|Yes| C[완료]
    B -->|No| D{status.level < desired_state.level?}
    D -->|Yes| E[step_up]
    D -->|No| F[step_down]
    E --> G[status 갱신]
    F --> G
    G --> B
```

### 예시: COLD → RUNNING

```
desired_state = RUNNING (Level 30)
status = COLD (Level 10)

1. COLD < RUNNING → step_up
2. COLD → RESTORING → WARM (Level 20)
3. WARM < RUNNING → step_up
4. WARM → STARTING → RUNNING (Level 30)
5. RUNNING == RUNNING → 완료
```

---

## 참조

- [ADR-008: Ordered State Machine](../adr/008-ordered-state-machine.md)
- [spec_v2/states.md](../spec_v2/states.md)
- [reconciler.md](./reconciler.md)
