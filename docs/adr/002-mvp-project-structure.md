# ADR-002: MVP Project Structure

## 상태
Accepted

## 컨텍스트
code-hub MVP 개발을 위한 프로젝트 구조 결정이 필요합니다.

**고려 사항:**
- MVP는 Python + FastAPI로 개발
- 추후 Go로 재작성 예정
- 프론트엔드 추가 예정
- 워크스페이스 최대 10개 (소규모)

**조사한 오픈소스 프로젝트:**
| 프로젝트 | 구조 | 특징 |
|----------|------|------|
| OpenHands | 계층+기능 혼합 | 이벤트 기반, 복잡도 높음 |
| Open WebUI | models + routers | 단순, 빠른 개발 |
| LiteLLM | 어댑터 패턴 | 백엔드 확장 용이 |

## 결정

### 1. Monorepo Lite 구조

```
code-hub/
├── .github/                   # CI/CD
├── docker-compose.yml         # 통합 실행
├── docs/                      # 문서
│
├── backend/                   # Python FastAPI (MVP)
│   ├── app/
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
│
└── frontend/                  # React/Next.js (추후)
    ├── src/
    ├── package.json
    └── Dockerfile
```

### 2. 백엔드 내부 구조 (서비스 중심)

```
backend/
├── app/
│   ├── main.py               # FastAPI 엔트리포인트
│   │
│   ├── api/                  # [API 계층] 요청/응답 처리
│   │   └── v1/
│   │       ├── router.py     # 라우터 통합
│   │       ├── auth.py       # 인증 API
│   │       └── workspaces.py # 워크스페이스 API
│   │
│   ├── services/             # [서비스 계층] 핵심 비즈니스 로직
│   │   ├── docker_service.py # Instance Controller 로직
│   │   ├── storage_service.py# Storage Provider 로직
│   │   ├── proxy_service.py  # 프록시 로직
│   │   └── lifecycle.py      # 워크스페이스 상태 머신
│   │
│   ├── data/                 # [데이터 계층]
│   │   ├── database.py       # DB 연결 (aiosqlite, WAL)
│   │   └── models.py         # SQLModel (DB + API 스키마 통합)
│   │
│   └── core/                 # [공통]
│       ├── config.py         # 설정 (pydantic-settings)
│       └── errors.py         # 도메인 예외
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── Dockerfile
└── pyproject.toml
```

### 3. 설계 원칙

| 원칙 | 설명 |
|------|------|
| 서비스 집중 | `services/`에 핵심 로직 집중 → Go 전환 시 참조 대상 |
| SQLModel 사용 | DB 모델 + API 스키마 통합 → 중복 제거, 개발 속도 향상 |
| 역할 기반 폴더명 | `backend` (언어 명시 X) → 설정 파일 수정 최소화 |
| API 계층 분리 | 라우터는 요청/응답 처리만, 로직은 서비스에 위임 |

### 4. 의존성 흐름

```
api/v1/*.py (라우터)
    ↓
services/*.py (비즈니스 로직)
    ↓
data/*.py (DB, 모델)
```

## 결과

### 장점
- **Go 전환 용이**: `services/` 폴더만 보면 핵심 로직 파악 가능
- **프론트엔드 추가 용이**: `frontend/` 폴더만 추가하면 됨
- **설정 변경 최소화**: `backend` 폴더명 유지로 docker-compose 등 수정 불필요
- **빠른 MVP 개발**: SQLModel로 중복 코드 제거

### 단점
- 초기 폴더 수가 많아 보일 수 있음
- 작은 기능도 여러 파일에 분산

### Go 전환 시나리오

```
현재: backend/ (Python)
  ↓
전환 시점:
  1. backend/ → legacy-backend-py/ (보관)
  2. 새 backend/ 생성 (Go 코드)
  ↓
결과: docker-compose.yml 수정 없이 Go 서버 실행
```

## 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| `backend-py` / `backend-go` 분리 | 설정 파일 수정 필요, 복잡도 증가 |
| 단순 구조 (Open WebUI 스타일) | 파일 비대화, Go 전환 시 로직 파악 어려움 |
| 순수 레이어 구조 | MVP에서 과도한 추상화 |

## 참고 자료
- [OpenHands GitHub](https://github.com/All-Hands-AI/OpenHands)
- [Open WebUI GitHub](https://github.com/open-webui/open-webui)
- [LiteLLM GitHub](https://github.com/BerriAI/litellm)
- [FastAPI Project Structure Best Practices](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
