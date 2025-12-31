# Storage Job Specification (M2)

> [storage.md](./storage.md)로 돌아가기

---

## 개요

Job은 Volume과 Object Storage 간 데이터 이동을 담당하는 **격리된 컨테이너**입니다.

| 항목 | 값 |
|------|---|
| 입력 | ARCHIVE_URL, S3 인증 정보 |
| 출력 | exit code (0=성공) |
| 의존성 | Object Storage만 (DB 없음) |
| 설계 | Crash-Only, Stateless, Idempotent |

> **격리 원칙**: Job은 DB, Reconciler, Control Plane을 모름. 매개변수만 받아 작업.

---

## 불변식

1. Job 실행 중 workspace 컨테이너는 실행되지 않음
2. Job 실행 중 Volume의 write-owner는 Job만
3. Job은 경로를 구성하지 않음 (전체 URL 수신)

---

## 마운트 구조

| 컨테이너 | 마운트 | 경로 |
|---------|--------|------|
| Job | Volume | /data |
| Job | emptyDir | /tmp (임시, 종료 시 삭제) |
| Workspace | Volume | /home/coder |

> /tmp에 tar.gz, meta, staging 저장. Job 종료 시 자동 삭제.

---

## 입력 환경변수

| 환경변수 | 설명 |
|---------|------|
| ARCHIVE_URL | 전체 경로 (`s3://bucket/archives/{ws_id}/{op_id}/home.tar.gz`) |
| S3_ENDPOINT | Object Storage 엔드포인트 |
| S3_ACCESS_KEY | 인증 정보 |
| S3_SECRET_KEY | 인증 정보 |

---

## Restore Job

| 단계 | 동작 |
|------|------|
| 1 | ARCHIVE_URL, ARCHIVE_URL.meta 다운로드 |
| 2 | sha256 checksum 검증 |
| 3 | staging 디렉토리에 tar 해제 |
| 4 | rsync --delete로 /data 동기화 |

> **Crash-Only**: 크래시 시 /tmp 사라짐 → 재시도하면 처음부터 재실행
>
> **주의**: /data의 기존 파일은 삭제됨 (아카이브 스냅샷으로 동기화)

---

## Archive Job

| 단계 | 동작 |
|------|------|
| 1 | HEAD 체크: tar.gz + meta 둘 다 있으면 skip (exit 0) |
| 2 | /data를 tar.gz 압축 |
| 3 | sha256 checksum 생성 → .meta |
| 4 | tar.gz, .meta 업로드 |

> **멱등성**: 같은 op_id = 같은 경로 → HEAD 체크로 완료 여부 판단

---

## 무결성 검증

| 단계 | 방식 |
|------|------|
| Archive | tar.gz sha256 → .meta 파일 저장 |
| Restore | 다운로드 후 .meta와 비교 |

### meta 파일 형식

```
sha256:{hex_string}
```

---

## 에러 코드 (CODEHUB_ERROR)

| 코드 | 설명 | ErrorReason | 복구 |
|------|------|-------------|------|
| S3_ACCESS_ERROR | Object Storage 접근 실패 | Unreachable | 자동 재시도 (3회), 초과 시 관리자 개입 |
| ARCHIVE_NOT_FOUND | 아카이브 없음 | DataLost | 관리자 개입 (즉시 terminal) |
| META_NOT_FOUND | meta 없음 (불완전) | DataLost | 관리자 개입 (즉시 terminal) |
| CHECKSUM_MISMATCH | sha256 불일치 | DataLost | 관리자 개입 (즉시 terminal) |
| TAR_EXTRACT_FAILED | 압축 해제 실패 | ActionFailed | 자동 재시도 (3회), 초과 시 관리자 개입 |
| DISK_FULL | 디스크 공간 부족 | ActionFailed | 관리자 개입 |

> 재시도 정책은 [error.md](./error.md) 참조

---

## tar 안전 원칙

### Restore (추출)

- 절대경로 금지
- `..` 경로 탈출 방지
- `--no-same-owner` (소유권 강제 덮어쓰기 금지)

### Archive (생성)

- 특수파일(socket, device) 제외

---

## 디스크 공간 요구사항

| 작업 | 필요 공간 | 계산 |
|------|----------|------|
| Restore | 3.0x | /tmp(tar.gz + staging) + /data |
| Archive | 2.0x | /tmp(tar.gz) + /data |

> 보수적 추정 (압축률 0% 가정)

---

## Job Timeout

| 백엔드 | 설정 | 권장값 |
|--------|------|--------|
| K8s | activeDeadlineSeconds | 1800초 (30분) |
| Docker | timeout wrapper | 1800초 |

> 계산 근거: 10GB / 10MB/s = 1000초 + 여유

---

## 실행 권한

| 백엔드 | 설정 |
|--------|------|
| Docker | `--user 1000:1000` |
| K8s | securityContext (runAsUser: 1000) |

> Job ≠ workspace UID면 Permission denied 발생

---

## 참조

- [storage.md](./storage.md) - StorageProvider 인터페이스
- [components/archive-gc.md](./components/archive-gc.md) - GC 시스템
