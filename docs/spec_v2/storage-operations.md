# Storage Operations - 플로우 (M2)

> [storage.md](./storage.md)로 돌아가기

---

## op_id 정책

| Operation | op_id 필요 | 이유 |
|-----------|-----------|------|
| RESTORING | ❌ | 기존 archive_key 사용, 새 경로 생성 안 함 |
| ARCHIVING | ✅ | archive_key 경로 생성에 필요 (`archives/{id}/{op_id}/...`) |
| DELETING | ❌ | Volume만 삭제, Archive는 GC가 처리 |

### op_id 생성/조회

| 시점 | op_id 상태 | 동작 |
|------|-----------|------|
| 첫 시도 | NULL | 생성 후 DB 저장 |
| 재시도 | NOT NULL | 기존 값 사용 (DB에서 조회) |

> **핵심**: op_id는 archive 호출 전에 DB에 먼저 저장됨.
> 크래시 후 재시도 시 같은 op_id로 같은 경로에 업로드.

---

## RESTORING (COLD → WARM)

Object Storage에서 Volume으로 데이터 복원.

### 전제 조건
- `status = COLD, operation = RESTORING`

> **참고**: PENDING → COLD (INITIALIZING)는 메타데이터만 초기화하며, Storage 작업이 없습니다.

### 분기

| archive_key | Object Storage 파일 | 동작 |
|-------------|-------------------|------|
| 있음 | 있음 | `provision(workspace_id)` → `restore(workspace_id, archive_key)` |
| 있음 | 없음 | ERROR (`ARCHIVE_NOT_FOUND`) - 관리자 개입 필요 |
| 없음 | - | `provision(workspace_id)` - 빈 Volume 생성 |

> **참고**: provision은 멱등 (Volume 있으면 무시). ARCHIVING에서 Volume 삭제 후 복원할 때도 안전.

### 동작 (첫 시도)

```mermaid
sequenceDiagram
    participant DB as Database
    participant R as Reconciler
    participant S as StorageProvider

    Note over DB,R: 시작: status=COLD, operation=RESTORING
    R->>S: provision(workspace_id)
    Note over S: Volume 생성 (멱등: 이미 있으면 무시)
    S-->>R: success

    alt archive_key 있음
        R->>S: restore(workspace_id, archive_key)
        Note over S: Job 실행 (Volume: ws_{workspace_id}_home)
        S-->>R: success
    else archive_key 없음
        Note over R: 빈 Volume 사용 (restore 호출 안 함)
    end

    R->>DB: status = WARM, operation = NONE
    Note over DB,R: 완료: status=WARM, operation=NONE
```

