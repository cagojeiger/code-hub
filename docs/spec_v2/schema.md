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
| desired_state | ENUM | NO | 'RUNNING' | 목표 상태 (Reconciler용) |
| archive_key | VARCHAR(512) | YES | NULL | Object Storage 키 |
| last_access_at | TIMESTAMP | YES | NULL | 마지막 프록시 접속 시각 |
| warm_ttl_seconds | INT | NO | 1800 | RUNNING→WARM TTL (초) |
| cold_ttl_seconds | INT | NO | 604800 | WARM→COLD TTL (초) |
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

-- M2
ENUM(
  -- 안정 상태
  'PENDING',      -- Level 0
  'COLD',         -- Level 10
  'WARM',         -- Level 20
  'RUNNING',      -- Level 30
  -- 전이 상태
  'INITIALIZING', -- PENDING → COLD
  'RESTORING',    -- COLD → WARM
  'STARTING',     -- WARM → RUNNING
  'STOPPING',     -- RUNNING → WARM
  'ARCHIVING',    -- WARM → COLD
  'DELETING',     -- * → DELETED
  -- 예외 상태
  'ERROR',
  'DELETED'
)
```

### desired_state ENUM 값

```sql
ENUM('COLD', 'WARM', 'RUNNING')
```

> PENDING과 DELETED는 desired_state로 설정 불가

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
| **status** | ENUM | 현재 상태 |
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
WHERE status IN ('RUNNING', 'WARM') AND deleted_at IS NULL;

-- Reconciler 처리 대상
CREATE INDEX idx_workspaces_reconcile
ON workspaces (status, desired_state)
WHERE status != desired_state AND deleted_at IS NULL;
```

---

## 마이그레이션 전략

### Phase 1: 컬럼 추가

```sql
ALTER TABLE workspaces
ADD COLUMN desired_state VARCHAR(20) DEFAULT 'RUNNING',
ADD COLUMN archive_key VARCHAR(512),
ADD COLUMN last_access_at TIMESTAMP,
ADD COLUMN warm_ttl_seconds INT DEFAULT 1800,
ADD COLUMN cold_ttl_seconds INT DEFAULT 604800,
ADD COLUMN error_message TEXT,
ADD COLUMN error_count INT DEFAULT 0;
```

### Phase 2: 기존 데이터 마이그레이션

```sql
-- M1 상태 → M2 상태 매핑
UPDATE workspaces SET
  status = CASE status
    WHEN 'CREATED' THEN 'PENDING'
    WHEN 'PROVISIONING' THEN 'STARTING'
    WHEN 'STOPPED' THEN 'WARM'
    ELSE status
  END,
  desired_state = CASE
    WHEN status IN ('RUNNING', 'PROVISIONING') THEN 'RUNNING'
    WHEN status = 'STOPPED' THEN 'WARM'
    ELSE 'RUNNING'
  END
WHERE deleted_at IS NULL;
```

### Phase 3: ENUM 타입 변경

```sql
-- PostgreSQL에서 ENUM 변경
ALTER TYPE workspace_status ADD VALUE 'PENDING';
ALTER TYPE workspace_status ADD VALUE 'COLD';
ALTER TYPE workspace_status ADD VALUE 'WARM';
-- ... 기타 값 추가
```

---

## 참조

- [spec/schema.md](../spec/schema.md) - M1 스키마
- [states.md](./states.md) - 상태 정의
