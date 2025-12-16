# Development Workflow

> AI + Human 협업을 위한 개발 프로세스 시각화

---

## 0. 역할 정의

| 역할 | AI | Human |
|------|-----|-------|
| 🔵 **계획** | spec 초안 제안, Roadmap/Task 시뮬레이션 | 피드백 후 최종 결정 |
| 🤖 **실행** | 브랜치 생성, 구현, 테스트, PR 생성, Notes 기록 | - |
| 🔵🤖 **리뷰** | Self-review, 설명 | 코드 리뷰, PR 승인/머지 |
| 🔵🤖 **트리아지** | Notes 정리, 분류 제안 | 최종 분류 결정 |

> **원칙**: AI는 **제안/실행**, Human은 **결정/승인**

---

## 1. 계획 수립 🔵

```mermaid
flowchart LR
    H1[🔵 Human 요청] --> AI[🤖 AI 제안]
    AI --> H2{🔵 Human}
    H2 -->|피드백| AI
    H2 -->|승인| DONE[실행으로]
```

### 핑퐁 흐름

```
🔵 Human: 요청/질문
🤖 AI: 제안
🔵 Human: 피드백 or 승인
   ↺ (반복)
```

### 계획 단계

1. **spec.md** - 기능 요구사항
2. **Roadmap** - Milestone 구조
3. **Task** - 작업 단위 + Exit Criteria

> 각 단계마다 핑퐁 후 Human 승인 시 다음 단계로

---

## 2. 실행 흐름 🔵🤖

```mermaid
flowchart TB
    subgraph Phase1["Phase 1: 계획"]
        ROADMAP_CHECK[Roadmap 확인]
        TASK_SELECT[Task 선택]
        EXIT_CONFIRM[Exit Criteria 확인]
    end

    subgraph Phase2["Phase 2: 실행"]
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
        EXIT_MET{Exit Criteria<br/>충족?}
        TASK_CHECK[Task 체크 ✓]
        NOTES_UPDATE[Notes 업데이트]
        ALL_DONE{모든 Task<br/>완료?}
    end

    subgraph Phase5["Phase 5: 트리아지"]
        TRIAGE[Notes 트리아지]
        FIX_NOW{FIX-NOW?}
        FIX_TASK[현재 Milestone에<br/>FIX Task 추가]
        MS_DONE[Milestone 완료]
        ROADMAP_DONE{Roadmap 완료?}
        NEXT_MS[다음 Milestone]
        RELEASE[Release/완료]
    end

    ROADMAP_CHECK --> TASK_SELECT --> EXIT_CONFIRM --> BRANCH
    BRANCH --> IMPL --> TEST --> PR_CREATE --> REVIEW
    REVIEW -->|수정 필요| FIX --> REVIEW
    REVIEW -->|승인| MERGE

    MERGE --> EXIT_MET
    EXIT_MET -->|No| NEW_BRANCH[추가 브랜치 생성]
    NEW_BRANCH --> IMPL
    EXIT_MET -->|Yes| TASK_CHECK --> NOTES_UPDATE --> ALL_DONE

    ALL_DONE -->|No| TASK_SELECT
    ALL_DONE -->|Yes| TRIAGE

    TRIAGE --> FIX_NOW
    FIX_NOW -->|Yes| FIX_TASK --> TASK_SELECT
    FIX_NOW -->|No| MS_DONE --> ROADMAP_DONE
    ROADMAP_DONE -->|No| NEXT_MS --> TASK_SELECT
    ROADMAP_DONE -->|Yes| RELEASE

    subgraph Phase6["Phase 6: 완료"]
        RELEASE --> NEXT_ROADMAP{다음 Roadmap?}
        NEXT_ROADMAP -->|Yes| NEW_ROADMAP[새 Roadmap 시작]
        NEXT_ROADMAP -->|No| PROJECT_DONE[프로젝트 완료]
    end

    NEW_ROADMAP --> ROADMAP_CHECK
```

### 핵심 용어 정의

| 용어 | 정의 |
|------|------|
| **Task 완료** | PR 머지 + Exit Criteria 충족 → `[x]` |
| **Task 종료** | REVERTED/취소 → `[x] ~~취소선~~` (Closed) |
| **모든 Task 완료** | Open 상태(`[ ]`) Task가 0개 |
| **Milestone 완료** | 모든 Task 완료 + 트리아지 + FIX-NOW 해결 |
| **트리아지 트리거** | 모든 Task 완료 시점 |
| **1 Task = 1 PR (기본)** | 예외적으로 N PR 허용 (리스크 분산, Exit 단계적 충족) |

### 가드레일

