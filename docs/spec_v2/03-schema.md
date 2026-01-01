# DB 스키마 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

본 문서는 **Conditions SSOT** (Single Source of Truth)입니다.

| 섹션 | 역할 |
|------|------|
| **workspaces 테이블** | 전체 컬럼 정의 |
| **conditions JSONB** | Conditions 패턴 상세 (ADR-011) |
| **컬럼 소유권** | Single Writer Principle |

> **상태 정의**: [02-states.md](./02-states.md)
> **계약/규칙**: [00-contracts.md](./00-contracts.md)

---

## workspaces 테이블

### 전체 컬럼

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|------|------|----------|--------|------|
| id | UUID | NO | - | PK |
| owner_user_id | UUID | NO | - | FK(users.id) |
| name | VARCHAR(255) | NO | - | 이름 |
| description | TEXT | YES | NULL | 짧은 설명 |
| memo | TEXT | YES | NULL | 자유 메모 |
| image_ref | VARCHAR(512) | NO | - | 컨테이너 이미지 참조 |
| instance_backend | ENUM | NO | - | 'local-docker' / 'k8s' |
| storage_backend | ENUM | NO | - | 'docker-volume' / 'minio' |
| home_store_key | VARCHAR(512) | NO | - | Volume 키 (고정: `ws-{id}-home`) |
| home_ctx | JSONB | YES | NULL | Storage Provider 컨텍스트 |
| **conditions** | JSONB | NO | '{}' | Condition 상태 (신규) |
| **phase** | ENUM | NO | 'PENDING' | 파생 상태 (캐시) |
| **operation** | ENUM | NO | 'NONE' | 진행 중인 작업 |
| **op_started_at** | TIMESTAMP | YES | NULL | operation 시작 시점 |
| **op_id** | UUID | YES | NULL | 작업 ID (Idempotency Key) |
| **desired_state** | ENUM | NO | 'RUNNING' | 목표 상태 |
| **archive_key** | VARCHAR(512) | YES | NULL | Archive 경로 |
| **observed_at** | TIMESTAMP | YES | NULL | 마지막 관측 시점 |
| **last_access_at** | TIMESTAMP | YES | NULL | 마지막 접속 시각 |
| **standby_ttl_seconds** | INT | NO | 300 | RUNNING→STANDBY TTL |
| **archive_ttl_seconds** | INT | NO | 86400 | STANDBY→ARCHIVED TTL |
| **error_reason** | VARCHAR(50) | YES | NULL | 에러 분류 코드 |
| **error_count** | INT | NO | 0 | 연속 실패 횟수 |
| created_at | TIMESTAMP | NO | NOW() | 생성 시각 |
| updated_at | TIMESTAMP | NO | NOW() | 수정 시각 |
| deleted_at | TIMESTAMP | YES | NULL | Soft Delete 시각 |

> **굵은 글씨**: M2 신규/변경 컬럼

---

## ENUM 정의

### phase

