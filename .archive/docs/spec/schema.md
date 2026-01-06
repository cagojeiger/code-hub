# DB 스키마

> [README.md](./README.md)로 돌아가기

---

## users

| 컬럼 | 설명 |
|-----|------|
| id | PK |
| username | 로그인 ID (unique) |
| password_hash | bcrypt/argon2id 해시 |
| created_at | 생성 시각 |
| failed_login_attempts | 연속 로그인 실패 횟수 (default: 0) |
| locked_until | 계정 잠금 해제 시각 (nullable) |
| last_failed_at | 마지막 로그인 실패 시각 (nullable) |

---

## sessions

| 컬럼 | 설명 |
|-----|------|
| id | PK (UUID) |
| user_id | FK(users.id) |
| created_at | 생성 시각 |
| expires_at | 만료 시각 |
| revoked_at | 로그아웃/폐기 시각 (nullable) |

> 세션 쿠키 값은 `sessions.id`를 담는다.

---

## workspaces

| 컬럼 | 설명 |
|-----|------|
| id | PK |
| owner_user_id | FK(users.id) |
| created_at | 생성 시각 |
| name | 이름 |
| description | 짧은 설명 |
| memo | 자유 메모 |
| status | CREATED/PROVISIONING/RUNNING/STOPPING/STOPPED/DELETING/ERROR/DELETED |
| image_ref | 이미지 참조 |
| instance_backend | =local-docker |
| storage_backend | =local-dir |
| home_store_key | Home Store 키 |
| home_ctx | opaque context (nullable, JSON/string) |
| updated_at | 수정 시각 |
| deleted_at | soft delete 시각 (nullable) |
