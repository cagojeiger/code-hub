# API (v1)

> [README.md](./README.md)로 돌아가기

> Prefix: `/api/v1`

---

## Session

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/v1/login` | 로그인 |
| POST | `/api/v1/logout` | 로그아웃 |
| GET | `/api/v1/session` | 세션 조회 |

---

## Workspaces

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/v1/workspaces` | 목록 조회 (내 것만) |
| POST | `/api/v1/workspaces` | 생성 (`{ name, description?, memo? }`) |
| GET | `/api/v1/workspaces/{id}` | 상세 조회 |
| PATCH | `/api/v1/workspaces/{id}` | 수정 (`{ name?, description?, memo? }`) |
| POST | `/api/v1/workspaces/{id}:start` | 시작 |
| POST | `/api/v1/workspaces/{id}:stop` | 정지 |
| DELETE | `/api/v1/workspaces/{id}` | 삭제 (CREATED/STOPPED/ERROR 상태에서만 가능) |

---

## Events

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/v1/events` | SSE 스트림 연결 |

---

## 성공 응답 형식

**Workspace 조회/생성/수정:**
```json
{
  "id": "01HXYZ...",
  "name": "my-workspace",
  "description": "...",
  "memo": "...",
  "status": "CREATED",
  "path": "/w/01HXYZ.../",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

> path는 trailing slash 포함: `/w/{id}/` (클라이언트가 base URL과 조합하여 사용)

**start/stop:**
```json
{
  "id": "01HXYZ...",
  "status": "PROVISIONING"
}
```

> start → PROVISIONING, stop → STOPPING 상태 반환. 최종 상태(RUNNING/STOPPED)는 폴링 또는 상세 조회로 확인.

**delete:**
```
204 No Content
```

> 삭제 요청 시 즉시 204 반환. 실제 삭제(컨테이너/스토리지 정리)는 백그라운드에서 진행.

---

## 에러 응답 형식

```json
{
  "error": {
    "code": "WORKSPACE_NOT_FOUND",
    "message": "Workspace not found"
  }
}
```

| HTTP | 코드 | 설명 |
|------|------|------|
| 400 | `INVALID_REQUEST` | 잘못된 요청 (파라미터 오류 등) |
| 401 | `UNAUTHORIZED` | 인증 필요 |
| 403 | `FORBIDDEN` | 권한 없음 |
| 404 | `WORKSPACE_NOT_FOUND` | 워크스페이스 없음 |
| 409 | `INVALID_STATE` | 현재 상태에서 불가능한 작업 |
| 429 | `TOO_MANY_REQUESTS` | 로그인 시도 횟수 초과 (Retry-After 헤더 포함) |
| 500 | `INTERNAL_ERROR` | 예상치 못한 내부 오류 |
| 502 | `UPSTREAM_UNAVAILABLE` | 프록시 연결 실패 |
