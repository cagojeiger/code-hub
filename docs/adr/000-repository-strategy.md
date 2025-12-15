# ADR-000: Repository & Branch Strategy

## 상태
Accepted

## 컨텍스트
code-hub는 MVP(Python)와 Production(Go) 두 버전을 개발할 예정입니다.
- MVP: 빠른 검증 목적, Python 3.13 + FastAPI
- Production: 스케일 확장, Go 재작성

## 결정

### 브랜치 전략

| 브랜치 | 용도 | 언어 |
|--------|------|------|
| `dev` | Python MVP | Python 3.13 |
| `main` | Go Production (추후) | Go |

### 워크플로우

```
dev (Python MVP)
  │
  └── feature branches → PR to dev
        ↓
      MVP 검증 완료
        ↓
main (Go Production, 추후)
  │
  └── feature branches → PR to main
```

### 문서 관리

- `docs/`: 각 브랜치에서 독립적으로 관리
- MVP 검증 후 Go 재작성 시 문서도 함께 이관

## 결과

### 장점
- 단일 리포지토리로 이슈/PR 통합 관리
- 단순한 구조 (dev/main 2개 브랜치)
- MVP/Production이 명확히 구분됨

### 단점
- Python ↔ Go 브랜치 간 코드 머지 불가
- 브랜치 간 전환 시 환경 재설정 필요
- Go 개발 전까지 main이 빈 상태

## 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| 별도 리포지토리 | 문서 동기화 복잡, 이슈 분산 |
| 모노레포 (python/ + go/) | CI/CD 복잡도 증가 |
| Strangler Fig 패턴 | MVP 스케일에서 불필요한 복잡도 |

## 참고 자료
- [Martin Fowler - Branching Patterns](https://martinfowler.com/articles/branching-patterns.html)
- [GitLab - Branching Strategies](https://docs.gitlab.com/user/project/repository/branches/strategies/)
