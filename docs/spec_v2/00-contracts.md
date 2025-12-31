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
| 역할 분리 | HealthMonitor가 관측 → DB 갱신, StateReconciler는 DB만 읽어 판정 |

**상태 분리 원칙**:
| 상태 | 기준 | 설명 |
|------|------|------|
| observed_status | 리소스 관측 | Container/Volume 존재 여부만 반영 |
| health_status | 정책 판정 | 불변식 위반, timeout 등 오류 상태 |

> **observed_status에 ERROR 없음**: ERROR는 리소스 관측 결과가 아닌 정책 판정이므로 health_status로 분리

**예시**:
- `!volume_exists()` → HM이 관측 → `observed_status=PENDING` → SR이 확인
- Container가 실제로 running인데 DB만 보고 완료 판정하면 안 됨
- Container + !Volume → HM이 `observed_status=RUNNING, health_status=ERROR` 설정

**참조**: [04-control-plane.md#healthmonitor](./04-control-plane.md#healthmonitor), [04-control-plane.md#statereconciler](./04-control-plane.md#statereconciler)

---

## 2. Level-Triggered Reconciliation

| 항목 | 값 |
|------|---|
| 정의 | 이벤트가 아닌 현재 상태를 주기적으로 관찰하여 desired state로 수렴 |
| 핵심 | SR은 DB만 읽음, 이벤트를 신뢰하지 않음 |
| 장점 | 이벤트 유실에도 다음 reconcile에서 복구 (자기 치유) |
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
| HealthMonitor | observed_status, health_status, observed_at |
| StateReconciler | operation, op_started_at, op_id, archive_key, error_count, error_info, previous_status, home_ctx |
| API | desired_state, deleted_at, standby_ttl_seconds, archive_ttl_seconds, last_access_at |

> **health_status 소유자**: HealthMonitor가 리소스 관측 + 정책 판정 후 health_status 설정

> **desired_state 단일 소유자**: API만 desired_state를 변경할 수 있음
> - TTL Manager → 내부 서비스 레이어를 통해 API 호출
> - Proxy (Auto-wake) → 내부 서비스 레이어를 통해 API 호출

**예외 규칙 (error_info)**:
- 원칙적으로 error_info는 StateReconciler가 씀
- 불변식 위반(ContainerWithoutVolume 등) 시 error_info가 NULL이면 HealthMonitor가 1회 설정 가능

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
| 4 | HealthMonitor | `health_status=ERROR` 판정/기록 (error_info.is_terminal 확인) |

> **Single Writer 준수**: SR이 operation/op_id/error_info 설정, HM이 observed_status/health_status 설정
> **observed_status 유지**: ERROR 시에도 observed_status는 실제 리소스 상태 반영 (RUNNING/STANDBY/PENDING)

**참조**: [03-schema.md](./03-schema.md), [04-control-plane.md#statereconciler](./04-control-plane.md#statereconciler)

---

## 5. Ordered State Machine

| 항목 | 값 |
|------|---|
| 정의 | PENDING(0) < STANDBY(10) < RUNNING(20), 인접 레벨만 전이 |
| 효과 | step_up/step_down 경로 예측 가능 |
| 적용 대상 | Active 상태 (PENDING, STANDBY, RUNNING) |

**전이 규칙**:
- step_up: PENDING → STANDBY → RUNNING (순차)
- step_down: RUNNING → STANDBY → PENDING (순차)
- **RUNNING → PENDING 직접 전이 금지** (STOPPING → ARCHIVING 순차)

**레벨 정의**:
| 상태 | Level | Container | Volume |
|------|-------|-----------|--------|
| PENDING | 0 | - | - |
| STANDBY | 10 | - | O |
| RUNNING | 20 | O | O |

**예외 상태** (Ordered SM 미적용):
| 상태 | 설명 |
|------|------|
| DELETED | 삭제 완료 상태 (observed_status 축) |

> **health_status는 별도 축**: ERROR는 observed_status가 아닌 health_status로 표현 (Ordered SM과 독립)

**삭제 조건**:
| 조건 | 설명 |
|------|------|
| `observed_status = PENDING` | 정상 삭제 (Archive 완료 후) |
| `health_status = ERROR` | ERROR 탈출 삭제 |
| `operation = NONE` | 진행 중 작업 없음 (필수) |

> **RUNNING/STANDBY에서 삭제 요청**: desired_state=PENDING 설정 → step_down 완료 후 삭제
> **ERROR에서 삭제**: health_status=ERROR AND operation=NONE 시 바로 삭제 가능 (stuck 탈출)

**참조**: [02-states.md](./02-states.md), [ADR-008](../adr/008-ordered-state-machine.md)

---

## 6. Container↔Volume Invariant

| 항목 | 값 |
|------|---|
| 정의 | Container 있으면 Volume 반드시 존재 |
| 역방향 | Volume만 존재 가능 (STANDBY 상태) |
| 위반 감지 | HealthMonitor (ContainerWithoutVolume → health_status=ERROR) |

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

**RESTORING 완료 조건**:

| # | 조건 | 검증 주체 |
|---|------|----------|
| 1 | `observed_status = STANDBY` | HealthMonitor (Volume 존재 관측) |
| 2 | `home_ctx.restore_marker = archive_key` | StateReconciler (복원 완료 확인) |

> 두 조건 모두 충족해야 RESTORING 완료. restore_marker 미설정 시 Volume만 있어도 미완료 판정

**restore_marker 흐름**:
1. SR이 `StorageProvider.restore(ws_id, archive_key)` 호출
2. StorageProvider가 Storage Job 실행
3. Job 성공 시 StorageProvider가 restore_marker 반환
4. SR이 `home_ctx.restore_marker = archive_key` 저장

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

| 조건 | 동작 |
|------|------|
| health_status = ERROR | archive 삭제 금지 (복구 가능성 유지) |
| op_id 존재 | 해당 경로 archive 보호 (진행중/ERROR) |
| deleted_at != NULL | ERROR 보호 해제 (사용자 삭제 요청) |

**참조**: [05-data-plane.md#archive-gc](./05-data-plane.md#archive-gc), [04-control-plane.md#error-policy](./04-control-plane.md#error-policy)

---

## Quick Reference

| # | 계약 | 한줄 요약 |
|---|------|----------|
| 1 | Reality vs DB | 실제 리소스가 진실, observed_status(리소스) vs health_status(정책) 분리 |
| 2 | Level-Triggered | 적응형 Polling, SR은 DB만 읽음 |
| 3 | Single Writer | 컬럼별 단일 소유자 (HM: health_status 추가) |
| 4 | Non-preemptive | workspace당 동시 operation 1개 |
| 5 | Ordered SM | PENDING < STANDBY < RUNNING (health_status는 별도 축) |
| 6 | Container↔Volume | Container 있으면 Volume 필수 |
| 7 | Archive/Restore | op_id로 멱등, Crash-Only |
| 8 | Ordering | archive_key 저장 → Volume 삭제 |
| 9 | GC Protection | health_status=ERROR/op_id 기반 보호 |

---

## Cross-Reference (관련 문서)

| 계약 | 관련 문서 |
|------|----------|
| 1. Reality vs DB | 04-control-plane.md (HM, SR), 05-data-plane.md |
| 2. Level-Triggered | 01-glossary.md, 04-control-plane.md (SR) |
| 3. Single Writer | 03-schema.md, 04-control-plane.md (Coordinator, HM) |
| 4. Non-preemptive | 03-schema.md, 02-states.md, 04-control-plane.md (SR) |
| 5. Ordered SM | 02-states.md, ADR-008 |
| 6. Container↔Volume | 05-data-plane.md (Instance, Storage) |
| 7. Archive/Restore | 05-data-plane.md (Storage, Job) |
| 8. Ordering | 05-data-plane.md (Storage, Instance) |
| 9. GC Protection | 05-data-plane.md (GC), 04-control-plane.md (Error) |
