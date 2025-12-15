# Contributing

> Issue는 생각이고, PR은 제안이며, Merge는 결정이다.

## 원칙

| 구분 | 역할 |
|------|------|
| Issue | Why - 왜 해야 하는가 |
| PR | How - 어떻게 했는가 |
| Merge | Decision - 채택 |

- 논의는 Issue에서 끝낸다
- PR은 조용하고 작게
- 1 PR = 1 변경

---

## Phase 1: Issue (Why)

```mermaid
sequenceDiagram
    participant H as Human
    participant A as AI
    participant GH as GitHub

    H->>A: 요청
    A->>H: Issue 초안
    loop 논의
        H->>A: 피드백
        A->>H: 수정
    end
    H->>A: 확인
    A->>GH: gh issue create
```

**Phase 1 완료 조건**: 논의 끝, 구현 방향 확정

---

## Phase 2: PR (How)

```mermaid
sequenceDiagram
    participant A as AI
    participant GH as GitHub
    participant H as Human

    A->>GH: Issue 읽기
    A->>A: 구현 + 테스트
    A->>GH: PR 생성 (Closes #N)
    loop 코드 리뷰
        H->>GH: 코드 피드백
        A->>GH: 수정
    end
    H->>GH: Merge
    GH->>GH: Issue 자동 종료
```

**PR 규칙**: 코드 피드백만, "왜?"는 묻지 않음
