# code-hub

> v0.2.0

클라우드 개발 환경(CDE) 플랫폼.
필요할 때 격리된 워크스페이스를 생성하고, 브라우저에서 바로 코딩.

## 왜 code-hub인가?

여러 작업을 동시에 진행할 때, 같은 환경에서 작업하면 서로 충돌이 발생합니다.
git 상태, 빌드 결과물, 의존성이 섞이고, "내 환경에서는 됐는데?" 문제가 생깁니다.

code-hub는 작업별로 **완전히 격리된 워크스페이스**를 제공하여 이 문제를 해결합니다.

### 핵심 기능

- **환경 격리**: 워크스페이스마다 독립된 홈 디렉토리와 컨테이너
- **유연한 관리**: 필요할 때 생성/시작, 안 쓰면 정지하여 리소스 절약
- **웹 접속**: 브라우저에서 code-server(웹 기반 VS Code)로 즉시 접속
- **데이터 영속성**: 워크스페이스를 정지해도 작업 내용 유지

### 작동 방식

1. 워크스페이스 생성 → 격리된 개발 환경 준비
2. `/w/{workspace_id}` URL로 브라우저 접속
3. code-server에서 코딩
4. 작업 완료 후 정지 (데이터는 유지)
5. 다시 필요하면 시작하여 이어서 작업

## 빠른 시작

### 요구사항
- Docker & Docker Compose
- Git

### 실행

```bash
git clone https://github.com/cagojeiger/code-hub.git
cd code-hub
docker compose up -d
```

브라우저에서 http://localhost:8000 접속

**기본 계정**: `admin` / `admin`

> 초기 비밀번호를 변경하려면 `docker-compose.yml`에서 `CODEHUB_AUTH__INITIAL_ADMIN_PASSWORD` 환경변수를 설정하세요.

## 문서

- [스펙](docs/spec/) - API, DB 스키마, 상태 머신, 설정
- [아키텍처](docs/architecture/) - 시스템 구조 및 Coordinator 패턴
- [용어집](docs/spec/01-glossary.md) - 핵심 용어 정의

## 기술 스택

- **Backend**: Python 3.13 + FastAPI + uvicorn
- **Database**: PostgreSQL 17 + Alembic
- **Cache/Pub-Sub**: Redis 7
- **Container**: Docker + docker-py
- **IDE**: code-server (웹 기반 VS Code)

## 라이선스

[Apache License 2.0](LICENSE)
