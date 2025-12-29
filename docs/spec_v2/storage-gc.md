# Storage GC 시스템 (M2)

> [storage.md](./storage.md)로 돌아가기

---

## 개요

GC는 **Archive 정리**만 담당합니다.

| 리소스 | GC 필요 | 이유 |
|--------|---------|------|
| Volume | ❌ | workspace당 1개 고정, purge 시에만 삭제 |
| Archive | ✅ | op_id 변경 시 이전 버전이 orphan으로 남음 |

### DELETING vs GC

| 구분 | DELETING | GC |
|------|----------|-----|
| 트리거 | 사용자 삭제 요청 | 주기적 배치 |
| 대상 | **Volume만** | orphan Archive |
| 타이밍 | 즉시 | 1시간 지연 |
| 목적 | 컴퓨팅 리소스 해제 | 저장공간 회수 |

> **책임 분리**: DELETING은 Volume만 즉시 삭제. Archive는 GC가 soft-delete된 workspace를 감지하여 정리.

---

## Archive GC

### 왜 필요한가?

```
1차 Archive (op_id = aaa):
  → archives/ws123/aaa/home.tar.gz  ← DB에 저장됨

2차 Archive (op_id = bbb):
  → archives/ws123/bbb/home.tar.gz  ← DB 업데이트
  → archives/ws123/aaa/...          ← orphan (GC 대상)
```

**Orphan 발생 원인**: 크래시, 재시도, 부분 실패, 정상적인 재아카이브

### Orphan 판단 규칙

```python
def is_orphan_archive(archive_path, workspaces):
    """Archive가 orphan인지 판단"""
    for ws in workspaces:
        # soft-deleted된 workspace는 보호하지 않음 (orphan 취급)
        if ws.deleted_at is not None:
            continue

        # DB에 저장된 archive_key와 일치하면 보호
        if archive_path == ws.archive_key:
            return False

        # ARCHIVING 진행 중인 op_id 경로 보호
        if ws.operation == "ARCHIVING" and ws.op_id:
            if ws.op_id in archive_path:
                return False

    return True
```

> **Soft-Delete 처리**: `deleted_at != NULL`인 workspace의 archive는 보호하지 않음 → orphan으로 판단 → GC 대상

### GC 프로세스

```mermaid
flowchart TD
    A[GC 주기 실행] --> B[S3 ListObjects - prefix=archives/]
    B --> C[DB에서 보호 목록 조회]
    C --> D{보호 대상?}
    D -->|DB archive_key 일치| E[보호]
    D -->|ARCHIVING 중 op_id| E
    D -->|그 외| F[orphan 마킹]
    F --> G{1시간 경과?}
    G -->|Yes| H[삭제]
    G -->|No| I[다음 GC까지 대기]
```

### 안전 지연

orphan 판단 후 **즉시 삭제하지 않고 1시간 대기** 후 삭제합니다.

| 항목 | 값 |
|------|---|
| 지연 시간 | 1시간 |
| 목적 | 진행 중인 작업 완료 대기 |
| 구현 | `first_orphan_detected` 타임스탬프 기록 |
| 조건 | 1시간 연속 orphan이면 삭제 |

> **왜 1시간?**: Archive Job timeout(30분) + 여유. 크래시 후 재시도가 완료되기 전에 삭제 방지.

---

## 시간 복잡도

GC 알고리즘은 **O(W + A)** 로 최적화합니다.

| 방식 | 복잡도 | 설명 |
|------|--------|------|
| Naive (이중 루프) | O(A × W) | 각 archive마다 모든 workspace 순회 |
| **Optimized (Set)** | O(W + A) | protected_keys Set 구축 후 O(1) lookup |

### 최적화된 구현

