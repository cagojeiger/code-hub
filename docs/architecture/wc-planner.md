# WC Planner

> operation 결정 및 archive_op_id 관리

---

## 개요

WC Planner는 순수 함수로 operation을 결정합니다.

| 입력 | 출력 |
|------|------|
| PlanInput (phase, operation, desired_state, conditions, ...) | PlanAction (operation, phase, archive_op_id, ...) |

---

## Plan 로직

```mermaid
flowchart TB
    START["plan(input)"]
    JUDGE["judge() 호출<br/>→ JudgeOutput"]

    subgraph Case1["Case 1: 진행 중"]
        OP{"operation != NONE?"}
        COMP{"완료 조건?"}
        TIMEOUT{"timeout?"}
        COMPLETE["operation=NONE<br/>complete=True"]
        ERR_TIMEOUT["phase=ERROR<br/>reason=TIMEOUT"]
        RETRY["operation 유지<br/>archive_op_id 유지"]
    end

    subgraph Case2["Case 2: ERROR"]
        IS_ERR{"phase == ERROR?"}
        WANT_DEL{"desired == DELETED?"}
        DELETING["operation=DELETING"]
        WAIT["operation=NONE<br/>phase=ERROR"]
    end

    subgraph Case3["Case 3: 수렴됨"]
        CONV{"phase == target?"}
        NOOP["operation=NONE"]
    end

    subgraph Case4["Case 4: operation 선택"]
        SELECT["_select_operation()"]
        NEW_OP["operation 시작<br/>ARCHIVING/CREATE_EMPTY만<br/>archive_op_id=uuid4()"]
    end

    START --> JUDGE --> OP
    OP -->|Yes| COMP
    COMP -->|Yes| COMPLETE
    COMP -->|No| TIMEOUT
    TIMEOUT -->|Yes| ERR_TIMEOUT
    TIMEOUT -->|No| RETRY
    OP -->|No| IS_ERR
    IS_ERR -->|Yes| WANT_DEL
    WANT_DEL -->|Yes| DELETING
    WANT_DEL -->|No| WAIT
    IS_ERR -->|No| CONV
    CONV -->|Yes| NOOP
    CONV -->|No| SELECT --> NEW_OP
```

---

## archive_op_id 생성 vs 사용

```mermaid
flowchart TB
    subgraph Plan["plan() - archive_op_id 생성"]
        P1["Case 1 재시도 (ARCHIVING):<br/>archive_op_id = input.archive_op_id"]
        P3["Case 4 ARCHIVING/CREATE_EMPTY:<br/>archive_op_id = uuid4()"]
        P_NONE["그 외: archive_op_id = None"]
    end

    subgraph Execute["_execute() - archive_op_id 사용"]
        EX_ARC["ARCHIVING / CREATE_EMPTY"]
        EX_ARC_OP["archive_op_id = action.archive_op_id<br/>or ws.archive_op_id<br/>or uuid4()"]
        EX_ARC_USE["→ S3 경로로 사용!"]

        EX_REST["RESTORING"]
        EX_REST_USE["→ ws.archive_key 사용<br/>(archive_op_id 무시)"]

        EX_OTHER["PROVISIONING/STARTING<br/>STOPPING/DELETING"]
        EX_OTHER_USE["→ archive_op_id 사용 안 함"]
    end

    subgraph Persist["_persist() - DB 저장"]
        PE1{"ARCHIVING/CREATE_EMPTY?"}
        PE1_Y["archive_op_id = action.archive_op_id"]
        PE2{"op=NONE?"}
        PE2_Y["archive_op_id = ws.archive_op_id (GC 보호)"]
        PE3["그 외: archive_op_id = ws.archive_op_id"]
    end

    EX_ARC --> EX_ARC_OP --> EX_ARC_USE
    EX_REST --> EX_REST_USE
    EX_OTHER --> EX_OTHER_USE

    PE1 -->|Yes| PE1_Y
    PE1 -->|No| PE2
    PE2 -->|Yes| PE2_Y
    PE2 -->|No| PE3
```

---

## archive_op_id 실제 사용 여부

| Operation | plan() 생성 | _execute() 사용 | 용도 |
|-----------|-------------|----------------|------|
| PROVISIONING | - | - | - |
| RESTORING | - | - (archive_key 사용) | - |
| STARTING | - | - | - |
| STOPPING | - | - | - |
| **ARCHIVING** | **uuid4()** | **S3 경로** | 멱등성 |
| **CREATE_EMPTY** | **uuid4()** | **S3 경로** | 멱등성 |
| DELETING | - | - | - |

> **핵심**: `archive_op_id`는 ARCHIVING/CREATE_EMPTY_ARCHIVE에서만 생성/사용됨

---

## archive_op_id 생명주기

```mermaid
sequenceDiagram
    participant Plan as plan()
    participant Persist as _persist()
    participant Execute as _execute()
    participant DB
    participant S3

    Note over Plan,S3: 1. 새 ARCHIVING 시작

    Plan->>Plan: archive_op_id = uuid4() = "abc-123"
    Plan-->>Persist: PlanAction(archive_op_id="abc-123")
    Persist->>DB: archive_op_id = "abc-123"<br/>operation = ARCHIVING
    Persist-->>Execute: ws.archive_op_id = "abc-123"
    Execute->>S3: archive("abc-123") →<br/>ws-123/abc-123/home.tar.zst

    Note over Plan,S3: 2. 진행 중 (재시도)

    Plan->>Plan: archive_op_id = input.archive_op_id = "abc-123"
    Plan-->>Persist: PlanAction(archive_op_id="abc-123")
    Persist->>DB: archive_op_id = "abc-123" (유지)
    Execute->>S3: archive("abc-123")<br/>→ HEAD 체크 → skip

    Note over Plan,S3: 3. 완료

    Plan->>Plan: action.complete = True
    Plan-->>Persist: PlanAction(op=NONE, complete=True)
    Persist->>DB: archive_op_id = ws.archive_op_id = "abc-123"<br/>operation = NONE<br/>archive_key = "..."

    Note over DB: archive_op_id 유지됨! (GC 보호)

    Note over Plan,S3: 4. 다음 ARCHIVING

    Plan->>Plan: archive_op_id = uuid4() = "def-456"
    Plan-->>Persist: PlanAction(archive_op_id="def-456")
    Persist->>DB: archive_op_id = "def-456" (덮어씀)
```

---

## archive_op_id 요약

| 시점 | archive_op_id 값 | 코드 위치 | 이유 |
|------|-----------------|----------|------|
| ARCHIVING/CREATE_EMPTY 시작 | `uuid4()` | wc_planner.py | 새 S3 경로 |
| 진행 중 (재시도) | 기존 값 | wc_planner.py | 멱등성 |
| **완료 시** | **기존 값** | wc.py | **GC 보호** |
| 다음 ARCHIVING | `uuid4()` | wc_planner.py | 새 S3 경로 |
| 다른 Operation | N/A | - | 사용 안 함 |

---

## 참조

- [wc.md](./wc.md) - WorkspaceController 전체
- [wc-judge.md](./wc-judge.md) - Judge 로직
- [gc-runner.md](./gc-runner.md) - GC 보호 로직
