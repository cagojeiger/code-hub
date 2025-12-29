# Storage (M2)

> [README.md](./README.md)로 돌아가기

---

## 핵심 원칙

재시도/크래시/부분 실패에 안전한 Storage 설계.

1. **오퍼레이션은 workspace당 동시에 1개만** - `operation`이 락
2. **Archive는 op_id로 멱등성 확보** - 불변 경로 (HEAD 체크)
3. **Restore는 Crash-Only 설계** - 항상 재실행해도 같은 결과
4. **StorageProvider = 데이터 이동, Reconciler = DB 커밋**
5. **meta 파일 기반 checksum으로 무결성 검증** - sha256
6. **DELETING은 Volume만, Archive는 GC가 정리** - 책임 분리
7. **Workspace는 soft-delete** - GC가 orphan 판단에 필요

---

## 동시성 안전

**workspace당 operation은 동시에 1개만 실행 가능**:
- `operation != 'NONE'`이면 다른 작업 시작 불가
- 동일 workspace에 대한 동시 Archive/Restore 없음
- 따라서 **동시 덮어쓰기 이슈 없음**

---

## 불변식 (Invariants)

```
- archive Job: 같은 (workspace_id, op_id)에 대해 멱등 (HEAD 체크)
- restore Job: 같은 archive → 같은 결과 (Crash-Only)
- archive_key DB 저장 → Volume 삭제 순서 (역순 금지)
- Volume은 workspace당 1개 고정 (ws_{workspace_id}_home)
```

---

## 개요

상태 전환 중 Storage 관련 동작을 정의합니다.

| operation | Storage 동작 |
|-----------|-------------|
| RESTORING | restore (archive → volume) 또는 provision (빈 volume) |
| ARCHIVING | archive (volume → archive) + delete_volume |
| DELETING | delete_volume (Volume만 삭제, Archive는 GC가 정리) |

> 상세 플로우는 [storage-operations.md](./storage-operations.md) 참조

### 백엔드별 용어

| 추상 개념 | local-docker | k8s |
|----------|-------------|-----|
| Volume | Docker Volume | PersistentVolumeClaim (PVC) |
| Job | 임시 컨테이너 (`docker run --rm`) | Job Pod |
| Object Storage | MinIO | S3 / MinIO |

---

## 네이밍 규칙

모든 Storage 관련 식별자의 네이밍 패턴입니다.

### 키 형식

| 항목 | 형식 | 예시 |
|------|------|------|
| volume_key | `ws_{workspace_id}_home` | `ws_abc123_home` |
| archive_key | `archives/{workspace_id}/{op_id}/home.tar.gz` | `archives/abc123/550e8400.../home.tar.gz` |

> **Volume은 workspace당 1개 고정** - Volume GC 불필요

### Volume 라벨 (K8s/Docker)

```yaml
labels:
  codehub.io/workspace-id: "abc123"
```

### 마운트 경로

| 컨테이너 | 마운트 경로 | 설명 |
|---------|-----------|------|
| Job | `/data` | Volume 마운트 |
| Job | `/tmp` | 임시 공간 (emptyDir) |
| Workspace | `/home/coder` | Volume 마운트 (동일 Volume) |

### Volume 내부 구조

```
Volume
└── (사용자 파일들)         # 사용자 데이터만 저장
```

> **단순화**: Volume에는 사용자 데이터만 존재. 임시 파일(staging, tar.gz)은 모두 `/tmp`(emptyDir)에 저장.