```python
def find_orphan_archives(archives, workspaces):
    """O(W + A) 복잡도로 orphan 탐지"""

    # O(W): 보호 목록 구축
    protected_keys = set()
    archiving_op_ids = set()

    for ws in workspaces:
        if ws.deleted_at is not None:
            continue  # soft-deleted는 보호 안 함

        if ws.archive_key:
            protected_keys.add(ws.archive_key)

        if ws.operation == "ARCHIVING" and ws.op_id:
            archiving_op_ids.add(ws.op_id)

    # O(A): orphan 판단
    orphans = []
    for archive in archives:
        if archive in protected_keys:
            continue
        if any(op_id in archive for op_id in archiving_op_ids):
            continue
        orphans.append(archive)

    return orphans
```

### 메모리 분석

| 규모 | 메모리 (Set 방식) | 비고 |
|------|------------------|------|
| 10K workspaces | ~1.5 MB | 충분히 작음 |
| 100K workspaces | ~15 MB | 허용 범위 |
| 1M workspaces | ~150 MB | 최적화 필요 |

---

## 대규모 최적화: Counting Bloom Filter (P3)

M2에서는 Set 방식 사용. 대규모(100K+ workspaces) 시 Counting Bloom Filter 고려.

| 항목 | Set | Counting Bloom Filter |
|------|-----|----------------------|
| 메모리 | O(W) ~15MB/100K | O(1) ~480KB 고정 |
| 정확도 | 100% | 99%+ |
| 증분 업데이트 | ❌ 매번 재구축 | ✅ Insert/Delete 지원 |
| False Negative | 불가능 | 가능 (삭제 지연, 안전) |

### 왜 Counting Bloom Filter인가?

- **표준 Bloom Filter 한계**: Delete 불가 (비트 공유 문제)
- **Counting Bloom Filter**: 카운터 사용 → Insert/Delete 모두 지원
- **이벤트 기반 증분 업데이트**:
  - workspace 생성 → `filter.insert(archive_key)`
  - workspace 삭제 → `filter.delete(archive_key)`
  - ARCHIVING 시작 → `filter.insert(op_id)`
  - ARCHIVING 완료 → `filter.delete(old_key)`, `filter.insert(new_key)`

### 안전성

| 오류 유형 | 발생 가능성 | 영향 |
|----------|-------------|------|
| False Positive | 불가능 | - |
| False Negative | ~1% | 삭제 지연 (안전) |

> **안전한 이유**: False negative는 "orphan을 정상으로 오판" → 삭제가 다음 GC 사이클로 미뤄질 뿐, 데이터 손실 없음.
>
> 여러 GC 사이클 후 모든 orphan 삭제됨: P(N번 후에도 남음) = (0.01)^N

---

## GC와 Operation 동시성

GC와 workspace Operation은 **독립적으로 실행** 가능합니다.

### 구조적 분리

| 구분 | GC | Operation (Reconciler) |
|------|-----|------------------------|
| 실행 단위 | 전체 DB 스캔 (배치) | workspace 단위 |
| 트리거 | 주기적 (cron) | 상태 변경 시 |
| 락 범위 | 없음 (읽기 전용) | workspace당 operation 1개 |

### 동시 실행 시나리오

```
T1: GC가 DB 조회 (ws.archive_key = 'old')
T2: ARCHIVING이 archive_key = 'new' 저장
T3: GC가 S3 스캔 (T1 시점 스냅샷)
    → 'new'는 GC 대상에 없음 (T2 이후 생성)
T4: 다음 GC 사이클
    → 'new'는 DB에 있으므로 보호
    → 'old'는 orphan으로 삭제
```

### 안전성 보장

| 메커니즘 | 역할 |
|---------|------|
| ARCHIVING op_id 보호 | 진행 중인 업로드 경로 보호 |
| 1시간 지연 삭제 | 작업 완료 대기 |
| DB 스냅샷 | 일관된 보호 목록 |

> **결론**: GC와 Operation이 동시에 실행되어도 데이터 손실 없음.
> 최악의 경우 orphan 삭제가 다음 GC 사이클로 지연될 뿐.

---

## 참조

- [storage.md](./storage.md) - 네이밍 규칙, 인터페이스
- [storage-job.md](./storage-job.md) - Job 스펙
- [storage-operations.md](./storage-operations.md) - 플로우 상세