| 구분 | 규칙 | 설명 |
|------|------|------|
| 🔴 Hard | **Blocker 즉시 라우팅** | Task 완료 대기 없이 FIX/ADR/Issue 분기 |
| 🔴 Hard | **DROP은 Human 승인** | 사유 기록 필수 (ADR 또는 roadmap notes) |
| 🔴 Hard | **Revert 시 v2 필수** | 같은 Milestone 귀속 기본. 이동은 Human 승인 |
| 🟡 Soft | **dev green 유지** | "항상"이 아니라 "최대한 + 빨리 복구" |
| 🟡 Soft | **1 Task = 1 PR 기본** | N PR은 예외 (리스크 분산, Exit 단계적 충족) |
| 🟡 Soft | **FIX-NOW 컷** | Milestone 당 1~2회. 초과 시 ADR/Backlog로 이월 |

---

## 3. Roadmap → Milestone → Task → PR

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
        T2["Task 2"] --> PR2["PR #2"] --> PR2F["PR #3"]
        T3["Task 3"] --> PR3["PR #4"]
    end

    M1 -.-> Milestone
    M2 -.-> Milestone
    M3 -.-> Milestone
    M4 -.-> Milestone
    M5 -.-> Milestone
```

> 각 Milestone은 동일한 Task → PR 구조를 가짐 (점선은 "같은 패턴"을 의미)

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

## 4. Milestone 라이프사이클

```mermaid
stateDiagram-v2
    [*] --> Pending: Roadmap에 정의

    Pending --> InProgress: 이전 Milestone 완료

    InProgress --> TaskLoop: Task 선택

    state TaskLoop {
        [*] --> Pending_Task: Task 선택/생성
        Pending_Task --> Implement: 브랜치 생성
        Implement --> PR
        PR --> Review
        Review --> Merged: 승인
        Review --> Implement: 수정 요청
        Review --> Rejected: 방향성 거절
        Rejected --> Pending_Task: 재설계
        Merged --> ExitCheck: Exit Criteria 확인
        ExitCheck --> TaskCheck: 충족
        ExitCheck --> Implement: 미충족 → 추가 PR
        Merged --> Reverted: 버그 발견
        Reverted --> Pending_Task: 새 Task(v2) 정의
        TaskCheck --> [*]: 다음 Task
    }

    TaskLoop --> NotesTriage: 모든 Task 완료

    NotesTriage --> Completed: FIX-NOW 해결 완료
    NotesTriage --> InProgress: FIX-NOW 항목 존재

    Completed --> [*]
```

---

## 5. Task 라이프사이클

### 상태 흐름

```mermaid
stateDiagram-v2
    [*] --> Pending: Task 정의

    Pending --> InProgress: 브랜치 생성

    InProgress --> PR: PR 생성

    PR --> Review: 리뷰 요청

    Review --> Merged: 승인
    Review --> InProgress: 수정 요청
    Review --> Rejected: 방향성/설계 거절

    Rejected --> Pending: 재설계 후 재시작

    Merged --> ExitCheck: Exit Criteria 확인

    ExitCheck --> Completed: 충족
    ExitCheck --> InProgress: 미충족 → 추가 PR

    Merged --> Reverted: 버그 발견

    Reverted --> NewTask: 새 Task 생성 (v2)

    Completed --> [*]
