# AGENTS.md

> Execution guide for workflow.md process

---

## 1. Find Current Location

```
1. Find file with "Status: In Progress" in docs/roadmap/
2. Find Milestone marked "In Progress" in that file
3. Tasks with [ ] = current work target
```

---

## 2. Phase Checklist

### Phase 1: Plan
- [ ] Check Task in Roadmap
- [ ] Read related section in spec.md
- [ ] Confirm Exit Criteria

### Phase 2: Execute
- [ ] Create branch (`feature/{task-name}`)
- [ ] Implement
- [ ] Test
- [ ] Create PR

### Phase 3: Review
- [ ] Wait for Human review
- [ ] If changes requested → fix and re-review

### Phase 4: Wrap-up
- [ ] Check Exit Criteria met
- [ ] If met → mark `[x]`
- [ ] If not met → additional PR (keep Task open)
- [ ] Update Notes

### Phase 5: Triage (when all Tasks done)
- [ ] Review Notes list
- [ ] Propose classification (FIX/ADR/ISSUE/DROP/DONE)
- [ ] Wait for Human approval

---

## 3. Boundaries

### ✅ Always Do
- Read spec.md before starting work
- Confirm Exit Criteria
- Record findings in Notes immediately
- Route Blockers immediately

### ⚠️ Ask Human First
- DROP/DEFER decisions
- Multiple PRs (1 Task = 1 PR default)
- Writing new ADR
- Moving Task between Milestones

### 🚫 Never Do
- Implement without reading spec
- Mark Task done without Exit Criteria check
- Skip v2 Task after Revert

---

## 4. Decision Routing

```
Problem occurs
    ↓
├── Can fix immediately? → Fix it
├── Architecture decision needed? → ADR (⚠️)
├── External help needed? → Create Issue
└── Cannot proceed (Blocker)? → Route immediately
```

---

## 5. Release Checklist

버전 릴리스 시 아래 순서를 따른다.

1. `src/codehub/__init__.py`에서 `__version__` 업데이트
2. `CHANGELOG.md`에 해당 버전 항목 추가
3. version bump 커밋 (`chore: bump version to X.Y.Z`)
4. main 머지 후 tag 생성 (`git tag vX.Y.Z && git push origin vX.Y.Z`)
5. **GitHub Release 생성** (`gh release create vX.Y.Z`)
   - 릴리스 노트: 이전 tag 이후 커밋에서 feat/fix 분류
   - Compatibility 테이블 포함
   - Full Changelog 링크 포함

---

## 6. References

- **Process details**: [docs/workflow.md](docs/workflow.md)
- **Terminology**: [docs/glossary.md](docs/glossary.md)
