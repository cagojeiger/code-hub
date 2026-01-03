# WC Judge

> WorkspaceController의 Judge 단계 상세 설계
>
> **의존**: [wc.md](./wc.md) (전체 Reconcile Loop)

---

## 개요

Judge는 conditions를 읽어 phase를 계산하는 **순수 함수**입니다.

```
┌─────────────────────────────────────────────────────────────┐
│                         Judge                                │
├─────────────────────────────────────────────────────────────┤
│  [외부 입력]                    [내부 계산]                   │
│  ────────────                  ────────────                  │
│  • container_ready (Observer)   • policy.healthy ◀── 계산    │
│  • volume_ready (Observer)      • phase ◀── 계산             │
│  • archive_ready (Observer)                                  │
│  • deleted_at (API)                                          │
│  • archive_key (WC Control)                                  │
└─────────────────────────────────────────────────────────────┘
```

> **순수 함수**: 외부 I/O 없음, 같은 입력 → 같은 출력

---

## 판단 순서 (4단계)

Judge는 다음 순서로 phase를 결정합니다. **순서가 우선순위**입니다.

| 순서 | 이름 | 데이터 | 출처 | 역할 |
|------|------|--------|------|------|
| 1 | **사용자 의도** | deleted_at | API (DB) | 삭제 요청 (최우선) |
| 2 | **시스템 판단** | policy.healthy | Judge 계산 (tick 내) | 불변식 준수 여부 |
| 3 | **현실** | container_ready, volume_ready, archive_ready | Observer (DB) | 관측된 리소스 상태 |
| 4 | **시스템 기억** | archive_key | WC Control (DB) | Fallback용 과거 기록 |

### 판단 순서 흐름

```
1. 사용자 의도  →  2. 시스템 판단  →  3. 현실  →  4. 시스템 기억
   (삭제 우선)       (안전성 체크)      (상태)      (Fallback)
```

> **핵심**: 사용자 의도(삭제) > 시스템 안전성(불변식) > 현재 상태 > 과거 기록

---

## 단계 2: policy.healthy (내부 계산)

**중요**: policy.healthy는 외부 입력이 아니라 **Judge가 tick 내에서 계산**하는 값입니다.

```
[Observer 출력]                [Judge 계산]
container_ready ──┐
volume_ready ─────┼────▶ check_invariants() ──▶ policy.healthy
archive_ready ────┘
```

### 불변식 위반 조건 (check_invariants)

| 우선순위 | 조건 | reason | 설명 |
|---------|------|--------|------|
| 1 | container_ready ∧ !volume_ready | ContainerWithoutVolume | 계약 #6 위반 |
| 2 | archive_ready.reason ∈ {Corrupted, Expired, NotFound} | ArchiveAccessError | Archive 단말 오류 |

