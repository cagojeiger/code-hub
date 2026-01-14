# GC Runner

> orphan archive 정리

---

## 개요

GC Runner는 S3에서 orphan archive를 탐지하고 삭제합니다.

| 항목 | 설명 |
|------|------|
| 역할 | orphan archive 정리 |
| 주기 | 4시간 |
| 보호 대상 | archive_key + archive_op_id 경로 |

---

## 아키텍처

```mermaid
flowchart TB
    subgraph CP["Control Plane"]
        GC["GC Runner"]

        subgraph Query["DB 쿼리"]
            Q1["archive_keys<br/>(RESTORING 보호)"]
            Q2["(ws_id, archive_op_id)<br/>(ARCHIVING crash 보호)"]
        end
    end

    subgraph Agent["Agent"]
        API["POST /gc"]

        subgraph Calc["보호 키 계산"]
            C1["protected = set(archive_keys)"]
            C2["for ws_id, archive_op_id:<br/>  protected.add(<br/>    naming.s3_key(ws_id, archive_op_id)<br/>  )"]
        end

        subgraph Delete["삭제"]
            D1["all_keys = S3.list()"]
            D2["orphans = all - protected"]
            D3["S3.delete(orphans)"]
        end
    end

    GC --> Q1 & Q2
    Q1 & Q2 -->|"HTTP"| API
    API --> C1 --> C2 --> D1 --> D2 --> D3
```

---

## 두 가지 보호 유형

| 보호 대상 | 목적 | 시나리오 |
|----------|------|---------|
| `archive_key` | 실제 존재하는 아카이브 보호 | RESTORING 중 복원 대상 파일 |
| `archive_op_id` 경로 | ARCHIVING crash 대비 | archive → delete → crash → persist 안 됨 |

### 보호 로직

```python
# Control Plane (scheduler_gc.py)
# 1. archive_key 조회 (RESTORING 대상 보호)
archive_keys = SELECT archive_key FROM workspaces
               WHERE archive_key IS NOT NULL AND deleted_at IS NULL

# 2. (ws_id, archive_op_id) 조회 (ARCHIVING crash 대비)
protected_workspaces = SELECT id, archive_op_id FROM workspaces
                       WHERE archive_op_id IS NOT NULL AND deleted_at IS NULL

# Agent (storage.py)
# 보호 키 계산
protected_keys = set(archive_keys)
for ws_id, archive_op_id in protected_workspaces:
    protected_keys.add(naming.archive_s3_key(ws_id, archive_op_id))

# 삭제
all_keys = S3.list_objects(prefix)
orphans = all_keys - protected_keys
S3.delete_objects(orphans)
```

---

## 보호 시나리오

```mermaid
sequenceDiagram
    participant DB
    participant GC as GC Runner
    participant Agent
    participant S3

    Note over DB: RESTORING 시나리오
    Note over DB: archive_key = "ws-123/op-aaa/home.tar.zst"<br/>archive_op_id = "op-bbb" (새 작업)

    GC->>DB: SELECT archive_key, (id, archive_op_id)
    DB-->>GC: archive_keys: ["ws-123/op-aaa/..."]<br/>protected_ws: [(ws-123, op-bbb)]

    GC->>Agent: run_gc(archive_keys, protected_ws)
    Agent->>Agent: protected = {<br/>  "ws-123/op-aaa/...",<br/>  "ws-123/op-bbb/..."<br/>}
    Agent->>S3: list_objects()
    S3-->>Agent: all keys
    Agent->>Agent: orphans = all - protected
    Agent->>S3: delete(orphans)
```

### 시나리오별 보호

| 시나리오 | DB 상태 | 보호 키 |
|---------|--------|---------|
| RESTORING | archive_key="ws/op-aaa/...", archive_op_id="op-bbb" | archive_key 값 + archive_op_id 경로 |
| ARCHIVING 완료 | archive_key="ws/op-ccc/...", archive_op_id="op-ccc" | 둘 다 같은 경로 |
| ARCHIVING crash | archive_key=NULL, archive_op_id="op-ddd" | archive_op_id 경로만 |

---

## 참조

- [00-contracts.md](../spec/00-contracts.md#9-gc-separation--protection) - GC 계약
- [05-data-plane.md](../spec/05-data-plane.md#gc-runner) - GC Runner 스펙
