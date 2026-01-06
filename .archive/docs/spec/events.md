# Real-time Events (SSE)

> [README.md](./README.md)로 돌아가기

워크스페이스 상태 변경을 실시간으로 클라이언트에게 전달합니다.

---

## 이벤트 타입

| 이벤트 | 발생 시점 |
|--------|----------|
| `workspace_updated` | 워크스페이스 생성, 수정, 상태 변경 |
| `workspace_deleted` | 워크스페이스 삭제 |

---

## SSE 응답 형식

```
event: workspace_updated
data: {"id": "01HXYZ...", "name": "my-workspace", "status": "RUNNING", ...}

event: workspace_deleted
data: {"id": "01HXYZ..."}

event: heartbeat
data: {}
```

> heartbeat는 30초 간격으로 전송 (연결 유지)

---

## workspace_updated 페이로드

| 필드 | 타입 | 설명 |
|------|------|------|
| id | string | 워크스페이스 ID (ULID) |
| name | string | 워크스페이스 이름 |
| description | string \| null | 설명 |
| memo | string \| null | 메모 |
| status | string | 상태 (CREATED, PROVISIONING, RUNNING, STOPPING, STOPPED, DELETING, DELETED, ERROR) |
| created_at | string | 생성 시각 (ISO 8601) |
| updated_at | string | 수정 시각 (ISO 8601) |

> 클라이언트는 `id`로 URL 생성 가능: `{public_base_url}/w/{id}/`

---

## workspace_deleted 페이로드

| 필드 | 타입 | 설명 |
|------|------|------|
| id | string | 삭제된 워크스페이스 ID |

---

## Redis 채널 네이밍 규칙

**패턴**: `{domain}:{scope}:{scope_id}`

| 채널 | 용도 | 현재 |
|------|------|------|
| `events:user:{user_id}` | 사용자별 이벤트 | ✅ 사용 |
| `events:workspace:{ws_id}` | 워크스페이스별 이벤트 | 추후 |
| `events:system:global` | 시스템 공지 | 추후 |

> 클라이언트는 자신의 채널만 구독. 다른 사용자의 이벤트는 수신 불가.
