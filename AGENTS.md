# AGENTS.md

> AI 에이전트와 협업하기 위한 프로세스 가이드

---

## 1. 프로젝트 개요

**code-hub**는 Cloud Development Environment (CDE) 플랫폼입니다.

- **현재 상태**: MVP 개발 중 (Python 3.13 + FastAPI)
- **목표**: 로컬 Docker 기반 code-server 워크스페이스 관리
- **추후 계획**: Go로 재작성 예정

### 핵심 문서

| 문서 | 설명 | 용도 |
|------|------|------|
| [docs/spec.md](docs/spec.md) | 상세 스펙 | 무엇을 만들지 |
| [docs/architecture.md](docs/architecture.md) | 시스템 아키텍처 | 어떻게 만들지 |
| [docs/glossary.md](docs/glossary.md) | 용어 정의 | 공통 언어 |

---

## 2. 문서 구조

```
docs/
├── spec.md              # 상세 스펙 (무엇을)
├── architecture.md      # 시스템 아키텍처 (어떻게)
├── glossary.md          # 용어 정의
├── adr/                 # 아키텍처 결정 기록
│   ├── 000-*.md
│   ├── 001-*.md
│   └── ...
└── roadmap/             # 로드맵 + 마일스톤 (작업 관리)
    ├── 000-mvp.md
    ├── 001-v1.md        # (추후)
    └── ...
```

### 문서 역할

