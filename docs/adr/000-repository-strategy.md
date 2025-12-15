# ADR-000: Repository & Branch Strategy

## 상태
Accepted

## 컨텍스트
code-hub 개발을 위한 브랜치 전략이 필요합니다.
- 목표: 일반적인 Git 워크플로우 준수
- MVP는 Python 3.13 + FastAPI로 개발
- Production 스케일 확장 시 Go 재작성 검토

## 결정

### 브랜치 전략

| 브랜치 | 용도 |
|--------|------|
| `main` | 안정된 릴리즈 |
| `dev` | 개발 통합 |
| `feature/*` | 기능 개발 |
| `docs/*` | 문서 작업 |

### 워크플로우

```
main (안정된 릴리즈)
  ↑
dev (개발 통합)
  ↑
feature/* (기능 개발)
```

### 머지 규칙

- `feature/*` → `dev`: PR 리뷰 후 머지
- `dev` → `main`: 릴리즈 준비 완료 시 머지

## 결과

### 장점
- 업계 표준 워크플로우
- 단순하고 이해하기 쉬움
- CI/CD 연동 용이

### 단점
- Go 재작성 시 전략 재검토 필요

## 참고 자료
- [Martin Fowler - Branching Patterns](https://martinfowler.com/articles/branching-patterns.html)
- [GitLab - Branching Strategies](https://docs.gitlab.com/user/project/repository/branches/strategies/)