> **Spec 참조**: [03-schema.md#policy.healthy=false 조건](../spec_v2/03-schema.md#policyhealthyfalse-조건)

---

## 단계 3: 현실 (리소스 상태)

현실 계층 내에서도 **레벨 순서**가 있습니다. 높은 레벨이 우선합니다.

| 조건 | Phase | Level | 의미 |
|------|-------|-------|------|
| container ∧ volume | RUNNING | 20 | 완전 실행 |
| volume | STANDBY | 10 | 대기 상태 |
| archive | ARCHIVED | 5 | 보관 상태 |
| none | PENDING | 0 | 초기 상태 |

> **구체 → 일반**: 높은 레벨(RUNNING) 조건을 먼저 체크하여 더 구체적인 상태 우선

---

## 단계 4: Fallback (시스템 기억)

### 왜 필요한가?

S3 일시 장애 시 archive_ready.status=false가 되면, 실제로 Archive가 있어도 PENDING으로 잘못 판정됩니다.

```
[Fallback 없을 때]
S3 일시 장애 → archive_ready=false → phase=PENDING
                                         ↓
                                    desired=ARCHIVED면?
                                         ↓
                                    CREATE_EMPTY_ARCHIVE
                                         ↓
                                    기존 Archive 덮어씀!
                                         ↓
                                    🔴 데이터 손실
```

### Fallback 동작

| 조건 | 동작 |
|------|------|
| archive_ready.status = false | 정상: PENDING으로 판정 |
| + archive_key 존재 | Fallback 후보 |
| + reason ∈ {Unreachable, Timeout} | Fallback 적용: **ARCHIVED 유지** |
| + reason ∈ {Corrupted, Expired, NotFound} | 단말 오류: **ERROR** |

> **Spec 참조**: [02-states.md#calculate_phase](../spec_v2/02-states.md#calculate_phase)

---

## Phase 결정 테이블

### 전체 결정 흐름

| 순서 | 체크 | 조건 | 결과 Phase |
|------|------|------|------------|
| 1 | deleted_at | deleted_at ∧ resources | DELETING |
| 1 | deleted_at | deleted_at ∧ !resources | DELETED |
| 2 | healthy | !healthy | ERROR |
| 3 | resources | container ∧ volume | RUNNING |
| 3 | resources | volume | STANDBY |
| 3 | resources | archive | ARCHIVED |
| 4 | fallback | archive_key ∧ 일시장애 | ARCHIVED |
| 5 | default | - | PENDING |

> **resources**: `container_ready ∨ volume_ready ∨ archive_ready`
> **일시장애**: `archive_ready.reason ∈ {Unreachable, Timeout}`

### 결정 흐름도

```
deleted_at? ──Yes──▶ resources? ──Yes──▶ DELETING
    │                    │
    │                   No
    │                    ▼
    │               DELETED
    │
   No
    ▼
healthy? ──No──▶ ERROR
    │
   Yes
    ▼
container ∧ volume? ──Yes──▶ RUNNING
    │
   No
    ▼
volume? ──Yes──▶ STANDBY
    │
   No
    ▼
archive? ──Yes──▶ ARCHIVED
    │
   No
    ▼
archive_key ∧ 일시장애? ──Yes──▶ ARCHIVED (Fallback)
    │
   No
    ▼
PENDING
```

---

## ERROR 발생 경로

ERROR는 **두 경로**에서 발생합니다.

### 경로 1: Judge (불변식 위반)

| error_reason | 조건 | 감지 시점 |
|--------------|------|----------|
| ContainerWithoutVolume | container ∧ !volume | 관측 직후 |
| ArchiveCorrupted | archive.reason = Corrupted | 관측 직후 |
| ArchiveExpired | archive.reason = Expired | 관측 직후 |
| ArchiveNotFound | archive.reason = NotFound | 관측 직후 |

> **즉시 ERROR**: 관측 결과만으로 판단, 작업 시도 없이 ERROR
> **Spec 참조**: [03-schema.md#policy.healthy=false 조건](../spec_v2/03-schema.md#policyhealthyfalse-조건)

### 경로 2: Control (작업 실패)

Control 단계에서 operation 실행 중 실패 시 ERROR로 전환됩니다.

- Timeout, RetryExceeded, ActionFailed, ImagePullFailed 등
- **상세**: [wc-control.md](./wc-control.md) 참조
- **Spec 참조**: [03-schema.md#error_reason 값](../spec_v2/03-schema.md#error_reason-값)

### ERROR 결정 주체 비교

| 경로 | 주체 | 트리거 | 설정 필드 |
|------|------|--------|----------|
| 경로 1 | **Judge** | 불변식 위반 | policy.healthy.reason |
| 경로 2 | **Control** | 작업 실패 | error_reason 컬럼 |

> **Judge 범위**: 경로 1만 Judge 책임. 경로 2는 Control 문서에서 상세 설명

---

## 잘못된 순서의 부작용

### Case 1: resources → deleted_at (역순)

```
상황: container=T, volume=T, deleted_at=T

[잘못된 순서] resources 먼저
→ phase = RUNNING (리소스 있으니까)
→ deleted_at 무시됨!

[올바른 순서] deleted_at 먼저
→ phase = DELETING
→ 삭제 진행

문제: 사용자 의도(삭제) 무시
```

### Case 2: resources → healthy (역순)

```
상황: container=T, volume=F (불변식 위반)

[잘못된 순서] resources 먼저
→ container만? → 어떤 phase?
→ 불변식 위반 놓침

[올바른 순서] healthy 먼저
→ healthy = false (ContainerWithoutVolume)
→ phase = ERROR

문제: 불변식 위반 미감지 → 데이터 손상 위험
```

### Case 3: 일반 → 구체 (L3 내부 역순)

```
상황: container=F, volume=T, archive=T

[잘못된 순서] archive 먼저
→ archive=T → phase = ARCHIVED?
→ volume 무시됨

[올바른 순서] 구체(volume) → 일반(archive)
→ volume=T → phase = STANDBY

문제: 더 높은 레벨 상태 무시
```

---

## 테스트 케이스

### 기본 상태 계산

| ID | conditions | deleted_at | 기대 phase |
|----|------------|------------|-----------|
| JDG-001 | {c:F, v:F, a:F} | N | PENDING |
| JDG-002 | {c:F, v:F, a:T} | N | ARCHIVED |
| JDG-003 | {c:F, v:T, a:F} | N | STANDBY |
| JDG-004 | {c:T, v:T, a:F} | N | RUNNING |

### 불변식 위반

| ID | conditions | 기대 결과 |
|----|------------|----------|
| JDG-005 | {c:T, v:F, a:F} | ERROR (ContainerWithoutVolume) |
| JDG-008 | archive.reason=Corrupted | ERROR (ArchiveAccessError) |

### 삭제 처리

| ID | conditions | deleted_at | 기대 phase |
|----|------------|------------|-----------|
| JDG-006 | {c:T, v:T} | Y | DELETING |
| JDG-007 | {c:F, v:F} | Y | DELETED |

### Fallback

| ID | conditions | archive_key | 기대 phase |
|----|------------|-------------|-----------|
| JDG-009 | {a.status:F, a.reason:Unreachable} | 존재 | ARCHIVED |
| JDG-010 | {a.status:F, a.reason:NotFound} | 존재 | ERROR |
| JDG-011 | {a.status:F, a.reason:Unreachable} | NULL | PENDING |

### 순서 검증

| ID | 케이스 | 검증 |
|----|--------|------|
| JDG-ORD-001 | deleted_at + 리소스 | deleted_at 우선 (DELETING) |
| JDG-ORD-002 | 불변식 위반 + 리소스 | healthy 우선 (ERROR) |
| JDG-ORD-003 | volume + archive | 구체 우선 (STANDBY) |

---

## 참조

- [wc.md](./wc.md) - 전체 Reconcile Loop
- [wc-control.md](./wc-control.md) - Control 단계 (ERROR 경로 2 상세)
- [00-contracts.md](../spec_v2/00-contracts.md) - 핵심 계약 (#1, #6)
- [02-states.md](../spec_v2/02-states.md) - Phase 정의, calculate_phase()
- [03-schema.md](../spec_v2/03-schema.md) - policy.healthy, error_reason 정의
- [04-control-plane.md](../spec_v2/04-control-plane.md) - ERROR 전환 규칙

