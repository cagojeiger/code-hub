# ADR-013: OpenAPI를 API 스키마 SSOT로 사용

## 상태
Accepted

## 컨텍스트

### 문제 상황

WC(Control Plane)와 Agent는 HTTP API로 통신한다. 양쪽에서 동일한 요청/응답 스키마를 사용해야 하는데, 이 스키마가 두 곳에 중복 정의되어 있었다:

```
src/codehub/core/interfaces/runtime.py      # WC용 스키마
src/codehub_agent/api/v1/schemas.py         # Agent용 스키마 (수동 작성)
```

### 핵심 문제

**스키마 동기화 어려움**: API 변경 시 두 파일을 수동으로 동기화해야 한다. 하나를 수정하고 다른 하나를 잊으면 런타임 에러가 발생한다.

**리뷰 어려움**: 스키마가 코드에 흩어져 있어 API 계약을 한눈에 파악하기 어렵다.

**문서화 부재**: API 스펙 문서가 코드와 분리되어 있어 outdated될 가능성이 높다.

## 결정

**OpenAPI 3.1 스펙을 API 스키마의 Single Source of Truth로 사용**한다.

### 구조

```
api/
└── openapi.yaml                           # SSOT

scripts/
└── generate-schemas.sh                    # 코드 생성

src/
├── codehub_agent/
│   └── api/v1/
│       └── schemas.py                     # Agent용 (자동 생성, 수정 금지)
└── codehub/
    └── core/schemas/
        └── agent_api.py                   # WC용 (자동 생성, 수정 금지)

.github/workflows/
└── api-validation.yml                     # CI 검증
```

> **두 스키마 파일**: Agent와 WC가 동일한 OpenAPI 스펙에서 각자의 스키마를 생성합니다.

### 워크플로우

```
1. openapi.yaml 수정
2. ./scripts/generate-schemas.sh 실행
3. 커밋 & PR
4. CI가 스펙 유효성 + 코드 동기화 검증
```

### 도구

- **datamodel-codegen**: OpenAPI → Pydantic 모델 생성
- **openapi-spec-validator**: 스펙 유효성 검사

## 장점

### SSOT (Single Source of Truth)
OpenAPI 파일 하나만 수정하면 된다. 중복이 없으므로 동기화 문제가 발생하지 않는다.

### 자동 생성
수동으로 Pydantic 모델을 작성할 필요가 없다. 스펙에서 자동 생성되므로 오타나 누락이 없다.

### CI 검증
PR에서 자동으로 스펙 유효성과 코드 동기화를 검증한다. 스키마 불일치가 main에 머지되는 것을 방지한다.

### 문서화
OpenAPI 스펙 자체가 API 문서 역할을 한다. Swagger UI 등으로 시각화할 수 있다.

## 단점

### 의존성 추가
`datamodel-code-generator`와 `openapi-spec-validator` dev 의존성이 필요하다.

### @property 메서드 불가
생성된 코드에는 비즈니스 로직(@property 등)을 추가할 수 없다. **해결책**: `ConditionInput.from_workspace_state()`에서 비즈니스 로직 처리 (서비스 레이어 분리).

### 러닝 커브
OpenAPI 스펙 작성법을 알아야 한다. 복잡한 스키마는 YAML 작성이 번거롭다.

## 대안 (선택하지 않음)

### 공유 패키지 (shared-schemas)
Agent가 WC의 스키마를 import하는 방식.

미선택 이유: 패키지 간 의존성이 생긴다. Agent가 WC에 의존하면 독립 배포가 어렵다.

### 수동 동기화 유지
기존처럼 두 곳에 스키마를 수동 관리.

미선택 이유: 동기화 누락 위험이 높다. 이미 여러 번 불일치 문제가 발생했다.

### gRPC + Protocol Buffers
gRPC를 사용하면 스키마가 자동 동기화된다.

미선택 이유: 기존 REST API를 전면 교체해야 한다. 마이그레이션 비용이 너무 크다.

## 관련 문서

| 문서 | 설명 |
|------|------|
| [ADR-010](./010-package-separation.md) | 패키지 분리 아키텍처 |
| [spec/06-docker-agent.md](../spec/06-docker-agent.md) | Agent API 상세 스펙 |
| [api/openapi.yaml](../../api/openapi.yaml) | OpenAPI 스펙 (SSOT) |
