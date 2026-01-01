# Contracts (M2)

> 핵심 계약의 단일 정의 문서
>
> **역할**: 규칙/제약만 정의. 데이터 정의는 참조

---

## 1. Reality vs DB (진실의 원천)

| 항목 | 값 |
|------|---|
| 정의 | 실제 리소스(Container/Volume)가 진실, DB는 마지막 관측치 |
| 핵심 | Actuator 성공 반환 ≠ 완료. **관측 조건 충족 = 완료** |
| 예외 | is_terminal=true로 operation 종료 시 incomplete. ERROR 상태는 완료가 아님 |
| 역할 분리 | ResourceObserver가 관측 → conditions/phase 갱신, OperationController는 DB 읽어 operation 계획/실행 |

> **Conditions**: [03-schema.md#conditions](./03-schema.md#conditions-jsonb-구조)
> **Phase 정의**: [02-states.md#phase](./02-states.md#phase-요약)

---

## 2. Level-Triggered Reconciliation

| 항목 | 값 |
|------|---|
| 정의 | 이벤트가 아닌 현재 상태를 주기적으로 관찰하여 desired state로 수렴 |
| 핵심 | OC는 DB만 읽음, 이벤트를 신뢰하지 않음 |
| 장점 | 이벤트 유실에도 다음 reconcile에서 복구 (자기 치유) |
| 예외 | Phase=ERROR는 자동 복구 불가. 수동 개입(error_info 리셋) 후 재개 |

> **용어**: [01-glossary.md#level-triggered](./01-glossary.md#level-triggered-vs-edge-triggered)
> **구현**: [04-control-plane.md#operationcontroller](./04-control-plane.md#operationcontroller)

---

## 3. Single Writer Principle

| 항목 | 값 |
|------|---|
| 정의 | 한 데이터의 쓰기는 오직 하나의 컴포넌트만 담당 |
| 목적 | 동시성 충돌 방지, 상태 일관성 보장 |

**컬럼 소유권**:

| 컴포넌트 | 소유 컬럼 |
|---------|----------|
| ResourceObserver | conditions (JSONB), phase, observed_at |
| OperationController | operation, op_started_at, op_id, archive_key, error_count, error_info, home_ctx |
| API | desired_state, deleted_at, standby_ttl_seconds, archive_ttl_seconds, last_access_at |

> **컬럼 상세**: [03-schema.md](./03-schema.md)

---

## 4. Non-preemptive Operation

| 항목 | 값 |
|------|---|
| 정의 | `operation != NONE`이면 다른 operation 시작 불가 |
| 효과 | workspace당 동시에 1개 operation만 진행 |
| 이유 | 취소/선점/롤백/부분완료 복잡도 제거 |

**규칙**:

| 조건 | 결과 |
|------|------|
| `operation ≠ NONE` 시 desired_state 변경 | **409 Conflict** |
| `Phase=ERROR` ∧ `operation≠NONE` | OC가 `operation=NONE` 리셋 (교착 방지) |

**불변식**: `Phase=ERROR → operation=NONE` (OC 보장)

> **Operation 정의**: [02-states.md#operation](./02-states.md#operation-진행-상태)

---

## 5. Ordered State Machine

| 항목 | 값 |
|------|---|
| 정의 | Phase = calculate_phase(conditions), 인접 레벨만 전이 |
| 효과 | step_up/step_down 경로 예측 가능 |
| 적용 대상 | Active Phase (PENDING, ARCHIVED, STANDBY, RUNNING) |

**핵심 규칙**:

- step_up: 낮은 Level → 높은 Level (한 단계씩)
- step_down: 높은 Level → 낮은 Level (한 단계씩)
- 모든 경로가 **단조(monotonic)** - 상승/하강 혼합 없음

**Operation별 전이**:

| Operation | 전이 | 방향 |
|-----------|------|------|
| CREATE_EMPTY_ARCHIVE | PENDING(0) → ARCHIVED(5) | step_up |
| PROVISIONING | PENDING(0) → STANDBY(10) | step_up |
| RESTORING | ARCHIVED(5) → STANDBY(10) | step_up |
| STARTING | STANDBY(10) → RUNNING(20) | step_up |
| STOPPING | RUNNING(20) → STANDBY(10) | step_down |
| ARCHIVING | STANDBY(10) → ARCHIVED(5) | step_down |

> **PENDING 미포함**: PENDING은 desired_state가 아님 (phase로만 존재)
> **단조 경로 보장**: CREATE_EMPTY_ARCHIVE로 비단조 경로(0→10→5) 제거

**예외 상태** (Ordered SM 미적용):

| Phase | 이유 |
|-------|------|
| ERROR | 정책 위반 (별도 축) |
| DELETING | 삭제 진행 중 |
| DELETED | 삭제 완료 |

> **Phase Level**: [02-states.md#phase-level](./02-states.md#phase-level)
> **전이 규칙**: [02-states.md#전이-규칙](./02-states.md#전이-규칙)
> **ADR**: [ADR-008](../adr/008-ordered-state-machine.md)

---

## 6. Container↔Volume Invariant

| 항목 | 값 |
|------|---|
| 정의 | Container 있으면 Volume 반드시 존재 |
| 역방향 | Volume만 존재 가능 (Phase=STANDBY) |
| 위반 감지 | RO가 `container_ready ∧ !volume_ready` → `policy.healthy=false, reason="ContainerWithoutVolume"` |

**네이밍 규칙**:

| 항목 | 형식 |
|------|------|
| Container | `ws-{workspace_id}` |
| Volume | `ws-{workspace_id}-home` |

> K8s DNS-1123 호환: 하이픈(`-`) 사용, 언더스코어(`_`) 금지

---

## 7. Archive/Restore Contract

### Archive (멱등)

| 항목 | 값 |
|------|---|
| 정의 | 같은 (workspace_id, op_id)에 대해 멱등 |
| 구현 | HEAD 체크로 기존 archive 확인 후 skip |
| 경로 | `{workspace_id}/{op_id}/home.tar.zst` |

### Restore (Crash-Only)

| 항목 | 값 |
|------|---|
| 정의 | 같은 archive → 같은 결과, 크래시 후 재시도 안전 |
| 설계 | Stateless, 부분 결과 덮어쓰기 |
| 완료 마커 | `home_ctx.restore_marker = archive_key` |

**완료 조건**:

| Operation | 조건 |
|-----------|------|
| ARCHIVING | `!volume_ready` ∧ `archive_ready` ∧ `archive_key != NULL` |
| RESTORING | `volume_ready` ∧ `restore_marker = archive_key` |

> **상세**: [05-data-plane.md#storageprovider](./05-data-plane.md#storageprovider)

---

## 8. Ordering Guarantee (역순 금지)

### Storage 순서

| 순서 | 동작 |
|------|------|
| 1 | archive_key DB 저장 |
| 2 | Volume 삭제 |

> **역순 금지**: Volume 먼저 삭제 시 데이터 유실 위험

### Instance 순서

| 순서 | 동작 |
|------|------|
| 1 | Container 삭제 |
| 2 | Volume 삭제 |

> **역순 금지**: Container가 Volume 사용 중일 때 Volume 삭제 시 오류

---

## 9. GC Separation & Protection

### DELETING vs GC

| 대상 | 삭제 주체 | 타이밍 |
|------|----------|--------|
| Container | InstanceController | 즉시 |
| Volume | StorageProvider | Container 삭제 후 |
| Archive | GC | 2시간 후 (지연 정리) |

### 보호 규칙

| 우선순위 | 조건 | archive_key 경로 | op_id 경로 |
|---------|------|-----------------|-----------|
| 1 | deleted_at != NULL | 보호 유지 | **보호 해제** |
| 2 | healthy = false | 보호 | 보호 |
| 3 | op_id 존재 | - | 보호 |

> **사용자 의도 우선**: deleted_at 설정 = 사용자가 삭제 원함 → ERROR 보호 해제

---

## 10. Retry Policy

| 항목 | 값 |
|------|---|
| 정의 | 재시도는 `is_terminal=false`일 때만 자동 수행 |
| 종료 조건 | `is_terminal=true` → Phase=ERROR, 수동 복구 필요 |

**책임 분리**:

| 레벨 | 담당 | 재시도 |
|------|------|--------|
| Operation | OC | 즉시 3회 (단기) |
| Workspace | Controller | Exponential backoff (장기) |

> **의존성 기반 재시도**: 하위 Condition(volume_ready)부터 해결 후 상위(container_ready) 처리
> **구현**: [04-control-plane.md#operationcontroller](./04-control-plane.md#operationcontroller)

---

## Quick Reference

| # | 계약 | 한줄 요약 |
|---|------|----------|
| 1 | Reality vs DB | 관측 조건 충족 = 완료 |
| 2 | Level-Triggered | OC는 DB만 읽음 (이벤트 불신) |
| 3 | Single Writer | 컬럼별 단일 소유자 |
| 4 | Non-preemptive | workspace당 동시 operation 1개 |
| 5 | Ordered SM | 인접 레벨만 전이 (step_up/step_down) |
| 6 | Container↔Volume | Container 있으면 Volume 필수 |
| 7 | Archive/Restore | op_id로 멱등, Crash-Only |
| 8 | Ordering | archive_key 저장 → Volume 삭제 |
| 9 | GC Protection | deleted_at 시 op_id 보호 해제 |
| 10 | Retry Policy | is_terminal=true까지 재시도 |

---

## Cross-Reference

| 계약 | 관련 문서 |
|------|----------|
| 1. Reality vs DB | [02-states.md](./02-states.md) (Phase), [03-schema.md](./03-schema.md) (Conditions) |
| 2. Level-Triggered | [01-glossary.md](./01-glossary.md), [04-control-plane.md](./04-control-plane.md) |
| 3. Single Writer | [03-schema.md](./03-schema.md) |
| 4. Non-preemptive | [02-states.md](./02-states.md) (Operation) |
| 5. Ordered SM | [02-states.md](./02-states.md) (State Machine), [ADR-008](../adr/008-ordered-state-machine.md) |
| 6. Container↔Volume | [05-data-plane.md](./05-data-plane.md) |
| 7. Archive/Restore | [05-data-plane.md](./05-data-plane.md) |
| 8. Ordering | [05-data-plane.md](./05-data-plane.md) |
| 9. GC Protection | [05-data-plane.md](./05-data-plane.md) |
| 10. Retry Policy | [04-control-plane.md](./04-control-plane.md) |
