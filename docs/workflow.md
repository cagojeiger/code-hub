# Development Workflow

> AI + Human 협업을 위한 개발 프로세스 시각화

---

## 1. 전체 프로세스 개요

```mermaid
flowchart TB
    subgraph Documents["문서 체계"]
        SPEC[spec.md<br/>What to build]
        ARCH[architecture.md<br/>How to build]
        ADR[adr/*.md<br/>Why decisions]
        ROADMAP[roadmap/*.md<br/>Progress tracking]
    end

    subgraph Process["개발 프로세스"]
        direction TB
        R[Roadmap] --> M[Milestone]
        M --> T[Task]
        T --> PR[Pull Request]
        PR --> MERGE[Merge]
        MERGE --> |Task 완료| M
        M --> |Milestone 완료| TRIAGE[Notes Triage]
        TRIAGE --> |다음| M2[Next Milestone]
    end

    SPEC --> T
    ARCH --> T
    ADR --> T
    T --> ROADMAP
```

---

## 2. Roadmap → Milestone → Task → PR

### 계층 구조

```mermaid
flowchart LR
    subgraph Roadmap["docs/roadmap/000-mvp.md"]
        direction TB
        M1["M1: Foundation"]
        M2["M2: Infrastructure"]
        M3["M3: Auth"]
        M4["M4: Workspace"]
        M5["M5: Proxy & E2E"]
    end

    subgraph Milestone["Milestone 상세"]
        direction TB
        T1["Task 1"] --> PR1["PR #1"]
        T2["Task 2"] --> PR2["PR #2"]
        T3["Task 3"] --> PR3["PR #3"]
    end

    M1 --> Milestone
```

### 진행 상태

```
Roadmap 000: MVP
├── M1: Foundation ✅ Completed
├── M2: Infrastructure ✅ Completed
├── M3: Auth 🔄 In Progress ← 현재 위치
├── M4: Workspace ⏳ Pending
└── M5: Proxy & E2E ⏳ Pending
```

---

## 3. Milestone 라이프사이클

```mermaid
stateDiagram-v2
    [*] --> Pending: Roadmap에 정의

    Pending --> InProgress: 이전 Milestone 완료

    InProgress --> TaskLoop: Task 선택

    state TaskLoop {
        [*] --> Implement
        Implement --> PR
        PR --> Review
        Review --> Merged: 승인
        Review --> Implement: 수정 요청
        Merged --> TaskCheck
        TaskCheck --> [*]: 다음 Task
    }

    TaskLoop --> NotesTriage: 모든 Task 완료

    NotesTriage --> Completed: FIX-NOW 해결 완료
    NotesTriage --> InProgress: FIX-NOW 항목 존재

    Completed --> [*]
```

---

## 4. Task 라이프사이클

### 상태 흐름

```mermaid
stateDiagram-v2
    [*] --> Pending: Task 정의

    Pending --> InProgress: 브랜치 생성

    InProgress --> PR: PR 생성

    PR --> Review: 리뷰 요청

    Review --> Merged: 승인
    Review --> InProgress: 수정 요청

    Merged --> Completed: Exit Criteria 충족
    Merged --> Reverted: 버그 발견

    Reverted --> InProgress: 재구현 (v2)

    Completed --> [*]
```

### Task 형식

```markdown
**Tasks**:
- [ ] Task 이름 (Exit: 완료 조건 한 줄)
- [x] 완료된 Task (PR #N)
- [x] Reverted Task (PR #N) **REVERTED in PR #M**
```

### Exit Criteria 예시

| Task | Exit Criteria |
|------|---------------|
| Config 모듈 구현 | env-only로도 부팅 가능, 잘못된 값은 명확한 에러 |
| Auth Middleware | 유효한 세션 쿠키로 인증 통과, 만료 시 401 |
| Storage Provider | Provision/Deprovision 멱등성 테스트 통과 |

---

## 5. Notes 트리아지

### 왜 필요한가?

```
Notes만 쌓이고 Act가 없으면:
Month 1: Notes 8개 → "관리 가능"
Month 3: Notes 24개 → "나중에 정리"
Month 5: Notes 40개 → 💥 기술 부채 폭발
```

