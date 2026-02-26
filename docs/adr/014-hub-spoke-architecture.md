# ADR-014: Hub-Spoke 아키텍처 도입

## 상태
Accepted (v0.3.0)

## 컨텍스트

### 문제 상황

현재 code-hub는 Control Plane(CP)과 Agent(Data Plane)가 **같은 네트워크**에 있다고 가정한다. CP가 Agent를 직접 HTTP 호출하는 구조:

```
CP (Coordinator) ──HTTP──▶ Agent (Docker Runtime)
   Observer.observe()         /api/v1/workspaces
   WC.start/stop/archive()
   GC.run_gc()
```

이 구조에서는 Agent가 NAT 뒤에 있거나 다른 네트워크에 있으면 CP가 Agent에 접근할 수 없다. K8s 환경으로 확장하려면 CP를 외부 접근 가능한 Hub으로 진화시키고, Agent가 Hub에 연결하는 방향 전환이 필요하다.

### 핵심 문제

**통신 방향 제약**: 현재 10개 HTTP 엔드포인트 전부 CP→Agent 방향. Agent가 NAT/방화벽 뒤에 있으면 CP가 도달 불가.

| 카테고리 | 엔드포인트 | 주기 |
|----------|-----------|------|
| 관측 | `POST /observe` | 1~15s 폴링 |
| 생명주기 | `POST /start`, `POST /stop` | on-demand |
| | `POST /archive`, `POST /restore` | on-demand |
| | `POST /cleanup`, `POST /provision` | on-demand |
| 정보 | `GET /upstream` | on-demand |
| GC | `POST /gc` | 4h |
| 상태 | `GET /health` | on-demand |
| 관측 | `GET /report` | 1s |

**단일 Agent 가정**: 현재 CP는 Agent 1대만 알고 있다 (`agent_client.py`의 `base_url` 하나). 여러 Agent/클러스터 지원 불가.

**MinIO 접근 경로**: CP와 Agent가 같은 네트워크이므로 MinIO 위치가 크게 문제되지 않았다. 분산 환경에서는 대역폭 비용이 쟁점.

### Coder와의 차이

Coder는 유사한 CDE 플랫폼이지만 code-hub과 근본적으로 다른 접근:

| 특성 | Coder | code-hub |
|------|-------|----------|
| 프로비저닝 | Terraform (30~60s 오버헤드) | 직접 API 호출 (즉시) |
| 상태 관리 | Terraform state file | Ordered State Machine + DB |
| S3 Archive/Restore | ❌ 없음 | ✅ 핵심 기능 |
| GC (S3 orphan 정리) | ❌ 없음 | ✅ 자동 |
| Clone from Archive | ❌ 없음 | ✅ 빈 아카이브 생성 |

code-hub의 S3 archive lifecycle은 Coder에 없는 고유 기능이므로, Hub-Spoke 설계 시에도 이 lifecycle을 보존해야 한다.

## 결정

**Hub-Spoke 아키텍처를 도입한다.** CP를 Hub으로 진화시키고, Agent가 Hub에 능동적으로 연결하는 구조로 전환한다.

### 핵심 원칙

1. **Agent는 항상 NAT 뒤에 있다고 가정** — 모든 연결은 Agent→Hub 방향
2. **FRP 터널을 기본 연결 수단으로 사용** — Agent(frpc)가 Hub(frps)에 연결
3. **MinIO는 Agent(Data Plane) 쪽에 배치** — CP는 `archive_key` 메타데이터만 관리
4. **Hub화 먼저, K8s runtime 나중에** — 구현 순서 분리
5. **CP는 게이트키퍼, Agent는 Data Plane Gateway** — 역할 명확 분리 (아래 상세)

### 역할 분리: 게이트키퍼 vs Data Plane Gateway

Hub-Spoke에서 CP와 Agent의 역할은 근본적으로 다르다.

**CP (Hub) = 게이트키퍼**

CP는 리소스를 소유하지 않는다. 리소스에 대한 **접근 권한을 관리**한다.

