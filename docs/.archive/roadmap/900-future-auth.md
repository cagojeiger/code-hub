# Roadmap 900: Future Auth & Multi-tenancy

## Status: Backlog

> MVP 이후 확장을 위한 아이디어 기록. 구현 시점 미정.

---

## 배경

MVP는 Local 단일 사용자를 위한 기본 인증만 제공합니다.
향후 다음 시나리오를 지원하기 위해 확장 가능한 구조가 필요합니다:

- OIDC/Keycloak 연동 (SSO)
- Team/Group/Organization 지원
- Role-Based Access Control (RBAC)

---

## Phase 1: OIDC Integration

### 목표
외부 Identity Provider (Keycloak, Google, GitHub 등) 연동

### API 구조 (OIDC-Ready)

```
/api/v1/auth/
├── POST   /login              ← Local: username/password
├── POST   /logout             ← Revoke session
├── GET    /session            ← Current user info
│
│   (OIDC 추가 시)
├── GET    /oidc               ← Redirect to IdP
├── GET    /oidc/callback      ← Handle IdP response
│
│   (Multi-provider 추가 시)
├── GET    /providers          ← List enabled providers
└── GET    /{provider}/login   ← Provider-specific login
```

### Config 확장

```yaml
auth:
  mode: oidc  # local → oidc
  oidc:
    issuer: "https://keycloak.example.com/realms/codehub"
    client_id: "codehub"
    client_secret: "..."
    scopes: ["openid", "profile", "email"]
```

### DB 스키마 변경

```sql
-- users 테이블 확장
ALTER TABLE users ADD COLUMN external_id VARCHAR;  -- OIDC sub claim
ALTER TABLE users ADD COLUMN provider VARCHAR DEFAULT 'local';

-- sessions 테이블 확장
ALTER TABLE sessions ADD COLUMN provider VARCHAR DEFAULT 'local';
```

### Login Flow 비교

```
Local (현재):
POST /auth/login → Verify password → Create session → Cookie

OIDC:
GET /auth/oidc → 302 to IdP → User authenticates
    ↓
GET /auth/oidc/callback?code=xxx
    ↓
Exchange code → Get tokens → Create/link user → Create session → Cookie
```

---

## Phase 2: Multi-tenancy (Team/Org)

### 목표
개인 워크스페이스 외에 팀/조직 단위 워크스페이스 지원

### DB 스키마

```
┌─────────────────────┐       ┌─────────────────────┐
│       users         │       │    organizations    │
├─────────────────────┤       ├─────────────────────┤
│ id            (PK)  │       │ id            (PK)  │
│ username            │       │ name                │
│ ...                 │       │ slug          (URL) │
└──────────┬──────────┘       │ created_at          │
           │                  └──────────┬──────────┘
           │                             │
           ▼                             ▼
┌─────────────────────┐
│   org_memberships   │
├─────────────────────┤
│ user_id       (FK)  │
│ org_id        (FK)  │
│ role                │ ← owner / admin / member
│ created_at          │
└─────────────────────┘

┌─────────────────────┐
│     workspaces      │
├─────────────────────┤
│ ...                 │
│ owner_user_id (FK)  │ ← 개인 소유
│ org_id        (FK)  │ ← nullable, 조직 소유 시
│ visibility          │ ← private / org / public
└─────────────────────┘
```

### API 확장

```
/api/v1/users/
├── GET    /me                 ← Current user profile
├── PATCH  /me                 ← Update profile
└── GET    /me/orgs            ← My organizations

/api/v1/orgs/
├── GET    /                   ← List my orgs
├── POST   /                   ← Create org
├── GET    /{org}              ← Org details
├── GET    /{org}/members      ← List members
├── POST   /{org}/invite       ← Invite member
└── DELETE /{org}/members/{id} ← Remove member

/api/v1/orgs/{org}/workspaces/
├── GET    /                   ← Org workspaces
├── POST   /                   ← Create in org
└── ...
```

### Visibility 규칙

| visibility | 접근 가능 대상 |
|------------|---------------|
| private    | owner만 |
| org        | 같은 org 멤버 |
| public     | 모든 인증된 사용자 |

---

## Phase 3: RBAC (Role-Based Access Control)

### 목표
세분화된 권한 관리

### 권한 모델

```
┌─────────────────────────────────────────────────────────┐
│                    Permission Model                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Resource: workspace                                    │
│  Actions:  create, read, update, delete, start, stop   │
│                                                         │
│  Resource: org                                          │
│  Actions:  create, read, update, delete, invite        │
│                                                         │
│  Resource: org_member                                   │
│  Actions:  read, remove, change_role                   │
│                                                         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                    Role Definitions                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  org:owner                                              │
│  └── org:*, org_member:*, workspace:*                  │
│                                                         │
│  org:admin                                              │
│  └── org:read, org_member:read,remove, workspace:*     │
│                                                         │
│  org:member                                             │
│  └── org:read, org_member:read, workspace:create,read  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 구현 시 고려사항

### MVP에서 미리 준비할 것 (선택)

1. **API 경로 구조**
   - `/api/v1/login` → `/api/v1/auth/login`
   - 향후 `/api/v1/auth/oidc` 등 추가 용이

2. **User 모델 확장 필드**
   ```python
   external_id: str | None = None   # OIDC sub
   provider: str = "local"          # local, oidc, keycloak
   ```

3. **Session 모델 확장 필드**
   ```python
   provider: str = "local"          # 어떤 방식으로 로그인했는지
   ```

### 마이그레이션 전략

1. **Phase 1 (OIDC)**
   - 기존 local 사용자 유지
   - OIDC 사용자는 `external_id`로 연결
   - 동일 email 시 계정 병합 옵션

2. **Phase 2 (Multi-tenancy)**
   - 기존 워크스페이스는 `org_id = NULL` (개인 소유)
   - 조직 생성 후 워크스페이스 이전 가능

---

## 참고 자료

- [OIDC Spec](https://openid.net/specs/openid-connect-core-1_0.html)
- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [GitHub Organizations Model](https://docs.github.com/en/organizations)
