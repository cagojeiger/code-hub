# Contracts (M2)

> 핵심 계약의 단일 정의 문서 (Single Source of Truth)
>
> **충돌 시 이 문서가 우선**

---

## 1. Reality vs DB (진실의 원천)

| 항목 | 값 |
|------|---|
| 정의 | 실제 리소스(Container/Volume)가 진실, DB는 마지막 관측치 |
| 핵심 | Actuator 성공 반환 ≠ 완료. **관측 조건 충족 = 완료** |
| 예외 | is_terminal=true로 operation 종료 시 incomplete. ERROR 상태는 완료가 아님 |
| 역할 분리 | HealthMonitor가 관측 → DB 갱신, StateReconciler는 DB만 읽어 판정 |

**Conditions (단일 JSONB)**:

`conditions` JSONB 컬럼에 모든 Condition 정보 저장 (Dictionary 방식):

| Condition | Owner | 설명 |
|-----------|-------|------|
| `storage.volume_ready` | HealthMonitor | Volume 존재 여부 |
| `storage.archive_ready` | HealthMonitor | Archive 접근 가능 여부 |
| `infra.*.container_ready` | HealthMonitor | Container running 여부 |
| `policy.healthy` | HealthMonitor | 불변식 + 정책 준수 |

**`storage.archive_ready` reason 값**:
| reason | status | 설명 |
|--------|--------|------|
| ArchiveUploaded | true | Archive 정상 접근 가능 |
| ArchiveCorrupted | false | checksum 불일치 |
| ArchiveExpired | false | TTL 만료 |
| ArchiveNotFound | false | archive_key 있지만 S3에 없음 |
| NoArchive | false | archive_key = NULL |

> **archive_key vs archive_ready**: archive_key(SR 소유)는 경로 데이터, archive_ready(HM 소유)는 접근성 상태

각 Condition 구조:
```json
{
  "storage.volume_ready": {
    "status": true,
    "reason": "VolumeProvisioned",
    "message": "Volume is ready",
    "last_transition_time": "2026-01-01T12:00:00Z"
  }
}
```

> **그룹 명명규칙**: `{group}.{name}` 형식 (예: `infra.docker.container_ready`, `storage.volume_ready`)
> **확장성**: Docker/K8s/Archive 등 인프라별 Conditions 추가 시 ALTER TABLE 불필요

**Phase (파생 값)**:

| Phase | 조건 | Level |
|-------|------|-------|
| DELETED | `deleted_at != NULL` | -1 |
| ERROR | `!conditions["policy.healthy"].status` | - (Ordered 미적용) |
| RUNNING | healthy ∧ container_ready ∧ volume_ready | 20 |
| STANDBY | healthy ∧ volume_ready ∧ !container_ready | 10 |
| ARCHIVED | healthy ∧ !volume_ready ∧ archive_ready | 5 |
| PENDING | healthy ∧ !volume_ready ∧ !archive_ready | 0 |

> **Phase는 계산값**: DB에 `phase` 컬럼을 캐시로 저장하되, `conditions` 변경 시 앱이 함께 계산/업데이트
> **Conditions 동결 금지**: Phase=ERROR 시에도 volume_ready/container_ready는 실제 상태 반영

**policy.healthy=false 조건** (Phase=ERROR 유발):

| 우선순위 | 조건 | reason | 설명 |
|---------|------|--------|------|
| 1 | container_ready ∧ !volume_ready | ContainerWithoutVolume | 불변식 위반 (HM 직접 감지) |
| 2 | archive_key != NULL ∧ !archive_ready | ArchiveAccessError | Archive 접근 불가 (손상/만료/미존재) |
| 3 | error_info.is_terminal = true | (error_info.reason 복사) | SR 작업 실패 (HM이 SR 결과 읽음) |

> **Archive 접근 불가 → ERROR**: archive_key가 있는데 archive_ready=false면 healthy=false로 설정
> **Phase=ERROR 시 조치**: 수동 복구 (archive_key 리셋 또는 Archive 재업로드) 후 reconcile 재개

**예시**:
- `!volume_exists()` → HM이 `conditions["storage.volume_ready"].status=false` 설정 → Phase=PENDING
- Container running → HM이 `conditions["infra.docker.container_ready"].status=true` 설정 → Phase=RUNNING
- Container + !Volume → HM이 `conditions["policy.healthy"] = {status: false, reason: "ContainerWithoutVolume"}` → Phase=ERROR
- archive_key 있는데 S3 HEAD 실패 → HM이 `conditions["policy.healthy"] = {status: false, reason: "ArchiveAccessError"}` → Phase=ERROR

