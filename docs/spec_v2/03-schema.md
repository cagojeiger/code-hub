# DB 스키마 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

M2에서 추가/변경되는 스키마를 정의합니다. M1 스키마는 [spec/schema.md](../spec/schema.md) 참조.

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
| **observed_status** | ENUM | NO | 'PENDING' | 관측된 리소스 상태 |
| **health_status** | ENUM | NO | 'OK' | 정책 판정 상태 (신규) |
| **operation** | ENUM | NO | 'NONE' | 진행 중인 작업 |
| **op_started_at** | TIMESTAMP | YES | NULL | operation 시작 시점 |
| **op_id** | UUID | YES | NULL | 작업 ID (Idempotency Key) |
| **desired_state** | ENUM | NO | 'RUNNING' | 목표 상태 |
| **archive_key** | VARCHAR(512) | YES | NULL | Archive 경로 |
| **observed_at** | TIMESTAMP | YES | NULL | 마지막 관측 시점 |
| **last_access_at** | TIMESTAMP | YES | NULL | 마지막 접속 시각 |
| **standby_ttl_seconds** | INT | NO | 300 | RUNNING→STANDBY TTL |
| **archive_ttl_seconds** | INT | NO | 86400 | STANDBY→PENDING TTL |
| **error_message** | TEXT | YES | NULL | 에러 요약 |
| **error_info** | JSONB | YES | NULL | 구조화된 에러 정보 |
| **error_count** | INT | NO | 0 | 연속 실패 횟수 |
| **previous_status** | ENUM | YES | NULL | health_status=ERROR 전환 전 observed_status (복구용) |
| created_at | TIMESTAMP | NO | NOW() | 생성 시각 |
| updated_at | TIMESTAMP | NO | NOW() | 수정 시각 |
| deleted_at | TIMESTAMP | YES | NULL | Soft Delete 시각 |

> **굵은 글씨**: M2 신규/변경 컬럼

---

## ENUM 정의

### observed_status (리소스 관측)

| 값 | Level | 설명 |
|----|-------|------|
| PENDING | 0 | 활성 리소스 없음 |
| STANDBY | 10 | Volume만 존재 |
| RUNNING | 20 | Container + Volume |
| DELETED | - | Soft Delete 완료 |

> **ERROR 없음**: observed_status는 순수 리소스 관측 결과만 반영
> ARCHIVED는 파생 상태: `PENDING + archive_key != NULL`

### health_status (정책 판정)

| 값 | 설명 |
|----|------|
| OK | 정상 상태 |
| ERROR | 불변식 위반, timeout, 재시도 초과 등 |

> **계약 #1 준수**: health_status는 observed_status와 독립적인 축

### operation

| 값 | 전이 | 설명 |
|----|------|------|
| NONE | - | 안정 상태 |
| PROVISIONING | PENDING → STANDBY | 빈 Volume 생성 |
| RESTORING | PENDING → STANDBY | Archive 복원 |
| STARTING | STANDBY → RUNNING | Container 시작 |
| STOPPING | RUNNING → STANDBY | Container 정지 |
| ARCHIVING | STANDBY → PENDING | Volume → Archive |
| DELETING | → DELETED | 전체 삭제 (조건: operation=NONE, observed_status=PENDING OR health_status=ERROR) |

### desired_state

| 값 | 설명 |
|----|------|
| PENDING | Archive 상태 목표 |
| STANDBY | Volume만 유지 |
| RUNNING | 실행 상태 |

> ERROR, DELETED는 desired_state로 설정 불가

---

## 컬럼 소유권 (Single Writer Principle)

> **계약 #3 준수**: [00-contracts.md](./00-contracts.md#3-single-writer-principle)

### HealthMonitor

| 컬럼 | 설명 |
|------|------|
| observed_status | 관측된 리소스 상태 (PENDING/STANDBY/RUNNING/DELETED) |
| health_status | 정책 판정 상태 (OK/ERROR) |
| observed_at | 마지막 관측 시점 |

### StateReconciler

| 컬럼 | 설명 |
|------|------|
| operation | 진행 중인 작업 |
| op_started_at | operation 시작 시점 |
| op_id | 작업 고유 ID |
| archive_key | Archive 경로 (ARCHIVING 완료 시) |
| error_count | 재시도 횟수 |
| error_info | 에러 정보 (주 소유자) |
| previous_status | health_status=ERROR 전환 전 observed_status (복구용) |
| home_ctx | Storage Provider 컨텍스트 (restore_marker 포함) |

> **예외 규칙 (error_info)**: 원칙적으로 error_info는 StateReconciler가 쓰지만,
> ContainerWithoutVolume 같은 불변식 위반에서 error_info가 NULL이면 HealthMonitor가 1회 설정할 수 있다.
> (조건: per-error-event, error_info=NULL인 경우에만)

### API

| 컬럼 | 설명 |
|------|------|
| desired_state | 목표 상태 (API만 변경 가능) |
| deleted_at | Soft Delete 시각 |
| standby_ttl_seconds | RUNNING→STANDBY TTL |
| archive_ttl_seconds | STANDBY→PENDING TTL |
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
| is_terminal | boolean | true면 HM이 health_status=ERROR로 설정 |
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

> **계약 #7 준수**: RESTORING 완료 조건 = `observed_status=STANDBY AND home_ctx.restore_marker=archive_key`

---

## 인덱스

| 인덱스 | 용도 | 조건 |
|--------|------|------|
| idx_workspaces_ttl_check | TTL Manager 폴링 | `observed_status IN (RUNNING, STANDBY) AND health_status = OK AND operation = NONE` |
| idx_workspaces_reconcile | Reconciler 대상 조회 | `observed_status != desired_state OR operation != NONE OR health_status = ERROR` |
| idx_workspaces_operation | 진행 중 작업 조회 | `operation != NONE` |
| idx_workspaces_user_running | 사용자별 RUNNING 제한 | `owner_user_id, observed_status = RUNNING, health_status = OK` |
| idx_workspaces_running | 전역 RUNNING 카운트 | `observed_status = RUNNING AND health_status = OK` |
| idx_workspaces_error | ERROR 상태 조회 | `health_status = ERROR` |

> 모든 인덱스는 `deleted_at IS NULL` 조건 포함

---

## Known Issues

1. ~~**desired_state 경쟁 조건**: API/TTL Manager 동시 변경 시 Last-Write-Wins~~
   - **해결됨**: 계약 #3에 따라 API만 desired_state 변경 가능 (TTL Manager, Proxy는 내부 서비스 레이어 통해 API 호출)

2. **ENUM 변경 제약**: PostgreSQL에서 ENUM 값 제거 불가
   - 완화: 애플리케이션 레벨에서 deprecated 값 차단

3. ~~**observed_status에 ERROR 포함**: 리소스 관측과 정책 판정 혼재~~
   - **해결됨**: health_status를 별도 컬럼으로 분리 (계약 #1 준수)

---

## 참조

- [00-contracts.md](./00-contracts.md) - 핵심 계약
- [02-states.md](./02-states.md) - 상태 정의
- [04-control-plane.md](./04-control-plane.md) - Control Plane
- [spec/schema.md](../spec/schema.md) - M1 스키마