### 트리아지 흐름

```mermaid
flowchart TD
    subgraph Trigger["트리거"]
        MS_END[Milestone 종료]
    end

    subgraph Collect["수집"]
        MS_END --> NOTES[Notes 목록 확인]
    end

    subgraph Classify["분류"]
        NOTES --> ASSESS{각 Note 평가}

        ASSESS -->|즉시 해결 필요| FIX["🔴 FIX-NOW"]
        ASSESS -->|아키텍처 결정 필요| ADR["🟡 ADR"]
        ASSESS -->|조사/실험 필요| ISSUE["🟠 ISSUE"]
        ASSESS -->|중요하지 않음| DROP["⚪ DROP"]
        ASSESS -->|이미 해결됨| DONE["✅ DONE"]
    end

    subgraph Act["행동"]
        FIX --> BLOCK[다음 Milestone 시작 전 해결]
        ADR --> ADR_DOC[ADR 문서 작성]
        ISSUE --> GH_ISSUE[GitHub Issue 생성]
        DROP --> ARCHIVE[기록만 남김]
        DONE --> ARCHIVE
    end

    subgraph Gate["게이트"]
        BLOCK --> CHECK{FIX-NOW 해결?}
        CHECK -->|No| BLOCK
        CHECK -->|Yes| NEXT[다음 Milestone 시작]
        ADR_DOC --> NEXT
        GH_ISSUE --> NEXT
        ARCHIVE --> NEXT
    end
```

### 트리아지 결과 기록

```markdown
**Notes Triage (M1 종료)**:

| 분류 | 항목 | 처리 |
|------|------|------|
| 🔴 FIX | Session lazy loading 문제 | M2 시작 전 해결 |
| 🟡 ADR | 환경변수 우선순위 | ADR-003 작성 |
| 🟠 ISSUE | YAML 파싱 느림 | Issue #1 생성 |
| ⚪ DROP | 에러 코드 체계 고민 | 현재로 충분 |
| ✅ DONE | SQLModel async 확인 | 동작 확인됨 |
```

---

## 6. 엣지 케이스 처리

```mermaid
flowchart TD
    subgraph EdgeCases["엣지 케이스"]
        E1[Task 의존성 발견]
        E2[스펙 불완전/모순]
        E3[Task가 너무 큼]
        E4[Notes가 블로커]
        E5[AI 세션 중단]
        E6[PR Revert 필요]
        E7[AI가 아키텍처 제안]
    end

    E1 --> |Notes 기록| A1[의존 Task 먼저 진행]
    E2 --> |Notes 기록| A2[스펙 수정 PR 먼저]
    E3 --> A3[Task 분리 + Roadmap 수정]
    E4 --> D4{해결 방법?}
    E5 --> A5[Notes에 상태 상세 기록]
    E6 --> A6[REVERTED 표시 + 재구현]
    E7 --> D7{Exit Criteria에 필요?}

    D4 -->|명확| A4a[그냥 구현]
    D4 -->|여러 선택지| A4b[ADR 작성]
    D4 -->|외부 도움| A4c[Issue 생성]

    D7 -->|Yes| A7a[현재 Task에서 구현]
    D7 -->|No| A7b[Notes에 기록 + 봉인]
```

### Case: AI가 매 PR마다 "더 좋은 아키텍처" 제안

```mermaid
flowchart TD
    AI_SUGGEST[AI: 이 구조가 더 나을 것 같습니다]

    AI_SUGGEST --> CHECK{Exit Criteria에 필요?}

    CHECK -->|Yes| IMPL[현재 Task에서 구현]
    CHECK -->|No| DEFER[Notes에 기록]

    DEFER --> MILESTONE_END[Milestone 종료 시]
    MILESTONE_END --> TRIAGE[트리아지]

    TRIAGE -->|대안 비교 필요| ADR[ADR로 승격]
    TRIAGE -->|나중에| BACKLOG[Backlog]
    TRIAGE -->|불필요| DROP[Drop]
```

---

## 7. 문서 간 관계

