# Observer Coordinator

> 리소스 관측 → conditions DB 저장
>
> **관련**: [wc.md](./wc.md) (WorkspaceController)

---

## 개요

Observer Coordinator는 **별도 Coordinator**로, 리소스(Container, Volume, Archive)를 벌크 관측하여 DB에 저장합니다.

| 역할 | 입력 | 출력 |
|------|------|------|
| **Observe** | IC, SP | conditions |
| **Persist** | conditions | DB (workspaces.conditions) |

> **Single Writer**: Observer만 conditions, observed_at 소유

---

## 아키텍처

```mermaid
flowchart TB
    subgraph OBS["Observer Coordinator (별도)"]
        OBSERVE["WorkspaceRuntime.observe()"]
        COND["conditions 구성"]
        SAVE["DB 저장<br/>(conditions, observed_at)"]

        OBSERVE --> COND --> SAVE
    end

    subgraph WC["WC (별도)"]
        READ["DB에서 conditions 읽기"]
        JUDGE["Judge: calculate_phase()"]
        CTRL["Control: Plan → Execute"]

        READ --> JUDGE
        JUDGE --> CTRL
    end

    SAVE -.->|DB| READ
```

> **Level-Triggered**: Observer가 리소스 관측 → DB 저장, WC는 DB만 읽음

---

## 소유 컬럼 (Single Writer)

| Coordinator | 소유 컬럼 |
|-------------|----------|
| **Observer** | conditions, observed_at |
| **WC** | phase, operation, op_started_at, archive_op_id, archive_key, error_count, error_reason, home_ctx |

---

## 성능

| 지표 | Before (개별) | After (벌크) |
|------|--------------|-------------|
| API 호출 | N회 | 3회 |
| 시간 (100 ws) | ~21s | ~500ms |
| 개선율 | - | **~40배** |

---

## 주기

| 모드 | 주기 | 조건 |
|------|------|------|
| Idle | 15s | (`COORDINATOR_IDLE_INTERVAL`) |
| Active | 1s | (`COORDINATOR_ACTIVE_INTERVAL`) |
| Hint | 즉시 | Redis `codehub:wake:ob` 수신 |

---

## Hint 가속화

| Channel | Publisher | Subscriber | 동작 |
|---------|-----------|------------|------|
| `codehub:wake:ob` | EventListener | Observer | 즉시 관측 |
| `codehub:wake:wc` | EventListener, Observer | WC | conditions 저장 후 WC 깨움 |

---

## 인터페이스

### WorkspaceRuntime

| 메서드 | 반환 | 비고 |
|--------|------|------|
| `observe()` | `list[WorkspaceState]` | 벌크 리소스 관측 (Container + Volume + Archive 통합) |

> **통합 API**: Agent가 Container, Volume, Archive 상태를 단일 API로 반환

---

## 참조

- [wc.md](./wc.md) - WC 전체 설계
- [00-contracts.md](../spec/00-contracts.md) - 계약 #1 (Reality vs DB), #3 (Single Writer)
- [04-control-plane.md](../spec/04-control-plane.md) - Coordinator 정의
