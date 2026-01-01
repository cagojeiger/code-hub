# DB 스키마 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

M2에서 추가/변경되는 스키마를 정의합니다. **Conditions 패턴** 기반 (ADR-011).

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
| **error_message** | TEXT | YES | NULL | 에러 요약 |
| **error_info** | JSONB | YES | NULL | 구조화된 에러 정보 |
| **error_count** | INT | NO | 0 | 연속 실패 횟수 |
| created_at | TIMESTAMP | NO | NOW() | 생성 시각 |
| updated_at | TIMESTAMP | NO | NOW() | 수정 시각 |
| deleted_at | TIMESTAMP | YES | NULL | Soft Delete 시각 |

> **굵은 글씨**: M2 신규/변경 컬럼
> **conditions JSONB**: observed_status, health_status 대체 (ADR-011)

---

## ENUM 정의

### phase (파생 상태)

| 값 | Level | 조건 | 설명 |
|----|-------|------|------|
| DELETED | -1 | deleted_at != NULL | 삭제 완료 |
| ERROR | - | !policy.healthy | 정책 위반 (Ordered 미적용) |
| RUNNING | 20 | healthy ∧ container_ready ∧ volume_ready | Container + Volume |
| STANDBY | 10 | healthy ∧ volume_ready ∧ !container_ready | Volume만 존재 |
| ARCHIVED | 5 | healthy ∧ !volume_ready ∧ archive_ready | Archive만 존재 |
| PENDING | 0 | healthy ∧ !volume_ready ∧ !archive_ready | 활성 리소스 없음 |

> **Phase는 계산값**: conditions 변경 시 HM이 phase 컬럼도 함께 업데이트

### operation

| 값 | Phase 전이 | 설명 |
|----|-----------|------|
| NONE | - | 안정 상태 |
| PROVISIONING | PENDING → STANDBY | 빈 Volume 생성 |
| RESTORING | ARCHIVED → STANDBY | Archive 복원 |
| STARTING | STANDBY → RUNNING | Container 시작 |
| STOPPING | RUNNING → STANDBY | Container 정지 |
| ARCHIVING | STANDBY → ARCHIVED | Volume → Archive |
| DELETING | → DELETED | 전체 삭제 |

### desired_state

| 값 | Level | 설명 |
|----|-------|------|
| DELETED | -1 | 삭제 요청 |
| PENDING | 0 | 리소스 없음 (Archive도 삭제) |
| ARCHIVED | 5 | Archive만 유지 |
| STANDBY | 10 | Volume만 유지 |
| RUNNING | 20 | 실행 상태 |

