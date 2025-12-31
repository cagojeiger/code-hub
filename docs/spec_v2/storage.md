# Storage (M2)

> [README.md](./README.md)로 돌아가기

---

## 핵심 원칙

1. **Non-preemptive Operation**: workspace당 동시에 1개만
2. **Idempotent Archive**: op_id로 멱등성 확보 (HEAD 체크)
3. **Crash-Only Restore**: 재실행해도 같은 결과
4. **Separation of Concerns**: StorageProvider = 데이터 이동, Reconciler = DB 커밋
5. **Checksum 검증**: meta 파일 기반 sha256
6. **GC 분리**: DELETING은 Volume만, Archive는 GC가 정리
7. **Soft Delete**: Workspace soft-delete로 GC orphan 판단 지원

---

## 불변식

1. Archive Job: 같은 (workspace_id, op_id)에 대해 멱등 (HEAD 체크)
2. Restore Job: 같은 archive → 같은 결과 (Crash-Only)
3. 순서 보장: archive_key DB 저장 → Volume 삭제 (역순 금지)
4. Volume 고정: workspace당 1개 (`ws-{workspace_id}-home`)

---

## Operation별 Storage 동작

| Operation | 동작 | op_id 필요 |
|-----------|------|-----------|
| PROVISIONING | 빈 Volume 생성 | - |
| RESTORING | archive → Volume | - |
| ARCHIVING | Volume → archive + Volume 삭제 | ✅ |
| DELETING | Volume 삭제 (Archive는 GC) | - |

---

## 완료 조건 (관측 기반)

| Operation | Actuator | 완료 조건 |
|-----------|----------|----------|
| PROVISIONING | provision() | volume_exists() |
| RESTORING | provision() + restore() | volume_exists() |
| ARCHIVING | archive() + delete_volume() | !volume_exists() |
| DELETING | delete() + delete_volume() | !is_running() AND !volume_exists() |

> **원칙**: Actuator 성공 반환 ≠ 완료. 관측 조건 충족 = 완료.

---

## 네이밍 규칙

| 항목 | 형식 | 예시 |
|------|------|------|
| volume_key | `ws-{workspace_id}-home` | `ws-abc123-home` |
| archive_key | `archives/{workspace_id}/{op_id}/home.tar.gz` | `archives/abc123/550e8400.../home.tar.gz` |

> K8s DNS-1123 호환: 하이픈(`-`) 사용, 언더스코어(`_`) 금지

---

## op_id 정책

| 시점 | 상태 | 동작 |
|------|------|------|
| 첫 시도 | NULL | 생성 후 DB 저장 |
| 재시도 | NOT NULL | 기존 값 사용 |

> op_id는 archive 호출 전에 DB에 먼저 저장. 크래시 후 같은 op_id로 재시도.

---

## Job 특성

| 항목 | 값 |
|------|---|
| 입력 | ARCHIVE_URL, S3 인증 정보 |
| 출력 | exit code (0=성공) |
| 의존성 | Object Storage만 (DB 없음) |
| 설계 | Crash-Only, Stateless, Idempotent |

> **상세**: [storage-job.md](./storage-job.md)

---

## 무결성 검증

| 단계 | 방식 |
|------|------|
| Archive | tar.gz sha256 → .meta 파일 저장 |
| Restore | 다운로드 후 .meta와 비교 |

---

## 에러 처리

| 에러 코드 | ErrorReason | 복구 |
|----------|-------------|------|
| ARCHIVE_NOT_FOUND | DataLost | 관리자 개입 |
| S3_ACCESS_ERROR | Unreachable | 자동 재시도 (3회) |
| CHECKSUM_MISMATCH | DataLost | 관리자 개입 |
| TAR_EXTRACT_FAILED | ActionFailed | 자동 재시도 (3회) |

---

## 크래시 복구 (ARCHIVING)

| 크래시 시점 | DB 상태 | 재시도 동작 |
|------------|---------|------------|
| 업로드 중 | op_id 있음, archive_key 불일치 | 같은 op_id로 재시도 (HEAD 체크) |
| archive_key 저장 후 | archive_key 일치 | 업로드 skip → delete_volume만 |
| Volume 삭제 후 | archive_key 일치, !volume_exists | 최종 커밋만 |

---

## DELETING 삭제 대상

| 리소스 | 삭제 주체 | 타이밍 |
|--------|----------|--------|
| Container | InstanceController | 즉시 |
| Volume | StorageProvider | Container 삭제 후 |
| Archives | GC | 2시간 후 |

> Container/Volume = 컴퓨팅 비용 즉시 해제, Archive = GC 배치 정리

---

## StorageProvider 인터페이스

| 메서드 | 역할 | 멱등성 |
|--------|------|--------|
| provision(workspace_id) | Volume 생성 | 이미 있으면 무시 |
| restore(workspace_id, archive_key) | Archive → Volume | Crash-Only |
| archive(workspace_id, op_id) | Volume → Archive | HEAD 체크 |
| delete_volume(workspace_id) | Volume 삭제 | 없으면 무시 |
| volume_exists(workspace_id) | Volume 존재 확인 | - |

---

## 참조

- [storage-job.md](./storage-job.md) - Job 스펙
- [components/archive-gc.md](./components/archive-gc.md) - Archive GC
- [components/state-reconciler.md](./components/state-reconciler.md) - StateReconciler
- [error.md](./error.md) - 에러 정책
- [instance.md](./instance.md) - Instance 동작