```mermaid
flowchart LR
    subgraph Core["핵심 문서"]
        SPEC[spec.md<br/>기능 정의]
        ARCH[architecture.md<br/>시스템 설계]
    end

    subgraph Decisions["결정 기록"]
        ADR[adr/*.md<br/>왜 이렇게?]
    end

    subgraph Execution["실행"]
        ROADMAP[roadmap/*.md<br/>진행 상황]
        WORKFLOW[workflow.md<br/>프로세스]
        AGENTS[AGENTS.md<br/>AI 가이드]
    end

    subgraph Reference["참조"]
        GLOSSARY[glossary.md<br/>용어 정의]
    end

    SPEC --> ROADMAP
    ARCH --> ROADMAP
    ADR --> ROADMAP

    WORKFLOW --> AGENTS
    ROADMAP --> AGENTS

    GLOSSARY --> SPEC
    GLOSSARY --> ARCH
```

---

## 8. 브랜치 전략

```mermaid
gitGraph
    commit id: "Initial"
    branch dev
    checkout dev
    commit id: "Setup"

    branch feature/config
    checkout feature/config
    commit id: "feat: config"
    checkout dev
    merge feature/config id: "PR #1"

    branch feature/errors
    checkout feature/errors
    commit id: "feat: errors"
    checkout dev
    merge feature/errors id: "PR #2"

    branch feature/models
    checkout feature/models
    commit id: "feat: models"
    commit id: "fix: typo"
    checkout dev
    merge feature/models id: "PR #3"

    checkout main
    merge dev id: "Release MVP"
```

### 머지 규칙

```
feature/* → dev    : PR 리뷰 후 머지
dev → main         : 릴리즈 준비 완료 시
```

---

## 9. 전체 흐름 요약

```mermaid
flowchart TB
    subgraph Phase1["Phase 1: 계획"]
        SPEC_READ[spec.md 읽기]
        ARCH_READ[architecture.md 읽기]
        ROADMAP_CHECK[Roadmap 확인]
    end

    subgraph Phase2["Phase 2: 실행"]
        TASK_SELECT[Task 선택]
        BRANCH[브랜치 생성]
        IMPL[구현]
        TEST[테스트]
        PR_CREATE[PR 생성]
    end

    subgraph Phase3["Phase 3: 리뷰"]
        REVIEW[코드 리뷰]
        FIX[수정]
        MERGE[머지]
    end

    subgraph Phase4["Phase 4: 정리"]
        TASK_CHECK[Task 체크]
        NOTES_UPDATE[Notes 업데이트]
        TRIAGE{Milestone 종료?}
        NOTES_TRIAGE[Notes 트리아지]
    end

    SPEC_READ --> ARCH_READ --> ROADMAP_CHECK
    ROADMAP_CHECK --> TASK_SELECT
    TASK_SELECT --> BRANCH --> IMPL --> TEST --> PR_CREATE
    PR_CREATE --> REVIEW
    REVIEW -->|수정 필요| FIX --> REVIEW
    REVIEW -->|승인| MERGE
    MERGE --> TASK_CHECK --> NOTES_UPDATE --> TRIAGE
    TRIAGE -->|No| TASK_SELECT
    TRIAGE -->|Yes| NOTES_TRIAGE
    NOTES_TRIAGE --> TASK_SELECT
```

---

## 10. 체크리스트

### Task 시작 시

- [ ] Roadmap에서 현재 Task 확인
- [ ] spec.md에서 관련 섹션 읽기
- [ ] architecture.md에서 컴포넌트 관계 확인
- [ ] Exit Criteria 확인/정의

### PR 머지 후

- [ ] Task 체크: `- [x] Task (PR #N)`
- [ ] Notes 업데이트 (필요시)

### Milestone 종료 시

- [ ] 모든 Task 완료 확인
- [ ] Notes 트리아지 실행
- [ ] FIX-NOW 항목 해결
- [ ] Status를 Completed로 변경

---

## 참조

- [AGENTS.md](../AGENTS.md) - AI 에이전트 가이드
- [spec.md](./spec.md) - 기능 스펙
- [architecture.md](./architecture.md) - 시스템 아키텍처
- [ADR-000: Repository Strategy](./adr/000-repository-strategy.md)
