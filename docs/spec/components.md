# 구성요소 및 규칙

> [README.md](./README.md)로 돌아가기

---

## 구성요소 및 책임

| 구성요소 | 책임 |
|---------|------|
| Control Plane | `/api/v1/*` API 제공, `/w/{workspace_id}/*` 프록시 (auth → authorize → proxy), Workspace 메타데이터 관리 |
| Instance Controller (Local Docker) | Workspace Instance lifecycle 관리, Storage Provider 결과를 `/home/coder`에 마운트 |
| Storage Provider | local-dir: host dir 마운트 / object-store: restore/persist (클라우드용) |

---

## 고정 규칙

### URL 규칙

| 용도 | 패턴 |
|-----|------|
| API | `/api/v1/*` |
| Workspace 접속 | `/w/{workspace_id}/*` |

### 프록시 규칙

> 상세 내용은 [proxy.md](./proxy.md) 참조

- `/w/{workspace_id}` 요청은 **308 Redirect → `/w/{workspace_id}/`** 로 정규화
- 프록시는 prefix를 strip하고 code-server에 전달, 상대경로로 응답
- **미지원**: PWA 설치, 오프라인 모드, `/absproxy/:port` (영구 수용)

### ID 규칙

- `workspace_id`: ULID/UUID (추측 가능한 증가형 금지)

### Home Store Key 규칙

- DB에는 절대경로가 아닌 논리 키만 저장
- 패턴: `users/{user_id}/workspaces/{workspace_id}/home`