| 문서 | 역할 | 변경 빈도 |
|------|------|----------|
| spec.md | 기능 정의 (What) | 낮음 |
| architecture.md | 설계 (How) | 낮음 |
| adr/*.md | 결정 기록 (Why) | 결정 시마다 |
| roadmap/*.md | 작업 추적 | 매일 |

---

## 3. 개발 프로세스

### Roadmap → Milestone → Task → PR

```
docs/roadmap/000-mvp.md
├── Milestone 1: Foundation
│   ├── Task: Config 모듈 구현 → PR #1
│   ├── Task: Errors 모듈 구현 → PR #2
│   └── Task: DB/Models 구현 → PR #3
├── Milestone 2: Infrastructure
│   ├── Task: Storage Provider → PR #4
│   └── Task: Instance Controller → PR #5, #6, #7
└── ...
```

### 로드맵 파일 형식

```markdown
# Roadmap 000: MVP

## Status: In Progress

## Milestones

### M1: Foundation
**Status**: In Progress

**Tasks**:
- [x] pyproject.toml 설정 (PR #2)
- [ ] Config 모듈 구현
- [ ] Errors 모듈 구현
- [ ] DB/Models 구현

**Notes**:
- 2024-12-16: uv를 패키지 매니저로 선택
- 2024-12-17: SQLModel async 지원 확인 필요

---

### M2: Infrastructure
**Status**: Pending

**Tasks**:
- [ ] Storage Provider 구현
- [ ] Instance Controller: StartWorkspace
- [ ] Instance Controller: StopWorkspace
- [ ] Instance Controller: DeleteWorkspace

**Notes**:
(아직 없음)
```

### 프로세스 흐름

```
1. docs/roadmap/*.md에서 현재 Milestone 확인
2. Task 선택 → 브랜치 생성 (feature/*)
3. docs/spec.md 참조하여 구현
4. PR 생성 → 리뷰 → 머지
5. Task 체크: - [x] Task 이름 (PR #N)
6. 작업 중 발견한 관찰/우려 → Notes에 기록
7. Milestone 완료 → Status를 Completed로 변경
8. Roadmap 완료 → 다음 Roadmap 파일 생성
```

---

## 4. 핵심 규칙

### 4.1 기본 규칙

| 규칙 | 설명 |
|------|------|
| **스펙 우선** | 구현 전 반드시 spec.md 확인 |
| **1 Task = 1 PR** | Task가 크면 분리 |
| **Notes 기록** | 발견한 이슈/우려는 즉시 Notes에 기록 |
| **ADR 작성** | 아키텍처 수준 결정은 ADR로 기록 |

### 4.2 GitHub Issue 정책

| 상황 | 처리 방법 |
|------|----------|
| **일반 작업** | Issue 없음. Roadmap으로 관리 |
| **버그 발견** | GitHub Issue 생성 |
| **외부 도움 필요** | GitHub Issue 생성 |

### 4.3 브랜치 전략

```
main (안정된 릴리즈)
  ↑
dev (개발 통합)
  ↑
feature/* (기능 개발)
docs/* (문서 작업)
```

참조: [ADR-000: Repository Strategy](docs/adr/000-repository-strategy.md)

---

## 5. 엣지 케이스 처리

### Case 1: Task 간 의존성 발견

**상황**: Config 구현 중 Errors 모듈이 먼저 필요함을 발견

**처리**:
1. 현재 작업 중단
2. Notes에 기록: `- YYYY-MM-DD: Config 작업 중 Errors 의존성 발견`
3. Errors Task 먼저 진행
4. 완료 후 Config 재개

### Case 2: 스펙이 불완전/모순

**상황**: spec.md에 정의가 없거나 모순됨

**처리**:
1. Notes에 발견 내용 기록
2. spec.md 수정 PR 먼저 생성 (별도 커밋)
3. 스펙 수정 머지 후 구현 진행

```
커밋 메시지: docs(spec): clarify healthcheck endpoint path
```

### Case 3: Task가 예상보다 큼

**상황**: "Instance Controller 구현"이 500줄 이상

**처리**:
1. Task를 여러 개로 분리
2. Roadmap 수정:

```markdown
**Tasks**:
- [ ] Instance Controller: StartWorkspace
- [ ] Instance Controller: StopWorkspace
- [ ] Instance Controller: DeleteWorkspace
- [ ] Instance Controller: ResolveUpstream/GetStatus
```

**원칙**: 작은 Task = 작은 PR = 리뷰 용이 = 롤백 용이

### Case 4: Notes의 우려가 블로커가 됨

**상황**: Notes에 기록한 우려가 실제 블로킹 이슈가 됨

**처리 흐름**:
```
Notes의 우려 현실화
    ↓
해결 방법이 명확? → 그냥 구현
    ↓
여러 선택지 존재? → ADR로 승격
    ↓
외부 도움 필요? → GitHub Issue 생성
```

**Notes 업데이트**:
```
- YYYY-MM-DD: docker-py sync 이슈 → ADR-003으로 결정
```

### Case 5: Milestone 경계를 넘는 의존성

**상황**: M1 작업 중 M3의 모델이 필요

**처리**:
1. Milestone 범위 조정 (해당 Task를 현재 Milestone으로 이동)
2. Notes에 기록: `- YYYY-MM-DD: Session 모델이 M1에 필요. M1에 포함.`

### Case 6: AI 세션 중단 (컨텍스트 한계)

**상황**: 긴 작업 중 세션이 끊김

**처리**: Notes에 현재 상태 상세 기록
```markdown
**Notes**:
- YYYY-MM-DD: Instance Controller 구현 중단
  - 완료: container create, start
  - 미완료: health check polling
  - 현재 브랜치: feature/instance-controller
```

**다음 세션**: Notes 확인 → 이어서 작업

### Case 7: PR 리뷰에서 큰 변경 요청

**상황**: "이 접근 자체가 잘못됨" 피드백

**처리**:
1. Force push로 수정 (리뷰 이력 유지)
2. Notes에 변경 이유 기록:
```
- YYYY-MM-DD: Storage Provider 인터페이스 재설계. existing_ctx 파라미터 추가.
```

### Case 8: 구현 중 버그 발견

**상황**: M3 작업 중 M1 코드에서 버그 발견

**처리**:
| 버그 심각도 | 처리 방법 |
|------------|----------|
| 사소함 | 현재 PR에 별도 커밋으로 수정 |
| 중요함 | GitHub Issue 생성 |

```
커밋 메시지: fix(config): handle missing env file gracefully
```

### Case 9: 스펙에 없는 작은 결정

**상황**: 에러 메시지 문구, 로그 레벨 등

**처리**:
| 결정 크기 | 처리 방법 |
|----------|----------|
| 아키텍처 수준 | ADR 작성 |
| 구현 방향 | Notes에 기록 |
| 코드 레벨 | 코드 주석 또는 그냥 결정 |

---

## 6. Notes 관리

### Notes의 목적

- 작업 중 발견한 **관찰/우려/의문점** 기록
- "vibe coding" 방지 (빠른 코드 생성 → 기술 부채 축적 방지)
- 컨텍스트 보존 (세션 간 정보 유지)

### Notes 형식

```markdown
**Notes**:
- YYYY-MM-DD: [관찰/우려 내용]
- YYYY-MM-DD: [내용] → [해결 방법/결과]
```

### Notes가 많아질 때

```markdown
**Notes**:
<!-- 해결된 항목은 접어두기 -->
<details>
<summary>해결됨 (15개)</summary>

- 2024-12-10: Config 로딩 순서 문제 → 해결 (PR #5)
- ...
</details>

<!-- 미해결 항목만 표시 -->
- 2024-12-20: docker-py 메모리 누수 의심 → 조사 필요
```

### Notes → ADR 승격 기준

| 기준 | Notes 유지 | ADR로 승격 |
|------|-----------|-----------|
| 영향 범위 | 단일 파일/기능 | 여러 컴포넌트 |
| 결정 복잡도 | 명확한 해결책 | 여러 선택지 트레이드오프 |
| 재사용성 | 일회성 | 패턴으로 재사용 |

---

## 7. 작업 시작 체크리스트

새 Task 시작 시:

- [ ] docs/roadmap/*.md에서 현재 Task 확인
- [ ] docs/spec.md에서 관련 섹션 읽기
- [ ] docs/architecture.md에서 컴포넌트 관계 확인
- [ ] 관련 ADR 확인 (있다면)
- [ ] 브랜치 생성: `git checkout -b feature/{task-name}`

PR 생성 시:

- [ ] 테스트 통과 확인
- [ ] PR description에 Task 링크 포함
- [ ] 스펙 변경이 있다면 별도 커밋으로 분리

머지 후:

- [ ] Roadmap에서 Task 체크: `- [x] Task 이름 (PR #N)`
- [ ] Notes 업데이트 (필요시)

---

## 8. 커밋 메시지 규칙

```
<type>(<scope>): <subject>

[body]

[footer]
```

### Type

| Type | 설명 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| docs | 문서 변경 |
| refactor | 리팩토링 |
| test | 테스트 추가/수정 |
| chore | 빌드, 설정 변경 |

### 예시

```
feat(workspace): add StartWorkspace API endpoint

Implement POST /api/v1/workspaces/{id}:start
- Add status transition CREATED/STOPPED → PROVISIONING
- Integrate with Storage Provider and Instance Controller

Refs: docs/spec.md#StartWorkspace
```

---

## 9. 참조 문서

### 프로젝트 문서
- [docs/spec.md](docs/spec.md) - 상세 스펙
- [docs/architecture.md](docs/architecture.md) - 시스템 아키텍처
- [docs/glossary.md](docs/glossary.md) - 용어 정의

### ADR (Architecture Decision Records)
- [ADR-000: Repository Strategy](docs/adr/000-repository-strategy.md)
- [ADR-001: MVP Tech Stack](docs/adr/001-mvp-tech-stack.md)
- [ADR-002: MVP Project Structure](docs/adr/002-mvp-project-structure.md)

### Roadmap
- [Roadmap 000: MVP](docs/roadmap/000-mvp.md) (생성 예정)
