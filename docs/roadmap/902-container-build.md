# Roadmap 902: 컨테이너 빌드 (미래)

## Status: Future

> Workspace에서 컨테이너 이미지를 빌드하고 내부 레지스트리에 저장

---

## 문제 상황

```
Workspace (code-server)
    ↓
docker build .  ← 실행 불가 (Docker 데몬 없음)
```

**원인**: Workspace Pod에서 Docker 데몬 접근 불가 (보안 + K8s 호환)

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  Internal Network (code-hub)                                │
│                                                             │
│  Workspace Pod         BuildKit           Registry          │
│  ┌───────────┐        ┌─────────┐        ┌─────────┐       │
│  │code-server│──build─▶│BuildKit │──push─▶│Registry │       │
│  │           │   요청  │ (원격)  │        │ (v2)    │       │
│  └───────────┘        └─────────┘        └────┬────┘       │
│       │                                       │             │
│       │              ┌─────────┐              │             │
│       └────pull─────▶│  MinIO  │◀────storage──┘             │
│                      │(backend)│                            │
│                      └─────────┘                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                    ❌ External Access
```

---

## 핵심 구성 요소

| 구성 요소 | 역할 | 기술 |
|----------|------|------|
| **BuildKit** | 데몬리스 이미지 빌드 | moby/buildkit |
| **Registry** | 이미지 저장소 | Docker Registry v2 |
| **MinIO** | Registry 백엔드 스토리지 | S3 호환 |

---

## 주소 체계

```
registry:5000/
├── {user_id}/              # 사용자별 네임스페이스
│   ├── {image}:{tag}       # 사용자 빌드 이미지
│   └── ...
├── _system/                # 시스템 이미지 (Workspace 템플릿)
│   ├── code-server:latest
│   └── jupyter:latest
└── _cache/                 # 외부 이미지 캐시 (선택)
    ├── python:3.11
    └── node:20
```

**이미지 네이밍 예시**:
```
registry:5000/user-123/myapp:v1
registry:5000/user-123/myapp:latest
```

---

## 동작 흐름

### 1. 빌드 요청

```
Workspace                          BuildKit                Registry
┌──────────────┐                  ┌─────────┐            ┌─────────┐
│ Dockerfile   │                  │         │            │         │
│ src/         │──build 요청────▶│  Build  │───push────▶│  Store  │
│              │                  │         │            │         │
└──────────────┘                  └─────────┘            └─────────┘

결과: registry:5000/user-123/myapp:v1
```

### 2. 이미지 Pull

```
Deployment                                   Registry
┌──────────────┐                           ┌─────────┐
│ K8s Pod      │◀──────pull───────────────│  Image  │
│ (Helm 배포)  │                           │         │
└──────────────┘                           └─────────┘
```

---

## MinIO 스토리지 통합

M2에서 MinIO 도입 시 하나의 MinIO로 통합:

```
MinIO
├── /archive/     ← COLD 상태 워크스페이스 아카이브 (M2)
└── /registry/    ← 컨테이너 이미지 저장 (902)
```

---

## 구현 고려사항

| 항목 | 설명 |
|------|------|
| **빌드 트리거** | API 호출 또는 CLI 도구 |
| **DNS 해석** | 내부망에서 `registry:5000` 해석 |
| **Insecure Registry** | 내부망이라 HTTP 허용 (TLS 생략 가능) |
| **환경변수** | `REGISTRY_URL=registry:5000` 워크스페이스에 주입 |
| **이미지 정리** | GC 정책 필요 (오래된 이미지 삭제) |

---

## Local Docker (M1/M2) 예시

```yaml
# docker-compose.yml
services:
  registry:
    image: registry:2
    ports:
      - "5000:5000"
    environment:
      REGISTRY_STORAGE: s3
      REGISTRY_STORAGE_S3_ENDPOINT: minio:9000
      REGISTRY_STORAGE_S3_BUCKET: registry
      REGISTRY_STORAGE_S3_ACCESSKEY: minioadmin
      REGISTRY_STORAGE_S3_SECRETKEY: minioadmin
      REGISTRY_STORAGE_S3_REGION: us-east-1
      REGISTRY_STORAGE_S3_SECURE: "false"

  buildkit:
    image: moby/buildkit:latest
    privileged: true  # BuildKit은 privileged 필요

  minio:
    image: minio/minio
    # ... M2 설정과 공유
```

---

## 의존성

- M2: MinIO (Object Storage) 도입 후 자연스러움
- M3: K8s 환경에서 BuildKit DaemonSet 또는 Job 방식

---

## 미결정 사항

1. **빌드 트리거 방식**: CLI 도구? VS Code Extension? API 직접?
2. **BuildKit 배포 방식**: DaemonSet vs Job vs 별도 서비스
3. **이미지 보관 정책**: 사용자별 용량 제한?

---

## References

- [BuildKit GitHub](https://github.com/moby/buildkit)
- [Docker Registry v2](https://docs.docker.com/registry/)
- [Registry S3 Storage Driver](https://docs.docker.com/registry/storage-drivers/s3/)
