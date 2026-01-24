# Roadmap 901: 포트 포워딩 (미래)

## Status: Future

> code-server 내부에서 개발 서버(3000, 5000 등)를 띄웠을 때 브라우저 접근 지원

---

## 문제 상황

```
Browser → Control Plane (/w/{id}/) → code-server:8080 (만 프록시)
                                         ↓
                               개발서버 :3000, :5000 → 접근 불가
```

**원인**: Workspace 컨테이너가 host 포트를 노출하지 않음 (K8s 호환 + 보안 목적)

---

## 해결 방향

code-server 내장 방식(방식 2)은 FE+BE 분리 시 Origin 문제가 있어 **별도 도메인 방식**이 필요.

```
목표: 3000-{workspace_id}.code-hub.com → workspace:3000
```

**두 가지 구현 옵션**:

| 방식 | 설명 | 적합성 |
|------|------|--------|
| **FRP 사이드카** | frpc + frps 터널링 | ⚠️ 과잉 기능 |
| **Pattern Router** | 자체 개발 HTTP 프록시 | ✅ 검토 중 |

---

## 방식 A: FRP 사이드카

### 아키텍처

```
┌─────────────────────────────────────┐
│  Pod                                │
│  ┌─────────────┐  ┌──────────────┐  │
│  │ code-server │  │ frp-sidecar  │  │
│  │ :8080       │  │ (frpc)       │  │
│  │ :3000 (dev) │──│              │  │
│  └─────────────┘  └──────────────┘  │
└─────────────────────────────────────┘
          ↓
    ┌───────────┐
    │ frp-server│
    └───────────┘
          ↓
    브라우저: https://3000-{workspace_id}.code-hub.com
```

### 장점

- 검증된 오픈소스 (GitHub 80k+ stars)
- TCP/UDP 프로토콜 지원
- NAT 뒤에서도 터널링 가능

### 단점

- **과잉 기능**: NAT traversal이 핵심인데, K8s 내부망에선 불필요
- **의존성 증가**: frpc 바이너리를 모든 Pod에 배포
- **설정 복잡도**: frpc 동적 설정, 포트 정리 로직 필요
- **새로운 공격 표면**: frps가 모든 트래픽 처리

---

## 방식 B: Pattern Router (자체 개발) ✅ 검토 중

### 아키텍처

```
┌─────────────────────────────────────────────────────┐
│  Pattern Router (Python HTTP Proxy)                 │
│                                                     │
│  3000-ws123.code-hub.com → ws-123 Pod:3000         │
│  5000-ws456.code-hub.com → ws-456 Pod:5000         │
│                                                     │
└─────────────────────────────────────────────────────┘
          ↑
    Wildcard DNS: *.code-hub.com → Pattern Router
          ↑
    브라우저: https://3000-{workspace_id}.code-hub.com
```

### 핵심 로직 (Python)

```python
from fastapi import FastAPI, Request
from fastapi.responses import Response
import httpx

app = FastAPI()

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str):
    # 3000-abc123.code-hub.com → port=3000, wsID=abc123
    port, ws_id = parse_host(request.headers["host"])

    # workspace Pod 주소 조회 (DB 또는 K8s API)
    upstream = await resolve_workspace(ws_id, port)
    if not upstream:
        return Response("Workspace not found", status_code=404)

    # Reverse Proxy
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=request.method,
            url=f"{upstream}/{path}",
            headers=request.headers,
            content=await request.body()
        )
        return Response(resp.content, status_code=resp.status_code, headers=dict(resp.headers))
```

### 왜 Python인가?

| 고려 사항 | Python | Go |
|----------|--------|-----|
| **성능** | 충분 (1000 연결 기준) | 더 빠름 |
| **세션 공유** | ✅ API 서버와 import로 공유 | ❌ 별도 구현 필요 |
| **개발 속도** | 빠름 | 중간 |
| **유지보수** | 단일 언어 스택 | 이중 언어 |

**결정: Python**

- API 서버와 세션/인증 로직 공유 가능 (단순 import)
- 1000 동시 연결 목표에서 Python 성능 충분
- 단일 언어 스택으로 유지보수 용이

### 프로토콜 지원

| 프로토콜 | 지원 | 구현 방법 |
|----------|------|----------|
| HTTP/1.1 | ✅ | 기본 지원 |
| HTTP/2 | ✅ | hypercorn ASGI 서버 |
| WebSocket | ✅ | FastAPI + websockets |
| SSE | ✅ | StreamingResponse |

