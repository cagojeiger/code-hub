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
| **observed_status** | ENUM | NO | 'PENDING' | 관측된 상태 |
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
| **previous_status** | ENUM | YES | NULL | ERROR 전 상태 |
| created_at | TIMESTAMP | NO | NOW() | 생성 시각 |
| updated_at | TIMESTAMP | NO | NOW() | 수정 시각 |
| deleted_at | TIMESTAMP | YES | NULL | Soft Delete 시각 |

> **굵은 글씨**: M2 신규/변경 컬럼

### ENUM 정의

#### observed_status

| 값 | Level | 설명 |
|----|-------|------|
| PENDING | 0 | 활성 리소스 없음 |
| STANDBY | 10 | Volume만 존재 |
| RUNNING | 20 | Container + Volume |
| ERROR | - | 오류 상태 |
| DELETED | - | Soft Delete |

> ARCHIVED는 파생 상태: `PENDING + archive_key != NULL`

#### operation

| 값 | 전이 | 설명 |
|----|------|------|
| NONE | - | 안정 상태 |
| PROVISIONING | PENDING → STANDBY | 빈 Volume 생성 |
| RESTORING | PENDING → STANDBY | Archive 복원 |
| STARTING | STANDBY → RUNNING | Container 시작 |
| STOPPING | RUNNING → STANDBY | Container 정지 |
| ARCHIVING | STANDBY → PENDING | Volume → Archive |
| DELETING | * → DELETED | 전체 삭제 |

#### desired_state

| 값 | 설명 |
|----|------|
| PENDING | Archive 상태 목표 |
| STANDBY | Volume만 유지 |
| RUNNING | 실행 상태 |

> ERROR, DELETED는 desired_state로 설정 불가

---

## 불변식

1. **Non-preemptive Operation**: `operation != NONE`이면 다른 operation 시작 불가
2. **Single Writer**: 각 컬럼은 하나의 컴포넌트만 쓸 수 있음
3. **op_id Uniqueness**: 같은 workspace에서 op_id는 고유

---

## 컬럼 소유권 (Single Writer Principle)

### HealthMonitor

| 컬럼 | 설명 |
|------|------|
| observed_status | 관측된 상태 |
| observed_at | 마지막 관측 시점 |

### StateReconciler

| 컬럼 | 설명 |
|------|------|
| operation | 진행 중인 작업 |
| op_started_at | operation 시작 시점 |
| op_id | 작업 고유 ID |
| archive_key | Archive 경로 (ARCHIVING 완료 시) |
| error_count | 재시도 횟수 |
| error_info | 에러 정보 |
| previous_status | ERROR 전환 전 상태 |

### API / TTL Manager (공유)

| 컬럼 | 설명 |
|------|------|
| desired_state | 목표 상태 |
| deleted_at | Soft Delete 시각 |
| standby_ttl_seconds | RUNNING→STANDBY TTL |
| archive_ttl_seconds | STANDBY→PENDING TTL |

> **Known Issue**: desired_state는 API와 TTL Manager가 공유 → Last-Write-Wins

---

## error_info 구조

| 필드 | 타입 | 설명 |
|------|------|------|
| reason | string | 에러 유형 (Timeout, RetryExceeded, ActionFailed) |
| operation | string | 실패한 operation |
| is_terminal | boolean | true면 ERROR 상태로 전환 |
| error_count | int | 재시도 횟수 |
| last_error | string | 마지막 에러 메시지 |
| occurred_at | string | ISO 8601 timestamp |

> 상세: [error.md](./error.md)

---

## system_locks 테이블

Coordinator 배치 작업의 **Leader Election** 용도.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| lock_name | VARCHAR(64) | PK, lock 식별자 |
| holder_id | VARCHAR(64) | lock 소유자 |
| acquired_at | TIMESTAMP | 획득 시각 |
| expires_at | TIMESTAMP | 만료 시각 |

### Lock 종류

| lock_name | 용도 | TTL |
|-----------|------|-----|
| ttl_manager | TTL Manager | 5분 |
| archive_gc | Archive GC | 10분 |

### 동작 방식

| 작업 | 규칙 |
|------|------|
| 획득 | UPSERT with `expires_at < NOW()` 조건 |
| 해제 | DELETE with `holder_id` 일치 확인 |
| 갱신 | UPDATE `expires_at` |
| Failover | expires_at 초과 시 다른 인스턴스가 획득 가능 |

---

## 인덱스

| 인덱스 | 용도 | 조건 |
|--------|------|------|
| idx_workspaces_ttl_check | TTL Manager 폴링 | `observed_status IN (RUNNING, STANDBY) AND operation = NONE` |
| idx_workspaces_reconcile | Reconciler 대상 조회 | `observed_status != desired_state OR operation != NONE` |
| idx_workspaces_operation | 진행 중 작업 조회 | `operation != NONE` |
| idx_workspaces_user_running | 사용자별 RUNNING 제한 | `owner_user_id, observed_status = RUNNING` |
| idx_workspaces_running | 전역 RUNNING 카운트 | `observed_status = RUNNING` |

> 모든 인덱스는 `deleted_at IS NULL` 조건 포함

---

## Known Issues

1. **desired_state 경쟁 조건**: API/TTL Manager 동시 변경 시 Last-Write-Wins
   - 잠재적 해결: CAS, Optimistic Locking, 우선순위 컬럼

2. **ENUM 변경 제약**: PostgreSQL에서 ENUM 값 제거 불가
   - 완화: 애플리케이션 레벨에서 deprecated 값 차단

---

## 참조

- [spec/schema.md](../spec/schema.md) - M1 스키마
- [states.md](./states.md) - 상태 정의
- [error.md](./error.md) - error_info 상세
- [components/health-monitor.md](./components/health-monitor.md) - HealthMonitor
- [components/state-reconciler.md](./components/state-reconciler.md) - StateReconciler
- [components/ttl-manager.md](./components/ttl-manager.md) - TTL Manager