**참조**: [04-control-plane.md#healthmonitor](./04-control-plane.md#healthmonitor), [04-control-plane.md#statereconciler](./04-control-plane.md#statereconciler)

---

## 2. Level-Triggered Reconciliation

| 항목 | 값 |
|------|---|
| 정의 | 이벤트가 아닌 현재 상태를 주기적으로 관찰하여 desired state로 수렴 |
| 핵심 | SR은 DB만 읽음, 이벤트를 신뢰하지 않음 |
| 장점 | 이벤트 유실에도 다음 reconcile에서 복구 (자기 치유) |
| 예외 | health_status=ERROR는 자동 복구 불가. 수동 개입(error_info 리셋) 후 재개 |
| 구현 | 적응형 Polling (상태별 주기 조정) |

**루프**:
```
desired != observed → operation 실행 → 관측으로 완료 판정 → 반복
```

**적응형 Polling 주기**:
| 상태 | HM 주기 | SR 주기 |
|------|---------|---------|
| operation 진행 중 | 2초 | 2초 |
| 수렴 필요 (desired ≠ observed) | 5초 | 5초 |
| 안정 상태 | 30초 | 30초 |

> Redis hint로 즉시 관측 트리거 가능: SR이 operation 시작/완료 시 `PUBLISH monitor:trigger`

**참조**: [01-glossary.md](./01-glossary.md), [04-control-plane.md#statereconciler](./04-control-plane.md#statereconciler)

---

## 3. Single Writer Principle

| 항목 | 값 |
|------|---|
| 정의 | 한 데이터의 쓰기는 오직 하나의 컴포넌트만 담당 |
| 목적 | 동시성 충돌 방지, 상태 일관성 보장 |

**컬럼 소유권**:

| 컴포넌트 | 소유 컬럼 |
|---------|----------|
| HealthMonitor | conditions (JSONB), phase, observed_at |
| StateReconciler | operation, op_started_at, op_id, archive_key, error_count, error_info, home_ctx |
| API | desired_state, deleted_at, standby_ttl_seconds, archive_ttl_seconds, last_access_at |

**desired_state 허용 값**:

| desired_state | Level | 설명 |
|---------------|-------|------|
| DELETED | -1 | 삭제 요청 (soft-delete) |
| PENDING | 0 | 활성 리소스 없음 (Archive도 없음) |
| ARCHIVED | 5 | Archive만 유지 |
| STANDBY | 10 | Volume만 유지 |
| RUNNING | 20 | 실행 상태 |

> **ARCHIVED 추가 이유**: desired_state에 ARCHIVED가 없으면 Phase=ARCHIVED인데 step_down으로 Archive가 삭제되는 문제 발생
> **DELETED 추가 이유**: 삭제 의도를 명시적으로 표현 (deleted_at 설정과 동시에 사용)

> **conditions 소유자**: HealthMonitor가 리소스 관측 → conditions JSONB 갱신 → phase 계산/저장
> **phase 캐시**: conditions 변경 시 HM이 phase도 함께 계산하여 업데이트 (쿼리 성능 확보)

> **desired_state 단일 소유자**: API만 desired_state를 변경할 수 있음
> - TTL Manager → 내부 서비스 레이어를 통해 API 호출
> - Proxy (Auto-wake) → 내부 서비스 레이어를 통해 API 호출

**error_info → conditions.healthy 흐름** (Single Writer 완전 준수):

| 단계 | 주체 | 동작 |
|------|------|------|
| 1 | StateReconciler | operation 실패 시 error_info 설정 (is_terminal=true) |
| 2 | HealthMonitor | error_info.is_terminal 확인 → conditions["policy.healthy"].status=false, reason 복사 |
| 3 | HealthMonitor | 불변식 위반 감지 → conditions["policy.healthy"] = {status: false, reason: "ContainerWithoutVolume"} |

> **Single Writer 준수**: error_info는 SR 소유, conditions는 HM 소유
> **예외 규칙 제거**: Last-Write-Wins 불필요 (영역 분리로 충돌 없음)

**참조**: [03-schema.md](./03-schema.md)

---

## 4. Non-preemptive Operation

| 항목 | 값 |
|------|---|
| 정의 | `operation != NONE`이면 다른 operation 시작 불가 |
| 효과 | workspace당 동시에 1개 operation만 진행 |
| 이유 | 취소/선점/롤백/부분완료 복잡도 제거 |

**검사**: StateReconciler Plan 단계에서 `operation != NONE`이면 skip

**ERROR 전환 규칙**:

| 단계 | 컴포넌트 | 동작 |
|------|----------|------|
| 1 | StateReconciler | `is_terminal=true` 판정 시 `operation=NONE` 리셋 |
| 2 | StateReconciler | `error_info` 설정 (reason, message, is_terminal, context) |
| 3 | StateReconciler | `op_id` 유지 (GC 보호) |
| 4 | HealthMonitor | `conditions["policy.healthy"].status=false` 설정, reason=error_info.reason (is_terminal 확인) |

**보장 사항**:
- **원자성**: 1~3단계는 단일 DB 트랜잭션. 부분 완료 시 전체 롤백
- **Conditions 유지**: Phase=ERROR 시에도 volume_ready/container_ready는 실제 상태 반영
- **reconcile 제외**: conditions["policy.healthy"].status=false인 workspace는 SR 대상에서 제외. 복구 후 재개

**재시도 정책**:
- SR: 단기 재시도 (error_info.retry_count < max_retry)
- Main Controller: 장기 재시도 (reconcile_failure_count 기반 backoff)
- is_terminal=true 시 Phase=ERROR, reconcile 중단

**참조**: [03-schema.md](./03-schema.md), [04-control-plane.md#statereconciler](./04-control-plane.md#statereconciler)

---

## 5. Ordered State Machine

| 항목 | 값 |
|------|---|
| 정의 | Phase = calculate_phase(conditions), 인접 레벨만 전이 |
| 효과 | step_up/step_down 경로 예측 가능 |
| 적용 대상 | Active Phase (PENDING, ARCHIVED, STANDBY, RUNNING) |

**Phase 정의** (Conditions에서 계산):
| Phase | 조건 | Level |
|-------|------|-------|
| DELETED | `deleted_at != NULL` | -1 |
| ERROR | `!conditions["policy.healthy"].status` | - (Ordered 미적용) |
| RUNNING | healthy ∧ container_ready ∧ volume_ready | 20 |
| STANDBY | healthy ∧ volume_ready ∧ !container_ready | 10 |
| ARCHIVED | healthy ∧ !volume_ready ∧ archive_ready | 5 |
| PENDING | healthy ∧ !volume_ready ∧ !archive_ready | 0 |

**Phase 계산**:
```python
def calculate_phase(conditions: dict, deleted_at: datetime | None) -> Phase:
    if deleted_at:
        return Phase.DELETED
    if not conditions["policy.healthy"]["status"]:
        return Phase.ERROR
    if conditions["container_ready"]["status"] and conditions["volume_ready"]["status"]:
        return Phase.RUNNING
    if conditions["volume_ready"]["status"]:
        return Phase.STANDBY
    if conditions.get("storage.archive_ready", {}).get("status"):
        return Phase.ARCHIVED
    return Phase.PENDING
```

**전이 규칙**:
- step_up: PENDING → ARCHIVED → STANDBY → RUNNING (순차)
  - PENDING → ARCHIVED: 자동 (Archive 존재 시)
  - ARCHIVED → STANDBY: RESTORING
- step_down: RUNNING → STANDBY → ARCHIVED → PENDING (순차)
  - STANDBY → ARCHIVED: ARCHIVING
  - ARCHIVED → PENDING: Archive 삭제
- **RUNNING → PENDING 직접 전이 금지** (순차 전이 필수)

**예외 상태** (Ordered SM 미적용):
| Phase | Level | 설명 |
|-------|-------|------|
| DELETED | -1 | 삭제 완료 상태 |
| ERROR | - | 정책 위반 (healthy=false) |

> **DELETED 전이**: Phase=PENDING → DELETED만 가능. RUNNING/STANDBY 직접 전이 불가 (step_down 필수)
> **ERROR는 별도 축**: ERROR는 conditions["policy.healthy"].status=false로 표현 (Ordered SM과 독립)

**삭제 조건**:
| 조건 | 설명 |
|------|------|
| `Phase = PENDING` | 정상 삭제 (!volume_ready ∧ !archive_ready) |
| `Phase = ARCHIVED` | Archive 삭제 (!volume_ready ∧ archive_ready) |
| `Phase = ERROR` | ERROR 탈출 삭제 (!healthy) |
| `operation = NONE` | 진행 중 작업 없음 (필수) |

> **RUNNING/STANDBY에서 삭제 요청**: desired_state=PENDING 설정 → step_down 완료 후 삭제
> **ARCHIVED에서 삭제**: Phase=ARCHIVED AND operation=NONE 시 바로 삭제 가능 (Archive 포함 삭제)
> **ERROR에서 삭제**: Phase=ERROR AND operation=NONE 시 바로 삭제 가능 (stuck 탈출)

**참조**:
- [02-states.md](./02-states.md) - 상태 정의
- [ADR-008](../adr/008-ordered-state-machine.md) - Ordered SM (step_up/step_down, 유효)
- [ADR-009](../adr/009-status-operation-separation.md) - operation/op_id CAS (유효)
- [ADR-011](../adr/011-declarative-conditions.md) - Conditions 패턴 (Proposed)

---

## 6. Container↔Volume Invariant

| 항목 | 값 |
|------|---|
| 정의 | Container 있으면 Volume 반드시 존재 |
| 역방향 | Volume만 존재 가능 (Phase=STANDBY) |
| 위반 감지 | HealthMonitor (container_ready ∧ !volume_ready → conditions["policy.healthy"] = {status: false, reason: "ContainerWithoutVolume"}) |

**네이밍 규칙**:
| 항목 | 형식 |
|------|------|
| Container | `ws-{workspace_id}` |
| Volume | `ws-{workspace_id}-home` |

> K8s DNS-1123 호환: 하이픈(`-`) 사용, 언더스코어(`_`) 금지

**참조**: [05-data-plane.md#instancecontroller](./05-data-plane.md#instancecontroller), [05-data-plane.md#storageprovider](./05-data-plane.md#storageprovider)

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
| 완료 마커 | home_ctx.restore_marker = archive_key |

**ARCHIVING 완료 조건**:

| # | 조건 | 검증 주체 |
|---|------|----------|
| 1 | `conditions["storage.volume_ready"].status = false` | HealthMonitor (Volume 삭제 관측) |
| 2 | `conditions["storage.archive_ready"].status = true` | HealthMonitor (Archive 접근 확인) |
| 3 | `archive_key != NULL` | StateReconciler (경로 저장 확인) |

> 세 조건 모두 충족해야 ARCHIVING 완료 (Phase=ARCHIVED)

**RESTORING 완료 조건**:

| # | 조건 | 검증 주체 |
|---|------|----------|
| 1 | `conditions["storage.volume_ready"].status = true` | HealthMonitor (Volume 존재 관측) |
| 2 | `home_ctx.restore_marker = archive_key` | StateReconciler (복원 완료 확인) |

> 두 조건 모두 충족해야 RESTORING 완료 (Phase=STANDBY). restore_marker 미설정 시 Volume만 있어도 미완료 판정

**restore_marker 흐름**:
1. SR이 `StorageProvider.restore(ws_id, archive_key)` 호출
2. StorageProvider가 Storage Job 실행
3. Job 성공 시 restore_marker 반환 → SR이 `home_ctx.restore_marker` 저장
4. Job 실패 시 restore_marker 미반환 → 불완전 Volume 상태

**실패 시 처리**:

| 상황 | 처리 |
|------|------|
| restore() 실패 | restore_marker 미설정. STARTING 차단. 재시도 또는 수동 정리 |
| restore_marker 저장 실패 | 다음 reconcile에서 Volume 존재 + archive checksum 검증 후 재설정 |

**참조**: [05-data-plane.md#storageprovider](./05-data-plane.md#storageprovider), [05-data-plane.md#storage-job](./05-data-plane.md#storage-job)

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

**크래시 복구** (StateReconciler 책임):

| 상황 | 감지 조건 | 복구 절차 |
|------|----------|----------|
| archive_key 저장 전 크래시 | operation=ARCHIVING AND archive_key=NULL | op_id 기반 S3 HEAD 확인. 존재하면 archive_key 복구 후 delete_volume() |
| delete_volume() 전 크래시 | operation=ARCHIVING AND archive_key!=NULL AND volume_exists() | delete_volume() 재시도 |

> **감지 타이밍**: 다음 reconcile 사이클에서 SR이 ARCHIVING 상태 확인 시

**참조**: [05-data-plane.md#storageprovider](./05-data-plane.md#storageprovider), [05-data-plane.md#instancecontroller](./05-data-plane.md#instancecontroller)

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
| 2 | conditions["policy.healthy"].status = false | 보호 | 보호 |
| 3 | op_id 존재 | - | 보호 |

> **우선순위 규칙**: 높은 우선순위 조건이 먼저 평가됨
> **사용자 의도 우선**: deleted_at 설정 = 사용자가 삭제 원함 → ERROR(healthy=false) 보호 해제

**참조**: [05-data-plane.md#archive-gc](./05-data-plane.md#archive-gc), [04-control-plane.md#error-policy](./04-control-plane.md#error-policy)

---

## 10. Retry Policy

| 항목 | 값 |
|------|---|
| 정의 | 재시도는 is_terminal=false일 때만 자동 수행 |
| SR 책임 | 단기 재시도 (error_info.retry_count < max_retry) |
| Controller 책임 | 장기 재시도 (reconcile_failure_count 기반 exponential backoff) |
| 종료 조건 | is_terminal=true → Phase=ERROR, 수동 복구 필요 |

**재시도 흐름**:
1. SR operation 실패 → error_info 설정 (retry_count++)
2. retry_count < max_retry → HM: healthy 유지 → reconcile 계속
3. retry_count >= max_retry → SR: is_terminal=true → HM: healthy=false → Phase=ERROR

**Backoff 정책**:

| 레벨 | 담당 | 저장 | 간격 |
|------|------|------|------|
| Operation | SR | error_info.retry_count | 즉시 (3회) |
| Workspace | Controller | reconcile_failure_count | 1s→2s→4s→8s... (max 5분) |

**의존성 기반 재시도**:

```
의존성 체인: volume_ready → container_ready → healthy

1. Controller가 모든 Conditions 종합 판단
2. 의존성 체인의 "뿌리"부터 처리:
   - healthy=false 확인 → 원인 추적
   - container_ready=false가 원인? → 원인 추적
   - volume_ready=false가 원인? → 이것부터 해결!
3. 하위 Condition 해결될 때까지 상위 재시도 지연
```

> **핵심**: Condition 자체가 아닌, Condition을 true로 만드는 "상위 작업"을 재시도

**참조**: [04-control-plane.md#statereconciler](./04-control-plane.md#statereconciler)

---

## Quick Reference

| # | 계약 | 한줄 요약 |
|---|------|----------|
| 1 | Reality vs DB | conditions JSONB (volume_ready, archive_ready, container_ready, healthy) → Phase 계산 |
| 2 | Level-Triggered | 적응형 Polling, SR은 DB만 읽음 |
| 3 | Single Writer | 컬럼별 단일 소유자 (HM: conditions JSONB) |
| 4 | Non-preemptive | workspace당 동시 operation 1개 |
| 5 | Ordered SM | desired_state & Phase: PENDING(0) → ARCHIVED(5) → STANDBY(10) → RUNNING(20), ERROR/DELETED는 별도 축 |
| 6 | Container↔Volume | Container 있으면 Volume 필수 |
| 7 | Archive/Restore | op_id로 멱등, archive_ready로 상태 관측, Crash-Only |
| 8 | Ordering | archive_key 저장 → Volume 삭제 |
| 9 | GC Protection | conditions.healthy.status=false/op_id 기반 보호 |
| 10 | Retry Policy | is_terminal=true까지 재시도, 의존성 체인 기반 처리 |

---

## Cross-Reference (관련 문서)

| 계약 | 관련 문서 |
|------|----------|
| 1. Reality vs DB | 04-control-plane.md (HM, SR), 05-data-plane.md, ADR-011 (Conditions) |
| 2. Level-Triggered | 01-glossary.md, 04-control-plane.md (SR) |
| 3. Single Writer | 03-schema.md, 04-control-plane.md (Coordinator, HM) |
| 4. Non-preemptive | 03-schema.md, 02-states.md, 04-control-plane.md (SR) |
| 5. Ordered SM | 02-states.md, ADR-008 (유효), ADR-011 (Conditions) |
| 6. Container↔Volume | 05-data-plane.md (Instance, Storage) |
| 7. Archive/Restore | 05-data-plane.md (Storage, Job) |
| 8. Ordering | 05-data-plane.md (Storage, Instance) |
| 9. GC Protection | 05-data-plane.md (GC), 04-control-plane.md (Error) |
| 10. Retry Policy | 04-control-plane.md (SR, Controller) |