| 역할 | 설명 |
|------|------|
| 인증 | 사용자가 누구인지 확인 (로그인, 세션) |
| 인가 | 이 사용자가 이 워크스페이스에 접근 가능한지 확인 |
| 라우팅 | 워크스페이스가 어떤 Agent에 있는지 결정 |
| 상태 조율 | Reconciler로 desired ↔ actual 수렴 |
| 메타데이터 | workspace, agent, user 정보를 DB에 저장 |
| 트래픽 포워딩 | 인증된 사용자 트래픽을 Agent로 전달 |

CP에 필요한 인프라: PostgreSQL + Redis. 가벼운 Pod으로 충분하다.

**Agent (DP) = Data Plane Gateway**

Agent는 Data Plane으로 들어오는 **모든 트래픽의 유일한 진입점**이다. Runtime API뿐 아니라 사용자의 workspace 접속 트래픽도 Agent를 통해서만 DP에 도달할 수 있다.

| 역할 | 설명 |
|------|------|
| 컴퓨트 | 워크스페이스 컨테이너 실행 (Docker/K8s) |
| 스토리지 | 볼륨 관리, S3 archive/restore |
| 실행 | Hub의 명령(start, stop, archive 등)을 실제 수행 |
| **Workspace 프록시** | **CP에서 전달받은 HTTP/WebSocket 트래픽을 로컬 workspace 컨테이너로 중계** |
| 로컬 관리 | 자체 리소스 모니터링, 감사 로그, 연결 관리 |

Agent에 필요한 인프라: Container Runtime + MinIO + Docker Proxy. 실제 워크로드가 여기서 실행된다.

**왜 Agent가 프록시를 담당하는가**: NAT 격리 환경에서 CP는 workspace 컨테이너에 직접 도달할 수 없다. Agent는 workspace 컨테이너와 같은 네트워크(dp-net)에 있으므로 로컬 프록시가 가능하다. 또한 Agent는 컨테이너의 실제 상태를 즉시 알 수 있어, CP가 DB 기반으로 판단하는 것보다 더 정확한 프록시 응답이 가능하다.

**비유**: K8s에서 API Server(인증/스케줄링) vs Node(실제 Pod 실행)의 관계와 같다. Hub이 리소스를 "소유"하는 것이 아니라, Agent 소유자가 리소스를 Hub에 "등록하여 관리를 위임"하는 구조다.

### Agent 자율성 (Agent Dashboard)

Agent는 단순한 명령 실행기가 아니라, **자체 관리 기능을 가진 독립 서비스**다. Agent 소유자는 자신의 인프라에서 무슨 일이 일어나는지 투명하게 볼 수 있어야 한다.

**Agent Dashboard** (`http://agent-local:8081/dashboard`):

```
Agent Dashboard
├── 연결 상태
│   ├── Hub 연결: hub.example.com ✅ Connected
│   ├── FRP 터널: ✅ Active (latency: 3ms)
│   └── [연결 해제] [재연결]
│
├── 감사 로그 (Hub이 보낸 명령)
│   ├── 14:30:22  POST /observe           → 200
│   ├── 14:30:21  POST /start ws-abc123   → 200
│   └── 필터: 시간, 명령 타입, 워크스페이스
│
├── 워크스페이스 현황
│   ├── ws-abc123: RUNNING (CPU 12%, MEM 256MB)
│   ├── ws-def456: STANDBY (volume 2.1GB)
│   └── 총: 2 실행 / 1 대기 / 15GB 사용
│
├── 리소스 사용량
│   ├── CPU / Memory / Disk
│   └── MinIO 아카이브 사용량
│
└── 설정
    ├── Hub 등록 정보 (agent_id, 등록일)
    ├── 리소스 제한 (max workspaces, max CPU/MEM)
    └── [Hub 연결 해제] [Agent 초기화]
```

**Agent 소유권 원칙**:

1. **투명성**: Hub이 Agent에 보낸 모든 명령은 감사 로그에 기록된다
2. **제어권**: Agent 소유자는 언제든 Hub 연결을 해제할 수 있다
3. **리소스 제한**: Agent 소유자가 최대 워크스페이스 수, CPU/메모리 상한을 설정한다
4. **독립 실행**: Hub이 다운되어도 이미 실행 중인 워크스페이스는 계속 동작한다

### 아키텍처 구조

