# ADR-002: MVP Project Structure

## 상태
Accepted

## 컨텍스트
code-hub MVP 개발을 위한 프로젝트 구조 결정이 필요합니다.
- 목표: 빠른 개발 및 검증
- MVP는 Python + FastAPI로 개발
- 추후 Go로 재작성 예정
- 프론트엔드 추가 예정

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

### 2. 백엔드 레이어 구조

```
backend/
├── app/
│   ├── main.py               # FastAPI 엔트리포인트
│   │
│   ├── api/                  # [Interface Layer] HTTP 요청/응답 처리
│   │   └── v1/               # API 버전 관리
│   │
│   ├── services/             # [Business Logic Layer] 스펙 컴포넌트 구현
│   │                         # 예: Instance Controller, Storage Provider, ...
│   │
│   ├── db/                   # [Data Layer] 모델, DB 연결
│   │
│   └── core/                 # [Shared] 설정, 예외, 의존성
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

## 결과

### 장점
- **Go 전환 용이**: `services/` 폴더만 보면 핵심 로직 파악 가능
- **프론트엔드 추가 용이**: `frontend/` 폴더만 추가하면 됨
- **설정 변경 최소화**: `backend` 폴더명 유지로 docker-compose 등 수정 불필요
- **빠른 MVP 개발**: SQLModel로 중복 코드 제거

### 단점
- 초기 폴더 수가 많아 보일 수 있음

### 대안 (고려했으나 선택 안 함)
| 대안 | 미선택 이유 |
|------|------------|
| `backend-py` / `backend-go` 분리 | 설정 파일 수정 필요, 복잡도 증가 |
| 단순 구조 (Open WebUI 스타일) | 파일 비대화, Go 전환 시 로직 파악 어려움 |
| 도메인 기반 구조 | MVP 규모에서 과도한 분리 |

## 참고 자료
- [OpenHands GitHub](https://github.com/All-Hands-AI/OpenHands)
- [Open WebUI GitHub](https://github.com/open-webui/open-webui)
- [LiteLLM GitHub](https://github.com/BerriAI/litellm)
- [FastAPI Project Structure Best Practices](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