> **ARCHIVED 추가**: desired_state에 ARCHIVED가 없으면 step_down 시 Archive 삭제됨

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
  "infra.docker.container_ready": {
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
| `storage.volume_ready` | HealthMonitor | Volume 존재 여부 |
| `storage.archive_ready` | HealthMonitor | Archive 접근 가능 여부 |
| `infra.*.container_ready` | HealthMonitor | Container running 여부 |
| `policy.healthy` | HealthMonitor | 불변식 + 정책 준수 |

### storage.archive_ready reason 값

| reason | status | 설명 |
|--------|--------|------|
| ArchiveUploaded | true | Archive 정상 접근 가능 |
| ArchiveCorrupted | false | checksum 불일치 |
| ArchiveExpired | false | TTL 만료 |
| ArchiveNotFound | false | archive_key 있지만 S3에 없음 |
| NoArchive | false | archive_key = NULL |

### policy.healthy=false 조건

| 우선순위 | 조건 | reason | 설명 |
|---------|------|--------|------|
| 1 | container_ready ∧ !volume_ready | ContainerWithoutVolume | 불변식 위반 |
| 2 | archive_key != NULL ∧ !archive_ready | ArchiveAccessError | Archive 접근 불가 |
| 3 | error_info.is_terminal = true | (error_info.reason) | SR 작업 실패 |

---

## 컬럼 소유권 (Single Writer Principle)

> **계약 #3 준수**: [00-contracts.md](./00-contracts.md#3-single-writer-principle)

### HealthMonitor

| 컬럼 | 설명 |
|------|------|
| conditions | Condition 상태 (JSONB) |
| phase | 파생 상태 (conditions에서 계산) |
| observed_at | 마지막 관측 시점 |

> **phase 동기화**: conditions 변경 시 HM이 phase도 함께 업데이트

### StateReconciler

| 컬럼 | 설명 |
|------|------|
| operation | 진행 중인 작업 |
| op_started_at | operation 시작 시점 |
| op_id | 작업 고유 ID |
| archive_key | Archive 경로 (ARCHIVING 완료 시) |
| error_count | 재시도 횟수 |
| error_info | 에러 정보 |
| home_ctx | Storage Provider 컨텍스트 (restore_marker 포함) |

> **Single Writer 준수**: error_info는 SR 소유, conditions는 HM 소유 (예외 규칙 제거)

### API

| 컬럼 | 설명 |
|------|------|
| desired_state | 목표 상태 (API만 변경 가능) |
| deleted_at | Soft Delete 시각 |
| standby_ttl_seconds | RUNNING→STANDBY TTL |
| archive_ttl_seconds | STANDBY→ARCHIVED TTL |
| last_access_at | 마지막 접속 시각 |

> **desired_state 단일 소유자**: API만 desired_state를 변경할 수 있음
> - TTL Manager → 내부 서비스 레이어를 통해 API 호출
> - Proxy (Auto-wake) → 내부 서비스 레이어를 통해 API 호출

---

## error_info 구조

| 필드 | 타입 | 설명 |
|------|------|------|
| reason | string | 에러 유형 (Timeout, RetryExceeded, ActionFailed, DataLost, Unreachable) |
| message | string | 사람이 읽는 메시지 |
| is_terminal | boolean | true면 HM이 policy.healthy=false로 설정 |
| operation | string | 실패한 operation |
| error_count | int | 재시도 횟수 |
| context | dict | reason별 상세 정보 |
| occurred_at | string | ISO 8601 timestamp |

> 상세: [04-control-plane.md#error-policy](./04-control-plane.md#error-policy)

---

## home_ctx 구조

| 필드 | 타입 | 설명 |
|------|------|------|
| restore_marker | string | 복원 완료 마커 (= archive_key) |

**용도**:
- RESTORING 완료 조건 판정에 사용
- `restore_marker == archive_key` → 복원 완료

**흐름**:
1. SR이 `StorageProvider.restore()` 호출
2. StorageProvider가 Storage Job 실행
3. Job 성공 시 StorageProvider가 restore_marker 반환
4. SR이 `home_ctx.restore_marker = archive_key` 저장

> **계약 #7 준수**: RESTORING 완료 조건 = `volume_ready=true AND home_ctx.restore_marker=archive_key`

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
ALTER TABLE workspaces ADD COLUMN conditions JSONB NOT NULL DEFAULT '{}';
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
    'infra.docker.container_ready', jsonb_build_object(
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
        ELSE COALESCE(error_info->>'reason', 'Unknown')
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
-- (PostgreSQL ENUM 확장)
ALTER TYPE desired_state_enum ADD VALUE 'ARCHIVED';
ALTER TYPE desired_state_enum ADD VALUE 'DELETED';

-- 4. 기존 컬럼 제거 (확인 후)
-- ALTER TABLE workspaces DROP COLUMN observed_status;
-- ALTER TABLE workspaces DROP COLUMN health_status;
```

---

## Known Issues

1. ~~**desired_state 경쟁 조건**: API/TTL Manager 동시 변경 시 Last-Write-Wins~~
   - **해결됨**: 계약 #3에 따라 API만 desired_state 변경 가능

2. **ENUM 변경 제약**: PostgreSQL에서 ENUM 값 제거 불가
   - 완화: 애플리케이션 레벨에서 deprecated 값 차단

3. ~~**observed_status에 ERROR 포함**: 리소스 관측과 정책 판정 혼재~~
   - **해결됨**: Conditions 패턴으로 분리 (ADR-011)

---

## 참조

- [00-contracts.md](./00-contracts.md) - 핵심 계약
- [02-states.md](./02-states.md) - 상태 정의
- [04-control-plane.md](./04-control-plane.md) - Control Plane
- [ADR-011](../adr/011-declarative-conditions.md) - Conditions 기반 상태 표현
