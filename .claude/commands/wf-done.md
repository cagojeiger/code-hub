## Task 완료 체크리스트

### PR 생성 전

1. **Exit Criteria 충족 확인**
   - 모든 조건 테스트/확인 완료
   - 증거 준비 (테스트 로그, 스크린샷 등)

2. **설계 문서 정합성 검토**
   - `/wf-review` 실행하여 spec/architecture/ADR 위배 확인

3. **Notes 업데이트**
   - 발견한 이슈나 개선점 → Roadmap Notes에 기록
   - Blocker 발생 시 → 즉시 라우팅 (FIX/ADR/Issue)

4. **PR 생성**
   - Exit Criteria 증거 포함
   - Human 리뷰 요청

### PR 머지 후

5. **Task 체크 (Human 승인 후)**
   - `[ ]` → `[x]` 변경
   - PR 번호 추가: `(PR #N)`

6. **다음 단계**
   - 남은 Task 있음 → `/wf-start` 실행
   - Milestone 완료 → 트리아지 실행

---

**관련 문서**:
- [workflow.md](docs/workflow.md) - Phase 4: 정리
- [workflow.md](docs/workflow.md) - Phase 5: 트리아지
