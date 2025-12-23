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

## 5. References

- **Process details**: [docs/workflow.md](docs/workflow.md)
- **Terminology**: [docs/glossary.md](docs/glossary.md)
