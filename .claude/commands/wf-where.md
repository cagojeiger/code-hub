## 현재 위치 확인

다음을 확인하여 현재 작업 상태를 파악하세요:

### 1. Roadmap 확인
docs/roadmap/*.md 파일을 읽고:
- "Status: In Progress" Roadmap 파일 찾기
- "Status: In Progress" Milestone 찾기
- `[ ]` 상태인 Task 목록 확인

### 2. Git 상태 확인
```bash
git branch --show-current
git status
```
- `feature/*` 브랜치 → 진행 중인 작업 있음
- `main` 또는 `dev` → 새 Task 시작 필요

### 3. 열린 PR 확인
```bash
gh pr list --state open
```
- 리뷰 대기 중인 PR 확인
- 머지 가능 여부 확인

---

**다음 단계**:
- 새 Task 시작 → `/wf-start` 실행
- Task 완료 → `/wf-done` 실행
