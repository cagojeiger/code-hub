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
| archive_key | VARCHAR(512) | YES | NULL | Object Storage 키 |
| last_access_at | TIMESTAMP | YES | NULL | 마지막 프록시 접속 시각 |
| warm_ttl_seconds | INT | NO | 300 | RUNNING→WARM TTL (초) - WebSocket 기반 |
| cold_ttl_seconds | INT | NO | 86400 | WARM→COLD TTL (초) - DB 기반 |
| error_message | TEXT | YES | NULL | 에러 상세 메시지 |
| error_count | INT | NO | 0 | 연속 전환 실패 횟수 |

### 변경 컬럼

| 컬럼 | 변경 내용 |
|------|----------|
| status | ENUM 값 변경 (아래 참조) |
| storage_backend | `local-dir` → `docker-volume`, `minio` 추가 |

### status ENUM 값

```sql
-- M1
ENUM('CREATED', 'PROVISIONING', 'RUNNING', 'STOPPING', 'STOPPED', 'DELETING', 'ERROR', 'DELETED')

-- M2 (6개 - 핵심 상태만)
ENUM(
  'PENDING',   -- Level 0: 리소스 없음
  'COLD',      -- Level 10: Object Storage
  'WARM',      -- Level 20: Volume
  'RUNNING',   -- Level 30: Container + Volume
  'ERROR',     -- 오류 (레벨 없음)
  'DELETED'    -- 소프트 삭제 (레벨 없음)
)
```

> 전이 상태(INITIALIZING, STARTING 등)는 `operation` 컬럼으로 분리됨

### operation ENUM 값

```sql
ENUM(
  'NONE',         -- 작업 없음 (안정 상태)
  'INITIALIZING', -- PENDING → COLD
  'RESTORING',    -- COLD → WARM
  'STARTING',     -- WARM → RUNNING
  'STOPPING',     -- RUNNING → WARM
  'ARCHIVING',    -- WARM → COLD
  'DELETING'      -- * → DELETED
)
```

### desired_state ENUM 값

```sql
ENUM('COLD', 'WARM', 'RUNNING')
```

> PENDING, ERROR, DELETED는 desired_state로 설정 불가

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
| home_store_key | VARCHAR(512) | Storage Provider 키 |
| home_ctx | JSONB | Storage Provider 컨텍스트 |
| **status** | ENUM | 현재 상태 (6개) |
| **operation** | ENUM | 진행 중인 작업 (신규) |
| **desired_state** | ENUM | 목표 상태 (신규) |
| **archive_key** | VARCHAR(512) | Object Storage 키 (신규) |
| **last_access_at** | TIMESTAMP | 마지막 접속 시각 (신규) |
| **warm_ttl_seconds** | INT | RUNNING→WARM TTL (신규) |
| **cold_ttl_seconds** | INT | WARM→COLD TTL (신규) |
| **error_message** | TEXT | 에러 메시지 (신규) |
| **error_count** | INT | 에러 횟수 (신규) |
| created_at | TIMESTAMP | 생성 시각 |
| updated_at | TIMESTAMP | 수정 시각 |
| deleted_at | TIMESTAMP | 소프트 삭제 시각 |

---

## 인덱스

### 신규 인덱스

```sql
-- TTL 체크용 (Reconciler 폴링)
CREATE INDEX idx_workspaces_ttl_check
ON workspaces (status, last_access_at)
WHERE status IN ('RUNNING', 'WARM')
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
ADD COLUMN last_access_at TIMESTAMP,
ADD COLUMN warm_ttl_seconds INT DEFAULT 300,
ADD COLUMN cold_ttl_seconds INT DEFAULT 86400,
ADD COLUMN error_message TEXT,
ADD COLUMN error_count INT DEFAULT 0;
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
    WHEN 'PROVISIONING' THEN 'WARM'  -- WARM + STARTING
    WHEN 'STOPPED' THEN 'WARM'
    WHEN 'STOPPING' THEN 'RUNNING'   -- RUNNING + STOPPING
    ELSE status
  END,
  desired_state = CASE
    WHEN status IN ('RUNNING', 'PROVISIONING') THEN 'RUNNING'
    WHEN status IN ('STOPPED', 'STOPPING') THEN 'WARM'
    ELSE 'RUNNING'
  END
WHERE deleted_at IS NULL;
```

### Phase 3: ENUM 타입 변경

```sql
-- PostgreSQL에서 ENUM 변경
-- status: 6개 값으로 단순화
ALTER TYPE workspace_status ADD VALUE 'PENDING';
ALTER TYPE workspace_status ADD VALUE 'COLD';
ALTER TYPE workspace_status ADD VALUE 'WARM';
-- PROVISIONING, STOPPING 등 제거는 불가 → 사용하지 않도록 제약

-- operation: 새 타입 생성
CREATE TYPE workspace_operation AS ENUM (
  'NONE', 'INITIALIZING', 'RESTORING', 'STARTING',
  'STOPPING', 'ARCHIVING', 'DELETING'
);
```

---

## 참조

- [spec/schema.md](../spec/schema.md) - M1 스키마
- [states.md](./states.md) - 상태 정의
- [activity.md](./activity.md) - 활동 감지 메커니즘
- [limits.md](./limits.md) - RUNNING 제한