```

> **핵심**: PR 머지 ≠ Task 완료. **Exit Criteria 충족**이 완료 조건.

### Review 결과 구분

| 상황 | 경로 | 설명 |
|------|------|------|
| **수정 요청** | Review → InProgress | 코드 품질 이슈 → 수정 후 재리뷰 |
| **Rejected** | Review → Rejected → Pending | 방향성/설계 거절 → 재설계 후 재시작 |
| **ExitCheck 미충족** | Merged → ExitCheck → InProgress | 기능 부족/누락 → 추가 PR |
| **Revert** | Merged → Reverted → NewTask | 버그/장애 발견 → PR 롤백 후 새 Task |

> **판단 기준**
> - 코드만 고치면 됨 → **수정 요청**
> - 접근 방식 자체가 잘못됨 → **Rejected**
> - 머지 후 기능 부족 → **ExitCheck 미충족**
> - 머지 후 버그 발견 → **Revert**
>
> **Rejected vs ExitCheck 구분**
> - "추가 구현"으로 Exit 충족 가능 → **ExitCheck 미충족** (머지 허용)
> - "구조/접근 교체" 없이 Exit 불가 → **Rejected** (머지 금지)

### Task 형식

```markdown
**Tasks**:
- [ ] Task 이름 (Exit: 완료 조건 한 줄)
- [x] 완료된 Task (PR #N)
- [x] ~~Task 이름~~ (CLOSED: PR #N REVERTED → v2로 대체)
- [ ] Task 이름 v2 (Exit: 완료 조건)
```

> **완료 판정 규칙**
> - `[x]` = **완료(Done)** 또는 **종료(Closed)**
> - `[ ]` = **진행 중(Open)**
> - "모든 Task 완료" = Open 상태 Task가 0개
> - REVERTED Task는 `[x] ~~취소선~~`으로 **Closed** 처리 후, 새 Task(v2)를 Open

### Exit Criteria 예시

| Task | Exit Criteria |
|------|---------------|
| Config 모듈 구현 | env-only로도 부팅 가능, 잘못된 값은 명확한 에러 |
| Auth Middleware | 유효한 세션 쿠키로 인증 통과, 만료 시 401 |
| Storage Provider | Provision/Deprovision 멱등성 테스트 통과 |

---

## 6. Notes 트리아지

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
        ALL_TASK_DONE[모든 Task 완료]
    end

    subgraph Collect["수집"]
        ALL_TASK_DONE --> NOTES[Notes 목록 확인]
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
        FIX --> BLOCK[현재 Milestone에서 해결]
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
| 🔴 FIX | Session lazy loading 문제 | 현재 Milestone에 FIX Task 추가 |
| 🟡 ADR | 환경변수 우선순위 | ADR-003 작성 |
| 🟠 ISSUE | YAML 파싱 느림 | Issue #1 생성 |
| ⚪ DROP | 에러 코드 체계 고민 | 현재로 충분 |
| ✅ DONE | SQLModel async 확인 | 동작 확인됨 |
```

---

## 7. 엣지 케이스 처리

| 상황 | 처리 |
|------|------|
| **Task 의존성 발견** | Notes 기록 → 의존 Task 먼저 진행 |
| **스펙 불완전/모순** | Notes 기록 → 스펙 수정 PR 먼저 |
| **Task가 너무 큼** | Task 분리 + Roadmap 수정 |
| **Blocker 발생** | 🔴 즉시 FIX/ADR/Issue 분기 (Hard 가드레일) |
| **AI 세션 중단** | Notes/Draft PR에 현재 상태 기록 |
| **PR Revert 필요** | 🔴 v2 Task 생성 (Hard 가드레일) |
| **PR 완전 거절** | Notes 기록 → Task 재설계 후 재시작 |

### AI가 "더 좋은 아키텍처" 제안 시

| Exit Criteria에 필요? | 처리 |
|----------------------|------|
| **Yes** | 현재 Task에서 구현 |
| **No** | Notes에 기록 → Milestone 트리아지에서 ADR/Backlog/Drop 결정 |

---

## 8. 문서 간 관계

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

    AGENTS --> WORKFLOW
    ROADMAP --> AGENTS

    GLOSSARY --> SPEC
    GLOSSARY --> ARCH
```

---

## 9. 브랜치 전략

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
feature/*   → dev  : PR 리뷰 후 머지
dev → main         : 릴리즈 준비 완료 시
```

> **브랜치 규칙**
> - `feature/*`: dev의 최신 HEAD에서 생성
> - **리뷰 수정**: 같은 PR에 커밋 추가 (새 브랜치 ❌)
> - **추가 PR** (ExitCheck 미충족): 새 feature/* 브랜치 생성

### 범위 경계

> **이 프로세스의 범위**: MVP 개발 단계까지
>
> 릴리즈 이후 발견된 버그(hotfix)는 별도 운영 프로세스로 처리.
> 필요시 `hotfix/*` 브랜치 전략을 별도 문서로 정의.

---

## 10. 체크리스트

### Task 시작 시

- [ ] 🤖 Roadmap에서 현재 Task 확인
- [ ] 🤖 spec.md에서 관련 섹션 읽기
- [ ] 🤖 architecture.md에서 컴포넌트 관계 확인
- [ ] 🤖 Exit Criteria 확인 (정의는 Task 생성 시 완료)

### PR 머지 후

- [ ] 🤖 Exit Criteria 충족 확인
- [ ] 🔵 충족 시: Task 체크 `- [x] Task (PR #N)`
- [ ] 🤖 미충족 시: 추가 작업 진행 (Task 미완료 유지)
- [ ] 🤖 Notes 업데이트 (필요시)

### Milestone 종료 시

- [ ] 🤖 모든 Task 완료 확인
- [ ] 🔵🤖 Notes 트리아지 실행
- [ ] 🤖 FIX-NOW 항목 해결
- [ ] 🔵 Status를 Completed로 변경

---

## 참조

- [AGENTS.md](../AGENTS.md) - AI 에이전트 가이드
- [spec.md](./spec.md) - 기능 스펙
- [architecture.md](./architecture.md) - 시스템 아키텍처
- [ADR-000: Repository Strategy](./adr/000-repository-strategy.md)