### 예상 성능 (1000 동시 연결)

| 항목 | 예상치 |
|------|--------|
| **처리량** | 5,000~10,000 req/s |
| **p99 레이턴시** | 10~30ms (오버헤드) |
| **메모리** | 200~500MB |
| **CPU** | 2 vCPU |

**참고**: WebSocket은 연결 유지 비용만 (active 트래픽 없으면 거의 0)

### 장점

- **코드량 최소**: 200~500줄
- **세션 공유**: API 서버와 인증/세션 로직 import로 공유
- **제어권 100%**: 우리 요구사항에 맞게 최적화
- **사이드카 불필요**: Pod 변경 없음
- **단순 운영**: 단일 서비스
- **단일 언어 스택**: Python으로 통일

### 단점

- HTTP/WebSocket만 지원 (TCP/UDP 불가)
- 자체 유지보수 필요
- Go 대비 raw 성능은 낮음 (1000 연결에서는 무관)

---

## 비교 분석

| 항목 | FRP 사이드카 | Pattern Router (Python) |
|------|-------------|-------------------------|
| **구현 복잡도** | 중간 (설정) | 낮음 (200~500줄) |
| **의존성** | frp 바이너리 | FastAPI, httpx |
| **Pod 변경** | 사이드카 추가 필요 | 불필요 |
| **프로토콜** | HTTP/WS/TCP/UDP | HTTP/1.1, HTTP/2, WS, SSE |
| **NAT traversal** | ✅ 지원 | ❌ 불필요 |
| **세션 공유** | ❌ 별도 구현 | ✅ API 서버와 공유 |
| **유지보수** | 외부 프로젝트 | 자체 코드 (단일 언어) |
| **운영 복잡도** | frps + frpc 관리 | 단일 서비스 |
| **성능** | 높음 | 충분 (1000 연결) |

### 핵심 질문

> FRP의 핵심 가치는 "NAT 뒤에서 터널링"인데, K8s 내부망에서 이게 필요한가?

**답**: 아니오. K8s 내부에서는 Pod 간 직접 통신 가능.

---

## 권장 결론

**Pattern Router (방식 B, Python) 권장**

이유:
1. K8s 내부망에서 NAT traversal 불필요
2. HTTP/1.1, HTTP/2, WebSocket, SSE 지원 충분
3. API 서버와 세션/인증 로직 공유 가능 (Python)
4. 사이드카 없이 구현 가능
5. 1000 동시 연결 목표에서 Python 성능 충분
6. 단일 언어 스택으로 유지보수 용이

```
MVP/M2: code-server 내장 방식 사용
   ↓
M3 (K8s): Pattern Router (Python) 도입
   ↓
(미래) TCP/UDP 필요 시: FRP 재검토
```

---

## 구현 범위 (Pattern Router - Python)

### 필수

- [ ] Host 파싱: `{port}-{workspace_id}.domain.com`
- [ ] Workspace 조회: DB에서 Pod 주소 획득 (API 서버와 공유)
- [ ] HTTP Reverse Proxy (httpx AsyncClient)
- [ ] WebSocket 지원 (websockets)
- [ ] SSE 지원 (StreamingResponse)
- [ ] 세션/인증 로직 공유 (API 서버 모듈 import)

### 선택

- [ ] HTTP/2 지원 (hypercorn)
- [ ] 연결 풀링 (httpx 기본 지원)
- [ ] 헬스체크
- [ ] 메트릭 (Prometheus)
- [ ] Rate limiting

### 권장 스택

```
hypercorn      # ASGI 서버 (HTTP/2 지원)
FastAPI        # 웹 프레임워크
httpx          # async HTTP 클라이언트
websockets     # WebSocket 지원
```

---

## 의존성

- K8s 환경 (M3) 이후 구현이 자연스러움
- Local Docker에서는 code-server 내장 방식으로 대응

---

## References

- [FastAPI](https://fastapi.tiangolo.com/) - 웹 프레임워크
- [httpx](https://www.python-httpx.org/) - async HTTP 클라이언트
- [hypercorn](https://pgjones.gitlab.io/hypercorn/) - ASGI 서버 (HTTP/2 지원)
- [websockets](https://websockets.readthedocs.io/) - WebSocket 라이브러리
- [frp GitHub](https://github.com/fatedier/frp) - 참고용 (FRP 사이드카 방식)
- [code-server Proxy](https://coder.com/docs/code-server/guide) - M2 대응
