# code-hub Spec (Local MVP)

> 용어 정의는 [glossary.md](./glossary.md) 참조

---

## 1. 개요

로컬에서 **Workspace(메타데이터)** 를 생성/관리하고, 필요 시 **Workspace Instance(code-server)** 를 띄워 `/w/{workspace_id}` 프록시로 접속하는 워크스페이스 허브.

---

## 2. 범위

### 포함 (Local MVP)

- 로그인: 기본 계정(id/pw)
- Workspace: 이름/설명/메모 + Home Store Key를 가진 메타데이터
- Workspace Instance: Docker 기반 code-server 컨테이너 (Workspace 1개당 1개)
- 접속: Control Plane이 `/w/{workspace_id}` 리버스 프록시(게이트웨이) 내장
- 보안: 내 워크스페이스만 목록/접속/조작 (owner 강제)
- Home Store: 로컬은 host dir(마운트), 클라우드는 object storage로 확장 가능하도록 인터페이스 고정

### 제외

- Git 자동 clone/pull
- 컨테이너에 Docker 소켓 제공(로컬 Docker 제어)
- 멀티 노드/클러스터 운영
- TTL 자동 stop (MVP 제외, 추후 추가)

---

## 3. 구성요소 및 책임

| 구성요소 | 책임 |
|---------|------|
| Control Plane | `/api/v1/*` API 제공, `/w/{workspace_id}/*` 프록시 (auth → authorize → proxy), Workspace 메타데이터 관리 |
| Runner (Local Docker) | Workspace Instance lifecycle 관리, HomeStoreBackend 결과를 `/home/coder`에 마운트 |
| HomeStoreBackend | local-dir: host dir 마운트 / object-store: restore/persist (클라우드용) |

---

## 4. 고정 규칙

### URL 규칙

| 용도 | 패턴 |
|-----|------|
| API | `/api/v1/*` |
| Workspace 접속 | `/w/{workspace_id}/*` |

### ID 규칙

- `workspace_id`: ULID/UUID (추측 가능한 증가형 금지)

### Home Store Key 규칙

- DB에는 절대경로가 아닌 논리 키만 저장
- 패턴: `users/{user_id}/workspaces/{workspace_id}/home`

---

## 5. 핵심 플로우

### Workspace Open (`/w/{workspace_id}`)

1. 세션 확인
2. owner 인가 (불일치 시 403)
3. STOPPED면 StartWorkspace
4. ResolveUpstream 후 프록시 연결

> WebSocket 업그레이드 필수 (code-server)

### Runner 인터페이스

```
CreateWorkspace(owner_user_id, workspace_id, image_ref, spec)
StartWorkspace(workspace_id)
StopWorkspace(workspace_id) -> { persist_op_id? }
DeleteWorkspace(workspace_id)
GetStatus(workspace_id) -> { status, persist? }
ResolveUpstream(workspace_id) -> { host, port }
```

### HomeStoreBackend 인터페이스

```
PrepareHome(user_id, workspace_id, home_store_key, restore_ref?) -> {
  home_mount,
  home_ref,
  pod_template_patch?
}
PersistHome(user_id, workspace_id, home_store_key, home_ref) -> { persist_op_id? }
GetPersistStatus(persist_op_id) -> { state }
CleanupHome(home_ref)
PurgeHome(home_store_key)
```

---

## 6. API (v1)

> Prefix: `/api/v1`

### Session

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/v1/login` | 로그인 |
| POST | `/api/v1/logout` | 로그아웃 |
| GET | `/api/v1/session` | 세션 조회 |

### Workspaces

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/v1/workspaces` | 목록 조회 (내 것만) |
| POST | `/api/v1/workspaces` | 생성 (`{ name, description?, memo? }`) |
| GET | `/api/v1/workspaces/{id}` | 상세 조회 |
| PATCH | `/api/v1/workspaces/{id}` | 수정 (`{ name?, description?, memo? }`) |
| POST | `/api/v1/workspaces/{id}:start` | 시작 |
| POST | `/api/v1/workspaces/{id}:stop` | 정지 |
| DELETE | `/api/v1/workspaces/{id}` | 삭제 |
| GET | `/api/v1/workspaces/{id}/authorize` | 인가 확인 (200 or 403) |

---

## 7. DB 스키마

### users

| 컬럼 | 설명 |
|-----|------|
| id | PK |
| provider | =local |
| subject | =username |

### workspaces

| 컬럼 | 설명 |
|-----|------|
| id | PK |
| owner_user_id | 소유자 |
| name | 이름 |
| description | 짧은 설명 |
| memo | 자유 메모 |
| status | RUNNING/STOPPED/ERROR/DELETED |
| image_ref | 이미지 참조 |
| backend | =local-docker |
| home_store_backend | =local-dir |
| home_store_key | Home Store 키 |
| updated_at | 수정 시각 |
| deleted_at | soft delete 시각 |

---

## 8. 설정 (Config)

```yaml
server:
  bind: ":8080"
  public_base_url: "http://localhost:8080"

auth:
  mode: local
  session:
    cookie_name: "session"

home_store:
  backend: local-dir
  base_dir: "/data/home"
```

---

## 9. MVP 완료 기준

- [ ] 내 계정으로 생성 → `/w/{workspace_id}` 접속 성공
- [ ] 다른 계정으로 `/w/{workspace_id}` 직접 접근 → 403
- [ ] STOP 후 START → Home 유지 (Home Store 기준)
- [ ] WebSocket 포함 정상 동작 (터미널/에디터)

### (추후) TTL 도입 준비

- `last_access_at`, `expires_at` 컬럼/마이그레이션 준비
- `/w/{workspace_id}` 처리 파이프라인에 `touch` 훅 넣기 쉬운 구조 유지
- Reaper(스케줄러) 추가 시 `Runner.Stop`/DB 업데이트가 멱등적으로 동작
