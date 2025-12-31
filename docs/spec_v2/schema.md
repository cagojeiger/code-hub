# DB 스키마 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

M2에서 추가/변경되는 스키마를 정의합니다. M1 스키마는 [spec/schema.md](../spec/schema.md) 참조.

---

## workspaces 테이블 변경

### 신규 컬럼

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|------|------|----------|--------|------|
| **operation** | ENUM | NO | 'NONE' | 진행 중인 작업 (신규) |
| desired_state | ENUM | NO | 'RUNNING' | 목표 상태 (Reconciler용) |
| archive_key | VARCHAR(512) | YES | NULL | Object Storage 키 (`archives/{workspace_id}/{op_id}/home.tar.gz`) |
| op_id | UUID | YES | NULL | 현재 작업 ID (멱등성 보장) |
| last_access_at | TIMESTAMP | YES | NULL | 마지막 프록시 접속 시각 |
| standby_ttl_seconds | INT | NO | 300 | RUNNING→STANDBY TTL (초) - WebSocket 기반 |
| archive_ttl_seconds | INT | NO | 86400 | STANDBY→PENDING(ARCHIVED) TTL (초) - DB 기반 |
| error_message | TEXT | YES | NULL | 에러 상세 메시지 |
| error_count | INT | NO | 0 | 연속 전환 실패 횟수 |
| previous_status | ENUM | YES | NULL | ERROR 전 상태 (복구용) |

### 변경 컬럼

| 컬럼 | 변경 내용 |
|------|----------|
| status | ENUM 값 변경 (아래 참조) |
| storage_backend | `local-dir` → `docker-volume`, `minio` 추가 |

### status ENUM 값

```sql
-- M1
ENUM('CREATED', 'PROVISIONING', 'RUNNING', 'STOPPING', 'STOPPED', 'DELETING', 'ERROR', 'DELETED')

-- M2 (5개 - Active 상태만, ARCHIVED는 파생)
ENUM(
  'PENDING',   -- Level 0: 활성 리소스 없음 (archive_key 있으면 Display: ARCHIVED)
  'STANDBY',   -- Level 10: Volume만 존재
  'RUNNING',   -- Level 20: Container + Volume
  'ERROR',     -- 오류 (레벨 없음)
  'DELETED'    -- 소프트 삭제 (레벨 없음)
)
```

> - COLD 제거: ARCHIVED는 파생 상태 (PENDING + archive_key != NULL)
> - WARM → STANDBY: 용어 통일
> - 전이 상태(PROVISIONING, STARTING 등)는 `operation` 컬럼으로 분리됨

### operation ENUM 값

```sql
ENUM(
  'NONE',         -- 작업 없음 (안정 상태)
  'PROVISIONING', -- PENDING → STANDBY (빈 Volume 생성)
  'RESTORING',    -- PENDING(has_archive) → STANDBY (Archive 복원)
  'STARTING',     -- STANDBY → RUNNING
  'STOPPING',     -- RUNNING → STANDBY
  'ARCHIVING',    -- STANDBY → PENDING + has_archive
  'DELETING'      -- * → DELETED
)
```

### desired_state ENUM 값

```sql
ENUM('PENDING', 'STANDBY', 'RUNNING')
```

> - PENDING: 아카이브 상태 목표 (archive_key 있으면 Display: ARCHIVED)
> - STANDBY: Volume만 유지 (Container 없음)
> - RUNNING: 실행 상태
> - ERROR, DELETED는 desired_state로 설정 불가

---

## 동시성 보장

**workspace당 operation은 동시에 1개만 실행 가능**:
- `operation != 'NONE'`이면 다른 작업 시작 불가
- 동일 workspace에 대한 동시 Archive/Restore 없음
- 따라서 동시 덮어쓰기 이슈 없음

---

## 미래 개선 사항

### desired_state 동시성 제어

> ⚠️ **현재 한계**: `desired_state`는 CAS 없이 업데이트됩니다. 여러 소스(API, TTL Manager, Proxy)가 동시에 변경 시 경쟁 조건 발생 가능.

**잠재적 해결책**:

| 방식 | 구현 | 장단점 |
|------|------|--------|
| CAS | `UPDATE ... WHERE desired_state = ?` | 단순, 재시도 필요 |
| 우선순위 | `last_manual_change_at` 컬럼 추가 | 사용자 의도 보존 |
| 버전 | `version` 컬럼 + Optimistic Lock | 범용적, 복잡도 증가 |

