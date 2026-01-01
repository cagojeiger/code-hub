# CodeHub Roadmap 1 Archive

> Roadmap 1 구현의 아카이브 버전입니다.

---

## 실행 방법

```bash
cd .archive
docker compose up
```

## 아키텍처 개요

### 상태 머신

```
CREATED → PROVISIONING → RUNNING → STOPPING → STOPPED → DELETING → DELETED
```

### 주요 컴포넌트

| 컴포넌트 | 위치 | 설명 |
|----------|------|------|
| Instance Controller | `backend/app/services/instance/local_docker.py` | Docker 컨테이너 관리 |
| Storage Provider | `backend/app/services/storage/local_dir.py` | 로컬 디렉토리 바인드 마운트 |
| Workspace Service | `backend/app/services/workspace_service.py` | 상태 머신 오케스트레이션 |
| Proxy | `backend/app/proxy/` | code-server 리버스 프록시 |

### 기술 스택

- **Backend**: FastAPI + SQLModel + PostgreSQL
- **Container Runtime**: Docker (local-docker)
- **Storage**: 로컬 파일시스템 바인드 마운트 (local-dir)
- **Frontend**: Vanilla JS + Tailwind CSS

---

## Roadmap 1 vs Roadmap 2 차이점

| 항목 | Roadmap 1 | Roadmap 2 |
|------|-----------|-----------|
| 상태 | 7개 (CREATED~DELETED) | 4+2개 (PENDING, COLD, WARM, RUNNING + ERROR, DELETED) |
| 패턴 | 명령형 (start/stop) | 선언형 (desired_state + Reconciler) |
| Storage | 로컬 바인드 마운트 | Volume + Object Storage (archive/restore) |
| 자원 관리 | 항상 Volume 유지 | TTL 기반 자동 COLD 전환 |

---

## 테스트 실행

```bash
cd .archive
docker compose -f docker-compose.e2e.yml up --abort-on-container-exit
```

---

## 참조

- [Roadmap 1 Spec](../docs/spec/) - Roadmap 1 상세 스펙
- [Roadmap 2 Spec](../docs/spec_v2/) - Roadmap 2 상세 스펙 (현재 개발 중)
