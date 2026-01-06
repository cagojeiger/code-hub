# 설정 (Config)

> [README.md](./README.md)로 돌아가기

---

## 설정 예시

```yaml
server:
  bind: ":8080"
  public_base_url: "http://localhost:8080"

auth:
  mode: local
  session:
    cookie_name: "session"
    ttl: "24h"

workspace:
  default_image: "codercom/code-server:latest"
  healthcheck:
    type: http           # http 또는 tcp
    path: /healthz       # code-server 기본 헬스체크 엔드포인트
    interval: "2s"       # 폴링 간격
    timeout: "60s"       # 최대 대기시간 (초과 시 ERROR)

home_store:
  backend: local-dir
  control_plane_base_dir: "/var/lib/codehub/homes"   # Control Plane 컨테이너 내부 경로
  workspace_base_dir: "/host/var/lib/codehub/homes"  # 호스트 경로 (Docker bind mount용)

redis:
  url: "redis://localhost:6379"                      # Redis 연결 URL
```

---

## 설정 항목 설명

| 항목 | 설명 |
|------|------|
| `server.bind` | 서버 바인딩 주소 |
| `server.public_base_url` | 클라이언트에게 노출되는 기본 URL |
| `auth.session.ttl` | 세션 유효 기간 |
| `workspace.default_image` | 워크스페이스 생성 시 기본 이미지 |
| `workspace.healthcheck.*` | 헬스체크 설정 |
| `home_store.backend` | 스토리지 백엔드 (`local-dir` 또는 `object-store`) |
| `home_store.control_plane_base_dir` | Control Plane 컨테이너 내부에서 사용하는 경로 |
| `home_store.workspace_base_dir` | Docker bind mount에 사용할 호스트 경로 |
| `redis.url` | Redis 연결 URL |

---

## 경로 규칙

> CreateWorkspace 시 `workspace.default_image`, `home_store.backend` 사용.

`control_plane_base_dir`과 `workspace_base_dir`은 같은 물리적 위치를 서로 다른 관점에서 가리킴:
- `control_plane_base_dir`: Control Plane이 파일 시스템 작업(디렉토리 생성 등)에 사용
- `workspace_base_dir`: Instance Controller가 Docker bind mount 설정 시 사용 (Docker API는 호스트 경로 필요)