> **상세**: [storage-job.md](./storage-job.md#마운트-구조)

---

## Job (임시 컨테이너)

Archive/Restore 작업은 **Job**이 수행합니다. Job은 Volume과 Object Storage 간 데이터 이동을 담당하는 격리된 컨테이너입니다.

### 핵심 특성

| 항목 | 값 |
|------|---|
| 입력 | ARCHIVE_URL, S3 인증 정보 |
| 출력 | exit code (0=성공, ≠0=실패) |
| 의존성 | Object Storage만 (DB 없음, Reconciler 없음) |
| 멱등성 | HEAD 체크 (Archive), 항상 재실행 (Restore) |

### 설계 철학

> **Crash-Only Design**: 복잡한 상태 관리보다 단순한 재시작을 선택
> - Stateless: Volume에 상태 저장 안 함
> - Idempotent: 재시도해도 같은 결과

### 격리 원칙

```
Job은 DB를 모르고, Reconciler를 모르고, Control Plane을 모른다.
매개변수만 받아서 작업하고, 성공/실패만 반환한다.
```

> **상세 스펙**: [storage-job.md](./storage-job.md) 참조

---

## 무결성 검증

meta 파일 기반 checksum을 사용합니다.

| 단계 | 방식 | 설명 |
|------|------|------|
| Archive | sha256 생성 | tar.gz의 sha256을 .meta에 저장 |
| Restore | sha256 검증 | 다운로드 후 .meta와 비교 |

> **왜 ETag/Content-MD5가 아닌가?**: 멀티파트 업로드 시 ETag ≠ MD5이고,
> Content-MD5는 멀티파트에서 파트별로만 적용됨. 별도 checksum이 확실함.

---

## Job 에러 처리

StorageProvider가 예외를 던지면 Reconciler가 ERROR 상태로 전환:

- `previous_status` 저장 (복구 시 사용)
- `error_message` 기록
- `error_count` 증가
- `op_id` 유지 (재시도 시 같은 값 사용)

> **에러 유형 상세**: [storage-job.md](./storage-job.md#에러-처리) 참조

---

## StorageProvider 인터페이스

```python
class StorageProvider(ABC):
    """Storage 작업 추상 인터페이스

    Job(임시 컨테이너)은 구현 세부사항으로,
    각 백엔드가 내부적으로 처리함.

    핵심 원칙:
    - workspace_id, op_id를 인자로 받음 (Reconciler가 DB 관리)
    - 모든 작업은 멱등
    """

    @abstractmethod
    async def provision(self, workspace_id: str) -> None:
        """신규 Volume 생성 (멱등).

        Args:
            workspace_id: 워크스페이스 ID

        내부적으로 volume_key = ws_{workspace_id}_home 사용
        멱등성: Volume이 이미 있으면 무시
        """

    @abstractmethod
    async def restore(self, workspace_id: str, archive_key: str) -> None:
        """Object Storage → Volume (멱등).

        Args:
            workspace_id: 워크스페이스 ID
            archive_key: Object Storage 경로

        멱등성: 같은 아카이브 → 같은 결과 (Crash-Only 설계)

        Raises:
            StorageError: 복원 실패 시
        """

    @abstractmethod
    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Volume → Object Storage (멱등).

        Args:
            workspace_id: 워크스페이스 ID
            op_id: 작업 ID (archive_key 생성에 사용)

        Returns:
            archive_key: 경로 (archives/{workspace_id}/{op_id}/home.tar.gz)

        멱등성: HEAD 체크로 이미 존재하면 skip
        """

    @abstractmethod
    async def delete_volume(self, workspace_id: str) -> None:
        """Volume 삭제 (멱등).

        Args:
            workspace_id: 워크스페이스 ID

        멱등성: 존재하지 않으면 무시
        """

    # purge 메서드 제거됨 - DELETING은 delete_volume만 호출
    # Archive는 GC가 별도로 정리 (soft-delete된 workspace의 archive는 orphan 취급)
```

---

## 참조

- [storage-job.md](./storage-job.md) - Job 스펙 (Crash-Only 설계)
- [storage-operations.md](./storage-operations.md) - RESTORING, ARCHIVING, DELETING 플로우
- [storage-gc.md](./storage-gc.md) - Archive GC
- [states.md](./states.md) - 상태 전환 규칙
- [instance.md](./instance.md) - 인스턴스 동작