```
┌──────────────────────────────────────────────────────────┐
│  OCI K8s Cluster (Hub)                                   │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐ │
│  │ CP (FastAPI) │  │ PostgreSQL  │  │ Redis            │ │
│  │ - API Server │  │ - workspaces│  │ - Pub/Sub        │ │
│  │ - Coordinator│  │ - agents    │  │ - SSE events     │ │
│  │ - Fwd Proxy  │  │ - routing   │  │                  │ │
│  └──────┬───────┘  └─────────────┘  └──────────────────┘ │
│         │                                                │
│  ┌──────┴───────┐                                        │
│  │ frps         │ ◀──── FRP tunnel ──────────────┐       │
│  │ (frp server) │                                │       │
│  └──────────────┘                                │       │
└──────────────────────────────────────────────────┼───────┘
                                                   │
              ┌────────────────────────────────────┘
              │
┌─────────────┼────────────────────────────────────────────┐
│  Agent Site (NAT 뒤)                                     │
│             │                                            │
│  ┌──────────┴──┐  ┌──────────────┐  ┌──────────────────┐│
│  │ frpc        │  │ Agent (DP GW)│  │ MinIO (S3)       ││
│  │ (frp client)│  │ - Runtime    │  │ - archive/restore││
│  │             │  │ - API        │  │ - volume backup  ││
│  └─────────────┘  │ - Storage    │  └──────────────────┘│
│                   │ - WS Proxy   │                       │
│                   │ - Dashboard  │                       │
│                   │ - Audit Log  │                       │
│                   └──────────────┘                       │
│                                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │ Container Runtime (Docker / K8s)                     ││
│  │ - Workspace containers                               ││
│  │ - Storage Jobs                                       ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

### 통신 흐름 변경

현재 (CP→Agent 직접):
```
CP ──HTTP──▶ Agent
```

변경 후 (FRP 터널 경유):
```
CP ──HTTP──▶ frps ──▶ frpc ──▶ Agent API    (제어 명령: Hub→Agent via tunnel)
User ──▶ CP ──▶ frps ──▶ frpc ──▶ Agent ──▶ Workspace Container
                                             (Workspace 프록시: Agent가 로컬 프록시)