**예시 (CAS)**:

```sql
-- 현재 (경쟁 조건 있음)
UPDATE workspaces SET desired_state = 'STANDBY' WHERE id = ?;

-- 개선안 (CAS)
UPDATE workspaces SET desired_state = 'STANDBY'
WHERE id = ? AND desired_state = 'RUNNING';
-- affected_rows = 0이면 재시도 또는 skip
```

**예시 (우선순위)**:

```sql
-- 컬럼 추가
ALTER TABLE workspaces ADD COLUMN last_manual_change_at TIMESTAMP;

-- TTL Manager는 우선순위 체크
UPDATE workspaces SET desired_state = 'STANDBY'
WHERE id = ?
  AND (last_manual_change_at IS NULL
       OR last_manual_change_at < NOW() - INTERVAL '5 minutes');
```

> **Note**: M2에서는 Last-Write-Wins로 동작합니다. 상세: [activity.md](./activity.md#known-issues)

---

## system_locks 테이블 (신규)

TTL Manager, Archive GC 등 **주기적 배치 작업**의 단일 인스턴스 실행을 보장합니다.

### 스키마

| 컬럼 | 타입 | Nullable | 설명 |
|------|------|----------|------|
| lock_name | VARCHAR(64) | NO | PK, lock 식별자 |
| holder_id | VARCHAR(64) | NO | lock 소유자 (인스턴스 ID) |
| acquired_at | TIMESTAMP | NO | lock 획득 시각 |
| expires_at | TIMESTAMP | NO | lock 만료 시각 |

### Lock 이름

| lock_name | 용도 | 만료 TTL |
|-----------|------|----------|
| `ttl_manager` | TTL Manager | 5분 |
| `archive_gc` | Archive GC | 10분 |

### 동작 방식

| 단계 | SQL |
|------|-----|
| 획득 시도 | `INSERT ... ON CONFLICT DO UPDATE WHERE expires_at < NOW()` |
| 해제 | `DELETE WHERE lock_name = ? AND holder_id = ?` |
| 갱신 | `UPDATE expires_at WHERE lock_name = ? AND holder_id = ?` |

> **Dead Lock 방지**: `expires_at` 초과 시 다른 인스턴스가 덮어쓰기 가능

### DDL

```sql
CREATE TABLE system_locks (
  lock_name VARCHAR(64) PRIMARY KEY,
  holder_id VARCHAR(64) NOT NULL,
  acquired_at TIMESTAMP NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMP NOT NULL
);

-- 만료된 lock 정리용 인덱스
CREATE INDEX idx_system_locks_expires ON system_locks (expires_at);
```

---

## 전체 workspaces 스키마 (M2)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| owner_user_id | UUID | FK(users.id) |
| name | VARCHAR(255) | 이름 |
| description | TEXT | 짧은 설명 |
| memo | TEXT | 자유 메모 |
| image_ref | VARCHAR(512) | 컨테이너 이미지 참조 |
| instance_backend | ENUM | 'local-docker' / 'k8s' |
| storage_backend | ENUM | 'docker-volume' / 'minio' |
| home_store_key | VARCHAR(512) | Volume 키 (`ws-{workspace_id}-home` 고정) |
| home_ctx | JSONB | Storage Provider 컨텍스트 |
| **status** | ENUM | 현재 상태 (5개: PENDING, STANDBY, RUNNING, ERROR, DELETED) |
| **operation** | ENUM | 진행 중인 작업 (신규) |
| **desired_state** | ENUM | 목표 상태 (신규: PENDING, STANDBY, RUNNING) |
| **archive_key** | VARCHAR(512) | Object Storage 키 (신규) |
| **op_id** | UUID | 현재 작업 ID (신규) |
| **last_access_at** | TIMESTAMP | 마지막 접속 시각 (신규) |
| **standby_ttl_seconds** | INT | RUNNING→STANDBY TTL (신규) |
| **archive_ttl_seconds** | INT | STANDBY→PENDING(ARCHIVED) TTL (신규) |
| **error_message** | TEXT | 에러 메시지 (신규) |
| **error_count** | INT | 에러 횟수 (신규) |
| **previous_status** | ENUM | ERROR 전 상태 (신규) |
| created_at | TIMESTAMP | 생성 시각 |
| updated_at | TIMESTAMP | 수정 시각 |
| deleted_at | TIMESTAMP | 소프트 삭제 시각 |

---

## 인덱스

### 신규 인덱스

```sql
-- TTL 체크용 (TTL Manager 폴링)
CREATE INDEX idx_workspaces_ttl_check
ON workspaces (status, last_access_at)
WHERE status IN ('RUNNING', 'STANDBY')
  AND operation = 'NONE'
  AND deleted_at IS NULL;

-- Reconciler 처리 대상 (전환 필요)
CREATE INDEX idx_workspaces_reconcile
ON workspaces (status, desired_state, operation)
WHERE (status != desired_state OR operation != 'NONE')
  AND deleted_at IS NULL;

-- 진행 중인 작업 조회
CREATE INDEX idx_workspaces_operation
ON workspaces (operation)
WHERE operation != 'NONE' AND deleted_at IS NULL;

-- 사용자별 RUNNING 워크스페이스 (제한 체크용)
CREATE INDEX idx_workspaces_user_running
ON workspaces (owner_user_id, status)
WHERE status = 'RUNNING' AND deleted_at IS NULL;

-- 전역 RUNNING 카운트 (제한 체크용)
CREATE INDEX idx_workspaces_running
ON workspaces (status)
WHERE status = 'RUNNING' AND deleted_at IS NULL;
```

---

## 마이그레이션 전략

### Phase 1: 컬럼 추가

```sql
ALTER TABLE workspaces
ADD COLUMN operation VARCHAR(20) DEFAULT 'NONE',
ADD COLUMN desired_state VARCHAR(20) DEFAULT 'RUNNING',
ADD COLUMN archive_key VARCHAR(512),
ADD COLUMN op_id UUID,
ADD COLUMN last_access_at TIMESTAMP,
ADD COLUMN standby_ttl_seconds INT DEFAULT 300,
ADD COLUMN archive_ttl_seconds INT DEFAULT 86400,
ADD COLUMN error_message TEXT,
ADD COLUMN error_count INT DEFAULT 0,
ADD COLUMN previous_status VARCHAR(20);
```

### Phase 2: 기존 데이터 마이그레이션

```sql
-- M1 상태 → M2 (status + operation) 매핑
UPDATE workspaces SET
  -- 전이 상태는 status + operation으로 분리
  operation = CASE status
    WHEN 'PROVISIONING' THEN 'STARTING'
    WHEN 'STOPPING' THEN 'STOPPING'
    WHEN 'DELETING' THEN 'DELETING'
    ELSE 'NONE'
  END,
  status = CASE status
    WHEN 'CREATED' THEN 'PENDING'
    WHEN 'PROVISIONING' THEN 'STANDBY'  -- STANDBY + STARTING
    WHEN 'STOPPED' THEN 'STANDBY'
    WHEN 'STOPPING' THEN 'RUNNING'      -- RUNNING + STOPPING
    ELSE status
  END,
  desired_state = CASE
    WHEN status IN ('RUNNING', 'PROVISIONING') THEN 'RUNNING'
    WHEN status IN ('STOPPED', 'STOPPING') THEN 'STANDBY'
    ELSE 'RUNNING'
  END
WHERE deleted_at IS NULL;
```

### Phase 3: ENUM 타입 변경

```sql
-- PostgreSQL에서 ENUM 변경
-- status: 5개 값으로 단순화 (COLD 제거, WARM → STANDBY)
ALTER TYPE workspace_status ADD VALUE 'PENDING';
ALTER TYPE workspace_status ADD VALUE 'STANDBY';
-- PROVISIONING, STOPPING 등 제거는 불가 → 사용하지 않도록 제약

-- operation: 새 타입 생성 (INITIALIZING 제거, PROVISIONING 추가)
CREATE TYPE workspace_operation AS ENUM (
  'NONE', 'PROVISIONING', 'RESTORING', 'STARTING',
  'STOPPING', 'ARCHIVING', 'DELETING'
);

-- desired_state: 새 타입 생성
CREATE TYPE workspace_desired_state AS ENUM (
  'PENDING', 'STANDBY', 'RUNNING'
);
```

---

## 참조

- [spec/schema.md](../spec/schema.md) - M1 스키마
- [states.md](./states.md) - 상태 정의
- [activity.md](./activity.md) - TTL Manager (system_locks 사용)
- [storage-gc.md](./storage-gc.md) - Archive GC (system_locks 사용)
- [limits.md](./limits.md) - RUNNING 제한