> **Phase는 계산값**: WC가 reconcile 시 conditions를 읽어 계산/저장 (인덱스용 캐시)
> **정의**: [02-states.md#phase](./02-states.md#phase-요약)

### operation

> **정의**: [02-states.md#operation](./02-states.md#operation-진행-상태)
> **ENUM 값**: NONE, PROVISIONING, RESTORING, STARTING, STOPPING, ARCHIVING, CREATE_EMPTY_ARCHIVE, DELETING

### desired_state

> **정의**: [02-states.md#desired_state](./02-states.md#desired_state-목표)
> **ENUM 값**: DELETED, ARCHIVED, STANDBY, RUNNING (PENDING 미포함)

---

## conditions JSONB 구조

### 형식 (Dictionary)

```json
{
  "storage.volume_ready": {
    "status": true,
    "reason": "VolumeProvisioned",
    "message": "Volume is ready",
    "last_transition_time": "2026-01-01T12:00:00Z"
  },
  "storage.archive_ready": {
    "status": false,
    "reason": "NoArchive",
    "message": "No archive exists",
    "last_transition_time": "2026-01-01T12:00:00Z"
  },
  "infra.container_ready": {
    "status": true,
    "reason": "ContainerRunning",
    "message": "Container is running",
    "last_transition_time": "2026-01-01T12:00:00Z"
  },
  "policy.healthy": {
    "status": true,
    "reason": "AllConditionsMet",
    "message": "All conditions are satisfied",
    "last_transition_time": "2026-01-01T12:00:00Z"
  }
}
```

### Condition 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| status | boolean | 조건 충족 여부 |
| reason | string | 상태 이유 (CamelCase) |
| message | string | 사람이 읽는 메시지 |
| last_transition_time | string | ISO 8601 timestamp |

### 핵심 Conditions

| Condition | Owner | 설명 |
|-----------|-------|------|
| `storage.volume_ready` | WorkspaceController | Volume 존재 여부 |
| `storage.archive_ready` | WorkspaceController | Archive 접근 가능 여부 |
| `infra.container_ready` | WorkspaceController | Container running 여부 (Canonical 키) |
| `policy.healthy` | WorkspaceController | 불변식 + 정책 준수 |

> **Canonical 키**: API/UI는 백엔드 무관 `infra.container_ready` 사용. WC가 실제 백엔드(Docker/K8s) 관측 결과를 Canonical 키에 기록

### conditions 초기값 정책

| 상황 | conditions 값 | Phase 결과 |
|------|--------------|-----------|
| Workspace 생성 직후 | `{}` (빈 dict) | PENDING |
| WC 첫 관측 후 | 모든 Condition 포함 | 실제 상태 반영 |

**기본값 정책** (calculate_phase 내부):
- `policy.healthy`: **true** (관측 전에는 건강하다고 가정)
- `storage.volume_ready`: **false** (리소스 존재를 가정하지 않음)
- `storage.archive_ready`: **false** (리소스 존재를 가정하지 않음)
- `infra.container_ready`: **false** (리소스 존재를 가정하지 않음)

> **안전성**: calculate_phase()가 빈 conditions에도 기본값을 적용하여 KeyError 없이 안전하게 계산
>
> **구현**: [02-states.md#calculate_phase](./02-states.md#calculate_phase)

---

## storage.archive_ready reason 값

| reason | status | is_terminal | 설명 |
|--------|--------|-------------|------|
| ArchiveUploaded | true | - | Archive 정상 접근 가능 |
| ArchiveCorrupted | false | true | checksum 불일치 |
| ArchiveExpired | false | true | TTL 만료 |
| ArchiveNotFound | false | true | archive_key 있지만 S3에 없음 |
| ArchiveUnreachable | false | **false** | S3 일시 장애 (재시도 가능) |
| ArchiveTimeout | false | **false** | S3 요청 타임아웃 (재시도 가능) |
| NoArchive | false | - | archive_key = NULL |

> **비단말 오류**: ArchiveUnreachable/Timeout은 healthy=false 유발하지 않음 (재시도)
> **단말 오류**: Corrupted/Expired/NotFound → healthy=false → Phase=ERROR

---

## policy.healthy=false 조건

| 우선순위 | 조건 | reason | 설명 |
|---------|------|--------|------|
| 1 | container_ready ∧ !volume_ready | ContainerWithoutVolume | 불변식 위반 |
| 2 | archive_ready.reason ∈ {Corrupted, Expired, NotFound} | ArchiveAccessError | Archive 단말 오류 |

> **WC 판정**: WC가 관측 후 불변식 위반 확인하여 policy.healthy 설정
> **에러 처리**: WC가 작업 실패 시 phase=ERROR + error_reason 원자적 설정

---

## 컬럼 소유권 (Single Writer Principle)

> **계약 #3 준수**: [00-contracts.md](./00-contracts.md#3-single-writer-principle)

### WorkspaceController

| 컬럼 | 설명 |
|------|------|
| conditions | Condition 상태 (JSONB) |
| observed_at | 마지막 관측 시점 |
| phase | 파생 상태 (conditions에서 계산, 인덱스용 캐시) |
| operation | 진행 중인 작업 |
| op_started_at | operation 시작 시점 |
| op_id | 작업 고유 ID |
| archive_key | Archive 경로 (ARCHIVING 완료 시) |
| error_count | 재시도 횟수 |
| error_reason | 에러 분류 코드 |
| home_ctx | Storage Provider 컨텍스트 (restore_marker 포함) |

### API

| 컬럼 | 설명 |
|------|------|
| desired_state | 목표 상태 (API만 변경 가능) |
| deleted_at | Soft Delete 시각 |
| standby_ttl_seconds | RUNNING→STANDBY TTL |
| archive_ttl_seconds | STANDBY→ARCHIVED TTL |
| last_access_at | 마지막 접속 시각 |

> **desired_state 단일 소유자**: TTL Manager/Proxy(Auto-wake)는 내부 서비스 레이어를 통해 API 호출

---

## error_reason 값

| error_reason | is_terminal | 설명 |
|--------------|-------------|------|
| Timeout | 즉시 | 작업 시간 초과 |
| RetryExceeded | error_count 기반 | 재시도 한도 초과 |
| ActionFailed | 재시도 후 | Actuator 호출 실패 |
| DataLost | 즉시 | 복구 불가 데이터 손실 |
| Unreachable | 재시도 후 | 리소스 접근 불가 |
| ImagePullFailed | 즉시 | 컨테이너 이미지 가져오기 실패 |
| ContainerWithoutVolume | 즉시 | 불변식 위반 |
| ArchiveCorrupted | 즉시 | Archive 체크섬 불일치 |

**단말 에러(is_terminal) 판정 로직**:
```python
TERMINAL_REASONS = {"Timeout", "DataLost", "ImagePullFailed", "ContainerWithoutVolume", "ArchiveCorrupted"}
is_terminal = error_reason in TERMINAL_REASONS or error_count >= MAX_RETRY
```

> **상세 메시지**: 로그에서 확인 (DB 미저장)

---

## home_ctx 구조

| 필드 | 타입 | 설명 |
|------|------|------|
| restore_marker | string | 복원 완료 마커 (= archive_key) |

**용도**:
- RESTORING 완료 조건 판정에 사용
- `restore_marker == archive_key` → 복원 완료

---

## 인덱스

| 인덱스 | 용도 | 조건 |
|--------|------|------|
| idx_workspaces_ttl_check | TTL Manager 폴링 | `phase IN (RUNNING, STANDBY) AND operation = NONE` |
| idx_workspaces_reconcile | Reconciler 대상 조회 | `phase != desired_state OR operation != NONE` |
| idx_workspaces_operation | 진행 중 작업 조회 | `operation != NONE` |
| idx_workspaces_user_running | 사용자별 RUNNING 제한 | `owner_user_id, phase = RUNNING` |
| idx_workspaces_running | 전역 RUNNING 카운트 | `phase = RUNNING` |
| idx_workspaces_error | ERROR 상태 조회 | `phase = ERROR` |
| idx_workspaces_archived | ARCHIVED 상태 조회 | `phase = ARCHIVED` |

> 모든 인덱스는 `deleted_at IS NULL` 조건 포함
> **phase 캐시 활용**: JSONB 쿼리 대신 phase ENUM 인덱스 사용

---

## 마이그레이션 가이드

### observed_status, health_status → conditions, phase

```sql
-- 1. 새 컬럼 추가
ALTER TABLE workspaces ADD COLUMN conditions JSONB NOT NULL DEFAULT '{}'
ALTER TABLE workspaces ADD COLUMN phase VARCHAR(20) NOT NULL DEFAULT 'PENDING';

-- 2. 기존 데이터 마이그레이션
UPDATE workspaces SET
  conditions = jsonb_build_object(
    'storage.volume_ready', jsonb_build_object(
      'status', observed_status IN ('STANDBY', 'RUNNING'),
      'reason', CASE
        WHEN observed_status IN ('STANDBY', 'RUNNING') THEN 'VolumeProvisioned'
        ELSE 'VolumeNotFound'
      END,
      'last_transition_time', observed_at
    ),
    'infra.container_ready', jsonb_build_object(
      'status', observed_status = 'RUNNING',
      'reason', CASE
        WHEN observed_status = 'RUNNING' THEN 'ContainerRunning'
        ELSE 'ContainerNotRunning'
      END,
      'last_transition_time', observed_at
    ),
    'storage.archive_ready', jsonb_build_object(
      'status', archive_key IS NOT NULL,
      'reason', CASE
        WHEN archive_key IS NOT NULL THEN 'ArchiveUploaded'
        ELSE 'NoArchive'
      END,
      'last_transition_time', observed_at
    ),
    'policy.healthy', jsonb_build_object(
      'status', health_status = 'OK',
      'reason', CASE
        WHEN health_status = 'OK' THEN 'AllConditionsMet'
        ELSE COALESCE(error_reason, 'Unknown')
      END,
      'last_transition_time', observed_at
    )
  ),
  phase = CASE
    WHEN deleted_at IS NOT NULL THEN 'DELETED'
    WHEN health_status = 'ERROR' THEN 'ERROR'
    WHEN observed_status = 'RUNNING' THEN 'RUNNING'
    WHEN observed_status = 'STANDBY' THEN 'STANDBY'
    WHEN archive_key IS NOT NULL THEN 'ARCHIVED'
    ELSE 'PENDING'
  END;

-- 3. desired_state에 ARCHIVED, DELETED 추가
ALTER TYPE desired_state_enum ADD VALUE 'ARCHIVED';
ALTER TYPE desired_state_enum ADD VALUE 'DELETED';

-- 4. 기존 컬럼 제거 (확인 후)
-- ALTER TABLE workspaces DROP COLUMN observed_status;
-- ALTER TABLE workspaces DROP COLUMN health_status;
```

---

## 참조

- [00-contracts.md](./00-contracts.md) - 핵심 계약
- [02-states.md](./02-states.md) - 상태 정의 (Phase, Operation, SM)
- [04-control-plane.md#workspacecontroller](./04-control-plane.md#workspacecontroller) - WorkspaceController
- [ADR-011](../adr/011-declarative-conditions.md) - Conditions 패턴