> **Job 내부 동작**: [storage-job.md](./storage-job.md#restore-job) 참조

### 동작 (재시도)

```mermaid
sequenceDiagram
    participant DB as Database
    participant R as Reconciler
    participant S as StorageProvider

    Note over DB,R: 크래시 후 재시도
    R->>S: provision(workspace_id)
    Note over S: Volume 생성 (멱등: 이미 있으면 무시)
    S-->>R: success

    alt archive_key 있음
        R->>S: restore(workspace_id, archive_key)
        Note over S: Crash-Only 설계 → 항상 처음부터 재실행
        S-->>R: success (멱등)
    else archive_key 없음
        Note over R: 빈 Volume 사용 (restore 호출 안 함)
    end
```

### 실패 처리

| 에러 코드 | 상황 | 복구 방법 |
|----------|------|----------|
| `ARCHIVE_NOT_FOUND` | archive_key 있으나 Object Storage에 파일 없음 | 관리자 개입: archive_key NULL 처리 또는 백업 복원 |
| `S3_ACCESS_ERROR` | Object Storage 접근 실패 | 자동 재시도 |
| `CHECKSUM_MISMATCH` | sha256 불일치 | 관리자 개입 |
| `TAR_EXTRACT_FAILED` | tar.gz 해제 실패 | 관리자 개입 |

---

## ARCHIVING (WARM → COLD)

Volume을 Object Storage로 아카이브.

### 전제 조건
- `status = WARM, operation = ARCHIVING`
- 컨테이너가 정지된 상태 (RUNNING이 아님)

### 핵심 규칙

```
1. 업로드 (Volume 삭제 X)
2. DB에 archive_key 저장
3. Volume 삭제 → 최종 커밋
```

> **순서 중요**: archive_key DB 저장 → Volume 삭제. 크래시 시 Volume은 orphan으로 남지만 데이터는 안전.

### 동작

```mermaid
sequenceDiagram
    participant DB as Database
    participant R as Reconciler
    participant S as StorageProvider

    Note over DB,R: 시작: status=WARM, operation=ARCHIVING
    alt op_id가 NULL (첫 시도)
        R->>DB: op_id = uuid()
    end

    alt archive_key 없음 (업로드 필요)
        R->>S: archive(workspace_id, op_id)
        Note over S: Job 실행 (HEAD 체크 → 있으면 skip)
        S-->>R: archive_key
        R->>DB: archive_key = ...
    else archive_key 있음 (이미 업로드됨)
        Note over R: 업로드 skip
    end

    R->>S: delete_volume(workspace_id)
    S-->>R: success

    R->>DB: status=COLD, operation=NONE, op_id=NULL
    Note over DB,R: 완료: status=COLD, operation=NONE
```

> **Job 내부 동작**: [storage-job.md](./storage-job.md#archive-job) 참조

### 크래시 복구

| 크래시 시점 | DB 상태 | 재시도 동작 |
|------------|---------|------------|
| 업로드 중 | archive_key=NULL, op_id 있음 | 같은 op_id로 재시도 (Job이 HEAD 체크 후 skip 또는 재업로드) |
| archive_key 저장 후 | archive_key 있음 | 업로드 skip → delete_volume만 수행 |
| Volume 삭제 후 | archive_key 있음 | 최종 커밋만 수행 |

### 멱등성
- **op_id 기반 불변 경로**: 같은 op_id → 같은 archive_key
- **Job HEAD 체크**: S3에 이미 있으면 skip
- **archive_key 체크**: DB에 저장되어 있으면 업로드 skip

---

## DELETING

Volume만 삭제. Archive는 GC가 정리.

### 전제 조건
- `operation = DELETING`
- 컨테이너가 정지된 상태 (RUNNING이 아님)
- 모든 status에서 가능 (PENDING, COLD, WARM)

### 동작

```mermaid
flowchart TD
    A[DELETING 시작] --> B[Volume 삭제]
    B --> C[DB: deleted_at = now, status = DELETED]
    C --> D[완료]
    D -.-> E[GC가 Archive 정리]
```

### 삭제 대상

| 리소스 | 삭제 주체 | 타이밍 |
|--------|----------|--------|
| Volume | DELETING | 즉시 |
| Archives | GC | 1시간 후 (soft-delete 감지) |

> **왜 분리?**: Volume은 즉시 해제 (컴퓨팅 비용), Archive는 GC가 일괄 정리 (저장 비용, 배치 효율)

### Soft-Delete

```
DELETING 완료 시:
  - deleted_at = NOW()
  - status = DELETED
  - archive_key 유지 (GC가 orphan 판단에 사용)
```

> **중요**: archive_key를 NULL로 하지 않음. deleted workspace는 GC 보호 목록에서 제외되므로, 해당 archive는 자연스럽게 orphan이 됨.

### 실패 처리
- Volume 삭제 실패 시 재시도
- Archive는 GC 주기에 정리됨 (별도 처리 불필요)

---

## Reconciler 멱등성

Reconciler는 각 단계의 "완료 여부"를 DB 상태로 판단하여 멱등성을 보장합니다.

### ARCHIVING Reconciler

| 단계 | 완료 판단 기준 | 재시도 시 동작 |
|------|---------------|---------------|
| op_id 생성 | `op_id != NULL` | skip |
| archive | `archive_key != NULL` | skip |
| delete_volume | 멱등 (없으면 무시) | 항상 호출 |
| 최종 커밋 | `status = COLD, operation = NONE` | skip |

```python
def reconcile_archiving(ws):
    """ARCHIVING Reconciler - 멱등"""

    # 이미 완료 체크
    if ws.status == COLD and ws.operation == NONE:
        return

    # 단계 1: op_id 확보
    if ws.op_id is None:
        ws.op_id = uuid()
        db.update(ws)  # 커밋 포인트 1

    # 단계 2-3: archive (archive_key로 skip 판단)
    if ws.archive_key is None:
        archive_key = storage.archive(ws.id, ws.op_id)
        ws.archive_key = archive_key
        db.update(ws)  # 커밋 포인트 2

    # 단계 4: Volume 삭제 (멱등)
    storage.delete_volume(ws.id)

    # 단계 5: 완료
    ws.status = COLD
    ws.operation = NONE
    ws.op_id = None
    db.update(ws)  # 커밋 포인트 3
```

### RESTORING Reconciler

| 단계 | 완료 판단 기준 | 재시도 시 동작 |
|------|---------------|---------------|
| provision | 멱등 (있으면 무시) | 항상 호출 |
| restore | Crash-Only | 항상 재실행 |
| 최종 커밋 | `status = WARM, operation = NONE` | skip |

```python
def reconcile_restoring(ws):
    """RESTORING Reconciler - 멱등 (Crash-Only)"""

    # 이미 완료 체크
    if ws.status == WARM and ws.operation == NONE:
        return

    # 단계 1: Volume 생성 (멱등)
    storage.provision(ws.id)

    # 단계 2: restore (Crash-Only: 항상 재실행)
    if ws.archive_key:
        storage.restore(ws.id, ws.archive_key)

    # 단계 3: 완료
    ws.status = WARM
    ws.operation = NONE
    db.update(ws)
```

### DELETING Reconciler

| 단계 | 완료 판단 기준 | 재시도 시 동작 |
|------|---------------|---------------|
| delete_volume | 멱등 (없으면 무시) | 항상 호출 |
| soft-delete | `status = DELETED` | skip |

```python
def reconcile_deleting(ws):
    """DELETING Reconciler - 멱등"""

    # 이미 완료 체크
    if ws.status == DELETED:
        return

    # 단계 1: Volume 삭제 (멱등)
    storage.delete_volume(ws.id)

    # 단계 2: soft-delete
    ws.status = DELETED
    ws.deleted_at = now()
    ws.operation = NONE
    db.update(ws)
```

---

## 참조

- [storage.md](./storage.md) - 핵심 원칙, 인터페이스
- [storage-job.md](./storage-job.md) - Job 스펙 (Crash-Only 설계)
- [storage-gc.md](./storage-gc.md) - Archive GC
- [states.md](./states.md) - 상태 전환 규칙