```

**FRP가 제공하는 것**:
- Agent→Hub 방향 연결이므로 NAT/방화벽 통과
- TCP 프록시: CP→Agent API 통신을 터널링
- 연결 다중화: 하나의 FRP 연결로 제어 + 프록시 트래픽 동시 처리

**Workspace 프록시 흐름 (Double Proxy)**:
1. User → CP: 인증/인가 확인 후 트래픽을 Agent로 포워딩
2. CP → frps → frpc → Agent: FRP 터널 경유
3. Agent → Workspace Container: Agent가 dp-net 내부에서 직접 프록시

CP는 인증만 담당하고, 실제 workspace 컨테이너 프록시는 Agent가 수행한다. CP가 workspace 컨테이너 IP를 알 필요가 없다.

### Agent Registry

CP에 `agents` 테이블을 추가하여 멀티 Agent를 관리:

```sql
CREATE TABLE agents (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE,       -- 사람이 읽을 수 있는 이름
    status VARCHAR NOT NULL,            -- ONLINE / OFFLINE / DRAINING
    capabilities JSONB,                 -- {"runtimes": ["docker", "k8s"], "storage": ["s3"]}
    frp_proxy_name VARCHAR,             -- FRP에서의 프록시 이름
    last_heartbeat_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL,
    metadata JSONB                      -- 추가 정보 (region, labels 등)
);
```

기존 `workspaces` 테이블에 `agent_id` FK 추가:
```sql
ALTER TABLE workspaces ADD COLUMN agent_id UUID REFERENCES agents(id);
```

### MinIO를 Agent 쪽에 배치하는 이유

CP가 MinIO에 직접 접근하지 않음이 코드 분석에서 확인됨:
- CP는 `archive_key` 문자열만 DB에 저장
- 실제 S3 작업(archive/restore/gc)은 전부 Agent의 `StorageManager` + `JobRunner`가 수행
- FRP 터널을 통한 S3 트래픽은 대역폭 낭비 (볼륨 데이터는 수 GB)

따라서 MinIO를 Agent 로컬에 두고, Agent가 직접 S3 I/O를 수행하는 것이 자연스럽다.

## 구현 순서

### Phase 0: CP/DP 네트워크 분리 ✅ 완료
1. docker-compose 네트워크 분리 (`cp-net`, `dp-net`, `tunnel-net`)
2. FRP 터널 구성 (frps/frpc 서비스, Agent API 터널)
3. CP에서 불필요한 인프라 의존 제거 (`depends_on: agent` 제거)
4. 네트워크 격리 검증 E2E 15/15 통과

### Phase 0.5: Agent Workspace Proxy
1. Agent에 Workspace 프록시 엔드포인트 추가 (`/w/{ws_id}/{path}`)
2. HTTP reverse proxy + WebSocket relay 구현
3. CP의 `get_upstream()` 변경: workspace container IP → Agent(FRP) 주소 반환
4. E2E에 code-server 프록시 접속 테스트 추가

### Phase 1: Agent 등록 시스템
1. `agents` 테이블 + Alembic migration
2. Agent 등록/해제 API (`POST /api/v1/agents/register`, `DELETE /api/v1/agents/{id}`)
3. Agent 시작 시 Hub에 자동 등록 (HUB_ENDPOINT + 등록 토큰)
4. Agent heartbeat (`POST /api/v1/agents/{id}/heartbeat`)
5. `agent_client.py` 수정: 정적 URL → DB에서 Agent URL 조회
6. Observer/WC가 등록된 agent별로 observe/control
7. `workspace → agent` 라우팅 (workspace 생성 시 agent 지정)

### Phase 2: Agent Dashboard + 감사 로그
1. Agent 감사 로그 미들웨어 (Hub 명령 기록)
2. Agent Dashboard UI (연결 상태, 감사 로그, 워크스페이스 현황, 리소스)
3. Agent 리소스 제한 설정 (max workspaces, max CPU/MEM)
4. Hub 연결 관리 (연결 해제/재연결)

### Phase 3: K8s Runtime 추가
1. `runtimes/models.py`로 공유 타입 추출 (OperationResult, JobType 등)
2. `runtimes/kubernetes/` 구현 (instance.py, volume.py, job.py, storage.py)
3. `protocols.py`에서 Docker-specific import 제거
4. Agent config에 runtime 선택 (`runtime: docker | kubernetes`)

### Phase 4: Helm Chart + 배포
1. CP Helm chart (FastAPI + PostgreSQL + Redis)
2. Agent Helm chart (Agent + frpc + MinIO)
3. OCI K8s 배포 및 E2E 테스트

## 장점

### NAT/방화벽 투과
Agent가 어디에 있든 Hub에 연결 가능. 기업 네트워크, 홈 네트워크, 클라우드 VPC 모두 지원.

### 멀티 Agent 지원
여러 Agent를 하나의 Hub에 등록하여 중앙 관리. Agent별 capability로 워크스페이스 배치 최적화.

### 대역폭 효율
MinIO가 Agent 로컬에 있으므로 archive/restore 시 FRP 터널 대역폭을 소비하지 않음. 수 GB 볼륨 데이터가 로컬에서 처리됨.

### 점진적 전환
기존 Docker Agent는 그대로 유지하면서 K8s Agent를 추가 등록 가능. 하나의 Hub에서 혼합 운영.

### 기존 아키텍처 보존
Ordered State Machine, Level-Triggered Reconciliation, Single Writer Principle 등 핵심 계약은 변경 없음. Agent의 API 인터페이스도 동일 — 연결 경로만 FRP로 변경.

### Agent 자율성
Agent 소유자가 자신의 인프라를 직접 모니터링하고 제어할 수 있음. 감사 로그로 Hub의 모든 명령을 투명하게 확인. 리소스 제한으로 과도한 사용 방지. Hub 장애 시에도 기존 워크스페이스 계속 동작.

## 단점

### FRP 단일 장애점
frps가 다운되면 모든 Agent 연결 끊김. Hub K8s에서 frps의 고가용성(replica, health check) 확보 필요.

### 추가 인프라 복잡도
FRP 서버/클라이언트 설정, 인증서 관리, 터널 모니터링이 추가됨.

### Agent 등록 관리
Agent 추가/제거, heartbeat 감시, OFFLINE 감지 등 새로운 운영 부담. 기존 단일 Agent 구조보다 복잡.

### 네트워크 지연 (Double Proxy)
Workspace 트래픽은 CP(인증) → FRP 터널 → Agent(프록시) → Container로 이중 프록시를 거친다. 같은 노드 내 프록시 hop은 sub-ms 수준이므로 실사용 체감은 미미하지만, 물리적 거리(User↔Hub↔Agent)에 따른 지연은 불가피하다. Observer 폴링도 터널 지연을 포함하게 됨.

### Agent Dashboard 개발 비용
Agent가 headless API 서버에서 자체 UI를 가진 서비스로 확장됨. 프론트엔드 개발 및 유지보수 부담 추가. 다만 Agent Dashboard는 로컬 관리용이므로 CP Dashboard 대비 단순한 UI로 충분.

## 대안 (선택하지 않음)

### HTTP 방향 역전 (Agent→CP polling)

Agent가 CP를 주기적으로 폴링하여 명령을 가져오는 방식.

```
Agent ──poll──▶ CP: "할 일 있나요?"
CP ──response──▶ Agent: "workspace-1을 start 해주세요"
```

미선택 이유: 모든 CP→Agent 호출을 command queue 패턴으로 재작성해야 함. Observer의 실시간 관측(1s)을 polling으로 구현하면 지연이 심함. Workspace 프록시(code-server 접속)를 별도로 해결해야 함.

### WebSocket 양방향 채널

Agent가 CP에 WebSocket으로 연결하고, 양방향 메시지를 교환.

미선택 이유: 기존 REST API 10개를 WebSocket 메시지 프로토콜로 재작성해야 함. 기존 HTTP 기반 agent_client.py를 버려야 함. Workspace 프록시를 WebSocket 위에 별도 구현해야 함. 구현 비용이 FRP 대비 크게 높음.

### gRPC 양방향 스트리밍

Agent→CP gRPC 연결로 양방향 통신.

미선택 이유: 기존 FastAPI REST API와 공존이 어려움. Agent API 전체를 gRPC로 재작성해야 함. Workspace 프록시 문제는 여전히 별도 해결 필요. Protobuf 스키마 관리 오버헤드.

### VPN (Tailscale/WireGuard)

Agent와 Hub를 VPN으로 연결하여 같은 네트워크처럼 사용.

미선택 이유: 이미 OCI 클러스터에 FRP가 운영 중이므로 추가 인프라 불필요. VPN은 네트워크 레벨 연결이라 세밀한 프록시 제어가 어려움. Agent별 독립적인 터널 관리가 FRP가 더 적합.

### FRP가 최적인 이유

| 요구사항 | FRP | HTTP 역전 | WebSocket | gRPC | VPN |
|----------|-----|-----------|-----------|------|-----|
| NAT 투과 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 기존 REST API 유지 | ✅ | ❌ 재작성 | ❌ 재작성 | ❌ 재작성 | ✅ |
| Workspace 프록시 | ✅ 터널 경유 | ❌ 별도 | ❌ 별도 | ❌ 별도 | ✅ |
| 이미 운영 중 | ✅ OCI frps | - | - | - | - |
| 구현 비용 | 낮음 | 높음 | 높음 | 매우 높음 | 중간 |

**FRP의 결정적 장점**: 기존 REST API를 변경 없이 터널링. FRP가 CP↔Agent 경로를 제공하고, Agent가 로컬 workspace 프록시를 수행. 이미 OCI에 frps가 운영 중.

> **Note**: Workspace 프록시는 FRP가 직접 라우팅하는 것이 아니라, FRP 터널을 경유하여 Agent의 프록시 엔드포인트에 도달하는 구조다 (User → CP → FRP → Agent → Container).

## 관련 문서

| 문서 | 설명 |
|------|------|
| [ADR-010](./010-package-separation.md) | 패키지 분리 아키텍처 |
| [ADR-013](./013-openapi-schema-ssot.md) | OpenAPI 스키마 SSOT |
| [architecture/overview.md](../architecture/overview.md) | 현재 아키텍처 다이어그램 |
| [spec/05-data-plane.md](../spec/05-data-plane.md) | Data Plane 상세 스펙 |
