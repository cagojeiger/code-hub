# v0.2.0 Storage 저장 패턴 분석

> Object Storage 기반 Archive/Restore 패턴 분석

## 1. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                     S3StorageProvider                       │
│                   (Python Orchestrator)                     │
├─────────────────────────────────────────────────────────────┤
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
│  │ VolumeProvider│  │   JobRunner   │  │  S3 Client    │   │
│  │   (Docker)    │  │   (Docker)    │  │  (aioboto3)   │   │
│  └───────────────┘  └───────────────┘  └───────────────┘   │
└─────────────────────────────────────────────────────────────┘
           │                  │                    │
           ▼                  ▼                    ▼
    Docker Volume      Storage Job          S3/MinIO
    (codehub-ws-*)     Container           (bucket)
```

## 2. Object Storage 저장 패턴

### Archive Key 구조

```
{prefix}{workspace_id}/{op_id}/home.tar.zst
{prefix}{workspace_id}/{op_id}/home.tar.zst.meta

예: codehub-ws-abc123/op-001/home.tar.zst
    codehub-ws-abc123/op-001/home.tar.zst.meta
```

- **prefix**: 리소스 프리픽스 (기본: `codehub-ws-`)
- **workspace_id**: 워크스페이스 식별자
- **op_id**: 작업 ID (멱등성 보장)
- **.meta**: SHA256 체크섬 파일 (commit marker)

### Archive Flow (Volume → S3)

```sh
# containers/storage-job/archive.sh

1. HEAD 체크 (멱등성)
   - tar.zst + .meta 둘 다 존재하면 SKIP
   - 이미 완료된 작업 재실행 방지

2. tar + zstd 압축
   - tar --exclude='*.sock' -cf - -C /data . | zstd -o home.tar.zst
   - 소켓 파일 제외

3. SHA256 체크섬 생성
   - sha256sum → "sha256:{hash}" 형식
   - home.tar.zst.meta 파일로 저장

4. S3 업로드 (순서 중요!)
   - tar.zst 먼저 업로드
   - .meta 마지막 업로드 (commit marker)
   - .meta 존재 = 아카이브 완료 표시
```

### Restore Flow (S3 → Volume)

```sh
# containers/storage-job/restore.sh

1. S3 다운로드
   - tar.zst 다운로드
   - .meta 다운로드

2. 체크섬 검증
   - 다운로드된 tar.zst의 SHA256 계산
   - .meta 파일과 비교
   - 불일치 시 exit 1

3. 스테이징 추출
   - zstd -d | tar -x → /tmp/staging
   - 직접 /data에 추출하지 않음 (원자성)

4. rsync 동기화
   - rsync -a --delete /tmp/staging/ /data/
   - --delete: 기존 파일 중 없는 것 삭제
   - 원자적 상태 전환
```

## 3. 핵심 설계 원칙

| 원칙 | 구현 | 이점 |
|------|------|------|
| **Crash-Only** | 컨테이너 기반 실행 | 실패 시 재시작만 하면 됨 |
| **Stateless** | Python은 오케스트레이션만 | 상태 관리 복잡도 제거 |
| **Idempotent** | HEAD 체크 + op_id | 중복 실행 안전 |
| **Atomic** | .meta = commit marker | 부분 업로드 감지 가능 |
| **Verifiable** | SHA256 체크섬 | 데이터 무결성 보장 |

## 4. S3 Client 패턴

### 인프라 계층 (infra/s3.py)

```python
# 싱글톤 세션 관리
_session: aioboto3.Session | None = None

# 초기화 시 버킷 자동 생성
async def init_storage():
    try:
        await s3.head_bucket(Bucket=bucket_name)
    except ClientError:
        await s3.create_bucket(Bucket=bucket_name)

# Context Manager 패턴
class S3ClientContext:
    async def __aenter__(self) -> S3Client:
        self._client = await self._context.__aenter__()
        return self._client
```

### 사용 패턴

```python
async with get_s3_client() as s3:
    await s3.put_object(Bucket=bucket, Key=key, Body=data)
    await s3.get_object(Bucket=bucket, Key=key)
    await s3.delete_objects(Bucket=bucket, Delete={"Objects": [...]})
```

## 5. 계층 분리

| Layer | 책임 | 파일 |
|-------|------|------|
| **Interface** | 추상 계약 정의 | `core/interfaces/storage.py` |
| **Adapter** | S3 + Docker 통합 | `adapters/storage/s3.py` |
| **Infrastructure** | S3 클라이언트 관리 | `infra/s3.py` |
| **Job** | 컨테이너 실행 | `adapters/job/docker.py` |
| **Volume** | Docker 볼륨 관리 | `adapters/volume/docker.py` |
| **Shell Scripts** | 실제 아카이브 로직 | `containers/storage-job/*.sh` |

### 의존성 방향

```
Interface (core)
    ↑
Adapter (adapters)
    ↑
Infrastructure (infra)
    ↑
Shell Scripts (containers)
```

## 6. JobRunner 패턴

### 컨테이너 구성

```python
# adapters/job/docker.py

ContainerConfig(
    image=storage_job_image,
    cmd=["-c", "/usr/local/bin/archive"],  # 또는 restore
    env=[
        f"ARCHIVE_URL={archive_url}",      # s3://bucket/key
        f"AWS_ENDPOINT_URL={s3_endpoint}",
        f"AWS_ACCESS_KEY_ID={access_key}",
        f"AWS_SECRET_ACCESS_KEY={secret_key}",
    ],
    host_config=HostConfig(
        network_mode=network_name,
        binds=[f"{volume_name}:/data:ro"],  # archive는 ro
    ),
)
```

### 실행 흐름

```
1. 컨테이너 생성 (create)
2. 컨테이너 시작 (start)
3. 완료 대기 (wait with timeout)
4. 로그 수집 (logs)
5. 컨테이너 제거 (remove)
```

## 7. 이전 버전과 비교

| 항목 | v0.2.0 (S3) | Archive (LocalDir) |
|------|-------------|---------------------|
| 스토리지 | S3/MinIO | 로컬 디렉토리 |
| 압축 | tar.zst | 없음 |
| 무결성 검증 | SHA256 체크섬 | 없음 |
| 실행 방식 | Job Container | 직접 파일 I/O |
| 확장성 | K8s 호환 (DI) | 단일 호스트 전용 |
| 복구 | 체크섬 검증 후 rsync | 단순 복사 |

## 8. 파일 위치 참조

- Interface: `src/codehub/core/interfaces/storage.py`
- S3 Adapter: `src/codehub/adapters/storage/s3.py`
- S3 Client: `src/codehub/infra/s3.py`
- Job Runner: `src/codehub/adapters/job/docker.py`
- Volume Provider: `src/codehub/adapters/volume/docker.py`
- Archive Script: `containers/storage-job/archive.sh`
- Restore Script: `containers/storage-job/restore.sh`
