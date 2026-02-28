# Observability Architecture

> Hub-Spoke 구조에서의 메트릭 수집, 대시보드, Activity Recording 설계

**ADR**: [ADR-014](../adr/014-hub-spoke-architecture.md)
**선행**: [hub-spoke.md](./hub-spoke.md)

---

## 1. 배경: 왜 재설계가 필요한가

### 아키텍처 변경

```
Before (모노리스):
  User → CP (직접 프록시 → workspace container)
  CP가 모든 메트릭을 수집. Prometheus가 CP만 scrape하면 충분.

After (Hub-Spoke):
  User → CP (forward proxy) → FRP tunnel → Agent (reverse proxy) → workspace
  프록시 트래픽이 Agent(DP)에서 종료됨. CP는 forward만 담당.
```

### 현재 문제

| # | 문제 | 영향 |
|---|------|------|
| 1 | **유령 메트릭**: CP에 WS 메트릭 3개 정의 but 사용처 없음 | Grafana 패널 nodata |
| 2 | **Agent 프록시 블라인드**: proxy.py 303줄인데 메트릭 0개 | 프록시 장애 감지 불가 |
| 3 | **수집 경로**: Prometheus가 dp-net 직접 접근 (prod 불가) | 멀티 DP 확장 불가 |
| 4 | **Activity 부정확**: CP가 forward 시 1회 기록 | 장시간 WS 세션 idle 오판 |
| 5 | **대시보드 낙후**: api.json WS 패널 빈칸, agent.json 최소 | 운영 가시성 부족 |

---

## 2. 설계 원칙

1. **역할 기반 소유권**: 메트릭은 실제로 해당 작업을 수행하는 컴포넌트가 소유
2. **저 카디널리티**: Prometheus 라벨에 `workspace_id`, URL path, user ID 등 고카디널리티 값 금지
3. **Push 기반 수집**: NAT 뒤 DP는 Prometheus Agent Mode로 outbound push (remote_write)
4. **DP 경량 유지**: DP에 Grafana/full Prometheus 설치하지 않음. 수집기만 배치 (~30MB)
5. **장애 구간 분리**: 터널 vs 프록시 vs workspace 구간을 메트릭으로 구분 가능해야 함

---

## 3. 메트릭 수집 아키텍처

### 전체 구조

```
┌─── cp-net ──────────────────────┐    ┌─── dp-net (DP-A) ──────────────┐
│                                  │    │                                 │
│  ┌──────────┐  scrape CP only    │    │  ┌───────────┐ scrape ┌──────┐ │
│  │Prometheus├──►CP:8000/metrics  │    │  │Prom Agent ├───────►│Agent │ │
│  │(central) │                    │    │  │(agent     │        │:8081 │ │
│  │          │◄── remote_write ───│────│──│ mode)     │        │      │ │
│  │          │    from each DP    │    │  │ ~30MB     │        │      │ │
│  └────┬─────┘                    │    │  └───────────┘        └──────┘ │
│       │                          │    │       │ outbound only           │
│  ┌────▼─────┐                    │    │       ▼                         │
│  │ Grafana  │                    │    │  FRP tunnel → cp-net            │
│  └──────────┘                    │    │                                 │
│                                  │    └─────────────────────────────────┘
└──────────────────────────────────┘
                                        ┌─── dp-net (DP-B) ──────────────┐
                                   ────►│  동일 구조 (Agent + Prom Agent) │
                                        └─────────────────────────────────┘
                                        ┌─── dp-net (DP-C) ──────────────┐
                                   ────►│  동일 구조                       │
                                        └─────────────────────────────────┘

※ CP config 변경 없이 DP 추가 가능 (Agent가 remote_write로 push)
```

### Prometheus Agent Mode 선택 근거

| 방식 | DP 추가 컴포넌트 | DP 추가 시 CP 변경 | staleness | `up` 메트릭 |
|------|-----------------|-------------------|-----------|------------|
| FRP로 /metrics 노출 (pull) | 없음 | frpc.toml + prometheus.yml | 자동 | ✅ |
| Agent가 직접 remote_write | 없음 | 없음 | ❌ 수동 | ❌ |
| **Prometheus Agent Mode** | **Prom binary (~30MB)** | **없음** | **자동** | **✅** |
| Per-DP full Prometheus | Prometheus + 스토리지 | 없음 | 자동 | ✅ |

Prometheus Agent Mode 선택 이유:
- **공식 권고**: Prometheus docs에서 NAT 뒤 long-running service에 대해 Agent Mode + remote_write 권장
- **TSDB 없음**: 저장/알림/룰 비활성화, scrape→forward만 수행 → ~30MB 메모리
- **자동 staleness**: target 사라지면 StaleNaN 마커 자동 전송
- **자동 `up`/`job`/`instance`**: scrape 기반이므로 표준 라벨 자동 부여
- **CP config 불변**: remote_write receiver만 활성화하면 DP 개수 무관

### Central Prometheus 설정

```yaml
# prometheus.yml (central, cp-net)
global:
  scrape_interval: 15s

# CP 메트릭 (기존)
scrape_configs:
  - job_name: 'codehub'
    static_configs:
      - targets: ['control-plane:8000']

# Agent 메트릭은 remote_write로 수신 (scrape_config 불필요)

# 시작 플래그에 추가:
#   --web.enable-remote-write-receiver
```

### DP Prometheus Agent 설정

```yaml
# agent.yml (각 DP)
global:
  scrape_interval: 15s

external_labels:
  agent_id: "dp-a"    # DP별 고유 식별자
  site: "seoul"         # 선택: 지역 라벨

scrape_configs:
  - job_name: 'agent'
    static_configs:
      - targets: ['agent:8081']

remote_write:
  - url: "http://frps-endpoint:9090/api/v1/write"
    # FRP 터널을 통해 central Prometheus에 push
```

### docker-compose 추가 (각 DP)

```yaml
prom-agent:
  image: prom/prometheus:v3.9.1
  container_name: codehub-prom-agent
  command:
    - '--enable-feature=agent'
    - '--config.file=/etc/prometheus/agent.yml'
  volumes:
    - ./monitoring/agent.yml:/etc/prometheus/agent.yml:ro
  mem_limit: 64m
  cpus: 0.25
  networks:
    - dp-net
```

---

## 4. 메트릭 소유권

### 원칙

> 메트릭은 실제로 해당 작업을 수행하는 컴포넌트가 소유한다.

| 작업 | 수행 주체 | 메트릭 소유 |
|------|----------|------------|
| HTTP API 처리 | CP | CP |
| SSE 스트리밍 | CP | CP |
| DB/Redis 풀 관리 | CP | CP |
| Coordinator 조율 | CP | CP |
| TTL/이벤트 관리 | CP | CP |
| **HTTP/WS 프록시** | **Agent** | **Agent** |
| Docker 작업 | Agent | Agent |
| S3 작업 | Agent | Agent |
| Activity 기록 | Agent (수집) → CP (저장) | 양쪽 |

### CP 메트릭 (27개)

**변경 사항**: 유령 WS 메트릭 3개 제거, DP health 메트릭 1개 추가

#### 제거

| 메트릭 | 이유 |
|--------|------|
| `codehub_ws_active_connections` | Agent로 이동 (CP는 forward만 함) |
| `codehub_ws_message_latency_seconds` | Agent로 이동 |
| `codehub_ws_errors_total` | Agent로 이동 |

#### 추가

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `codehub_dp_last_seen_seconds` | Gauge | `agent_id` | Agent activity POST 수신 시각. `time() - value`로 DP 생존 확인 |

#### 유지 (기존 26개)

<details>
<summary>전체 목록</summary>

**PostgreSQL Pool (5)**
- `codehub_postgresql_connected_workers` Gauge
- `codehub_postgresql_pool_idle` Gauge
- `codehub_postgresql_pool_active` Gauge
- `codehub_postgresql_pool_total` Gauge
- `codehub_postgresql_pool_overflow` Gauge

**Redis Pool (4)**
- `codehub_redis_connected_workers` Gauge
- `codehub_redis_pool_idle` Gauge
- `codehub_redis_pool_active` Gauge
- `codehub_redis_pool_total` Gauge

**Coordinator (4)**
- `codehub_coordinator_reconcile_total` Counter `[coordinator]`
- `codehub_coordinator_reconcile_duration_seconds` Histogram `[coordinator]`
- `codehub_coordinator_is_leader` Gauge `[coordinator]`
- `codehub_coordinator_wake_received_total` Counter `[coordinator]`

**Observer (6)** *(deprecated 포함)*
- `codehub_observer_workspaces` Gauge *(deprecated)*
- `codehub_observer_containers` Gauge *(deprecated)*
- `codehub_observer_volumes` Gauge *(deprecated)*
- `codehub_observer_archives` Gauge *(deprecated)*
- `codehub_observer_stage_duration_seconds` Histogram `[stage]`
- `codehub_observer_observe_duration_seconds` Histogram

**Workspace (3)**
- `codehub_workspaces` Gauge `[state]`
- `codehub_runtime_observe_duration_seconds` Histogram
- `codehub_observer_api_duration_seconds` Histogram `[api]`

**WC Controller (4)**
- `codehub_wc_stage_duration_seconds` Histogram `[stage]`
- `codehub_wc_execute_duration_seconds` Histogram
- `codehub_wc_operation_duration_seconds` Histogram `[operation]`
- `codehub_wc_cas_failures_total` Counter

**TTL (2)**
- `codehub_ttl_expirations_total` Counter `[transition]`
- `codehub_ttl_sync_duration_seconds` Histogram `[target]`

**Event/SSE (9)**
- `codehub_event_notify_received_total` Counter `[channel]`
- `codehub_event_sse_published_total` Counter
- `codehub_event_wake_published_total` Counter `[target]`
- `codehub_event_queue_size` Gauge
- `codehub_event_errors_total` Counter `[operation]`
- `codehub_event_listener_is_leader` Gauge
- `codehub_sse_active_connections` Gauge
- `codehub_sse_messages_total` Counter `[event_type]`
- `codehub_sse_errors_total` Counter `[error_type]`
- `codehub_sse_dedup_skipped_total` Counter

**Circuit Breaker (3)**
- `codehub_circuit_breaker_state` Gauge `[circuit]`
- `codehub_circuit_breaker_calls_total` Counter `[circuit, result]`
- `codehub_circuit_breaker_rejections_total` Counter `[circuit]`

**External Call (1)**
- `codehub_external_call_errors_total` Counter `[error_type]`

**HTTP API (2)**
- `codehub_http_requests_total` Counter `[method, endpoint, status]`
- `codehub_http_request_duration_seconds` Histogram `[method, endpoint]`

</details>

### Agent 메트릭 (19개: 기존 7 + 신규 12)

#### 기존 (7)

| 메트릭 | 타입 | 라벨 |
|--------|------|------|
| `codehub_agent_docker_duration_seconds` | Histogram | `operation` |
| `codehub_agent_docker_errors_total` | Counter | `operation, error_type` |
| `codehub_agent_s3_duration_seconds` | Histogram | `operation` |
| `codehub_agent_s3_bytes_total` | Counter | `direction` |
| `codehub_agent_s3_errors_total` | Counter | `operation, error_type` |
| `codehub_agent_containers_total` | Gauge | — |
| `codehub_agent_volumes_total` | Gauge | — |

#### 신규: Proxy HTTP (5)

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `codehub_agent_proxy_requests_total` | Counter | `method, status_class` | HTTP 요청 수. status_class = 2xx/3xx/4xx/5xx |
| `codehub_agent_proxy_request_duration_seconds` | Histogram | `method` | 전체 요청 처리 시간 (upstream 응답까지) |
| `codehub_agent_proxy_in_flight` | Gauge | — | 현재 처리 중인 요청 수 |
| `codehub_agent_proxy_upstream_errors_total` | Counter | `error_type` | upstream 에러 (timeout, refused, reset) |
| `codehub_agent_proxy_bytes_total` | Counter | `direction` | in/out 트래픽 볼륨 |

#### 신규: Proxy WebSocket (5)

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `codehub_agent_proxy_ws_active` | Gauge | — | 현재 활성 WS 연결 수 |
| `codehub_agent_proxy_ws_connect_total` | Counter | — | WS 연결 수립 횟수 |
| `codehub_agent_proxy_ws_close_total` | Counter | `close_code_class` | WS 종료. normal/going_away/error |
| `codehub_agent_proxy_ws_messages_total` | Counter | `direction` | upstream/downstream 메시지 수 |
| `codehub_agent_proxy_ws_errors_total` | Counter | `error_type` | WS relay 에러 |

#### 신규: Proxy 진단 (2)

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `codehub_agent_proxy_upstream_connect_duration_seconds` | Histogram | — | Agent→workspace 연결 시간. 터널 vs upstream 구분용 |
| `codehub_agent_proxy_ws_session_duration_seconds` | Histogram | — | WS 세션 수명 분포 |

### 라벨 설계

| 라벨 | 값 예시 | 카디널리티 | 설명 |
|------|---------|-----------|------|
| `method` | GET, POST, PATCH, DELETE | 4 | HTTP 메서드 |
| `status_class` | 2xx, 3xx, 4xx, 5xx | 4 | 상태 코드 클래스 |
| `direction` | upstream, downstream / in, out | 2 | 방향 |
| `error_type` | timeout, refused, reset, ... | ~5 | 에러 분류 |
| `close_code_class` | normal, going_away, error | 3 | WS close 분류 |
| `operation` | create, start, stop, ... | ~8 | Docker/S3 작업 |
| `agent_id` | dp-a, dp-b | N (DP 수) | Prom Agent `external_labels`로 부여 |

**금지 라벨**: `workspace_id`, `user_id`, `container_id`, URL path, 에러 메시지 원문

---

## 5. Activity Recording

### 현재 문제

```
CP router.py → _activity_buffer.record(workspace_id)  # forward 시 1회
→ flush → Redis ZADD → TTL Manager 읽음

문제: WS 연결이 3시간 유지되어도 activity = 3시간 전 timestamp.
     TTL Manager가 "idle"로 오판 → 작업 중인데 standby 전환 위험.
```

### 변경 설계

```
Agent proxy.py:
  HTTP 요청마다 → local activity buffer에 workspace_id + timestamp 기록
  WS 트래픽마다 → 동일 (메시지 수신 시)

Agent background task (주기: 30초):
  buffer flush → POST /internal/activity → FRP tunnel → CP
  payload: { "activities": [{ "workspace_id": "...", "last_seen": 1234567890 }, ...] }

CP /internal/activity handler:
  각 workspace에 대해 Redis ZADD (score = last_seen timestamp)
  codehub_dp_last_seen_seconds{agent_id} 갱신

TTL Manager:
  기존과 동일 — Redis에서 idle workspace 판단
```

### 장애 모드

| 상황 | 동작 | 정책 |
|------|------|------|
| 터널 정상 | Agent → CP batch POST 성공 | 정상 경로 |
| 터널 일시 끊김 | Agent 로컬 버퍼 유지, 재시도 | 버퍼 상한 설정 (1000건) |
| 터널 장기 끊김 | CP에 activity 안 감 | **fail-open**: TTL 연장 (보수적) |
| Agent 크래시 | 마지막 POST 이후 activity 유실 | TTL 기본 타임아웃으로 자연 처리 |

**멱등성**: Redis ZADD는 score = timestamp로 동작. 같은 workspace에 대해 중복 POST가 와도 최신 timestamp만 유지. 순서 무관.

---

## 6. 대시보드 설계

### 구조: 4개 → 3개

```
기존 (4개):                          변경 (3개):
  control-plane.json  ──┐
  infrastructure.json ──┼──► ② Control Plane
  api.json ─────────────┤
      (WS 패널 제거)    ├──► ① Overview (신규)
      (HTTP/SSE → ②)   │
  agent.json ───────────┴──► ③ Data Plane (확장)
```

### ① Overview — "전체 상태 한눈에" (10 panels)

운영자가 매일 보는 화면. 모든 문제의 시작점.

| Row | 패널 | 메트릭 | 목적 |
|-----|------|--------|------|
| Health | CP up/down | `up{job="codehub"}` | CP 생존 |
| | DP up/down (per DP) | `up{job="agent"}` | 각 DP 생존 |
| | DP last seen | `time() - codehub_dp_last_seen_seconds` | remote_write 끊김 감지 |
| | Workspace by state | `codehub_workspaces` | 전체 workspace 분포 |
| Traffic | HTTP req/s | `rate(codehub_agent_proxy_requests_total[5m])` | 트래픽 볼륨 |
| | WS active | `codehub_agent_proxy_ws_active` | 실시간 WS 연결 |
| Latency | Proxy p50/p99 | `histogram_quantile(0.99, ...)` | 사용자 체감 성능 |
| Errors | Error rate | CP HTTP 5xx + Agent upstream errors | 전체 에러 비율 |
| | Worst DPs | `topk(3, rate(upstream_errors[5m])) by (agent_id)` | 문제 DP 즉시 식별 |

### ② Control Plane — "CP 내부 상세" (~14 panels)

CP 관련 문제 drill-down.

| Section | 패널 | 핵심 메트릭 |
|---------|------|------------|
| API | HTTP rate by endpoint | `codehub_http_requests_total` |
| | HTTP latency by endpoint | `codehub_http_request_duration_seconds` |
| | SSE active + msg/s | `codehub_sse_active_connections`, `codehub_sse_messages_total` |
| Coordinator | Leader status (OB, WC) | `codehub_coordinator_is_leader` |
| | Reconcile rate | `codehub_coordinator_reconcile_total` |
| | Reconcile duration | `codehub_coordinator_reconcile_duration_seconds` |
| Lifecycle | WC operation duration | `codehub_wc_operation_duration_seconds` |
| | TTL expirations | `codehub_ttl_expirations_total` |
| | CAS failures | `codehub_wc_cas_failures_total` |
| | Event queue size | `codehub_event_queue_size` |
| Infra | PG pool (active/idle/overflow) | `codehub_postgresql_pool_*` |
| | Redis pool (active/idle) | `codehub_redis_pool_*` |

### ③ Data Plane — "DP 내부 상세" (~18 panels)

DP selector 드롭다운으로 특정 DP 선택 가능. `agent_id` 라벨 기반 필터.

| Section | 패널 | 핵심 메트릭 |
|---------|------|------------|
| Proxy HTTP | Request rate by status | `codehub_agent_proxy_requests_total` |
| | Latency p50/p99 | `codehub_agent_proxy_request_duration_seconds` |
| | In-flight | `codehub_agent_proxy_in_flight` |
| | Upstream errors | `codehub_agent_proxy_upstream_errors_total` |
| | Bytes in/out | `codehub_agent_proxy_bytes_total` |
| | Upstream connect time | `codehub_agent_proxy_upstream_connect_duration_seconds` |
| Proxy WS | Active connections | `codehub_agent_proxy_ws_active` |
| | Connect/Close rate | `codehub_agent_proxy_ws_connect_total`, `ws_close_total` |
| | Messages by direction | `codehub_agent_proxy_ws_messages_total` |
| | Session duration | `codehub_agent_proxy_ws_session_duration_seconds` |
| | Errors | `codehub_agent_proxy_ws_errors_total` |
| Docker | Operation duration | `codehub_agent_docker_duration_seconds` |
| | Errors | `codehub_agent_docker_errors_total` |
| S3 | Operation duration | `codehub_agent_s3_duration_seconds` |
| | Bytes transferred | `codehub_agent_s3_bytes_total` |
| | Errors | `codehub_agent_s3_errors_total` |
| Resources | Containers / Volumes | `codehub_agent_containers_total`, `volumes_total` |
| RW Health | Remote write queue lag | `prometheus_remote_storage_*` (Prom Agent 자체 메트릭) |

---

## 7. 운영 시나리오

### "workspace 접속이 느려요"

```
① Overview → Proxy latency p99 급등
  ↓ drill-down
③ Data Plane → upstream_connect_duration vs request_duration 비교
  • connect 느림 → FRP 터널 or 네트워크 문제
  • connect 정상, request 느림 → workspace container 자체 느림
  • 특정 DP만 → Worst DPs 패널에서 확인 → 해당 DP 서버 점검
```

### "workspace가 사용 중인데 standby 됐어요"

```
② CP → TTL expirations 급증
  ↓
③ Data Plane → WS active connections (연결 있었는지 확인)
  ↓
  Activity heartbeat POST 실패 여부 확인
  → 터널 끊김으로 activity가 CP에 미전달 → TTL이 idle 오판
```

### "DP-B 전체가 안 됩니다"

```
① Overview → DP-B: 🔴 DOWN (up=0) + last_seen > 5min
  ↓
③ Data Plane → [DP: DP-B] → No data
  ↓
  RW Health → remote_write queue lag 확인
  → Prom Agent가 push 못 함 = FRP 터널 or Agent 자체 문제
```

### "workspace 생성이 실패해요"

```
② CP → WC operation PROVISIONING: no data (시작 안 됨)
  ↓
③ Data Plane → Docker errors: create + api_error 급증
  → Docker daemon or docker-proxy 문제
```

### "DB가 느려요"

```
② CP → Infrastructure → PG pool overflow 양수, idle 0
  → pool 소진. 느린 쿼리 or 커넥션 누수
```

---

## 8. 마이그레이션 전략

### 순서 (안전 + 되돌리기 가능)

```
Phase 1: 수신 인프라 준비 (CP)
  ├── Central Prometheus에 --web.enable-remote-write-receiver 활성화
  ├── 새 대시보드 3개 배포 (데이터 없어도 OK — 패널 구조만)
  └── CP에 codehub_dp_last_seen_seconds 메트릭 + /internal/activity 엔드포인트 추가

Phase 2: Agent 메트릭 + Prom Agent 배포 (각 DP)
  ├── Agent proxy.py에 메트릭 계측 코드 추가
  ├── Agent activity batch 기능 추가
  ├── Prometheus Agent Mode 컨테이너 추가
  └── → 데이터 흐름 시작, 새 대시보드에 데이터 표시

Phase 3: 검증 + 알림 추가
  ├── 대시보드 데이터 정상 확인
  ├── DP last seen 알림 추가
  └── remote_write 건강 확인

Phase 4: 정리 (1 retention window 후)
  ├── CP에서 유령 WS 메트릭 3개 제거
  ├── _init_metrics()에서 WS 라벨 초기화 제거
  ├── 구 대시보드 4개 삭제
  └── docker-compose에서 Prometheus의 dp-net 연결 제거 (remote_write만 사용)
```

### 되돌리기

각 Phase는 독립적. 문제 발생 시:
- Phase 2 롤백: DP에서 Prom Agent 제거, Agent 메트릭 코드 revert
- Phase 4 롤백: 구 대시보드 복원, WS 메트릭 복원 (어차피 nodata지만 구조는 유지)

---

## 9. 향후 고려사항

| 항목 | 현재 | 향후 |
|------|------|------|
| DP 수 | 1-3개 | 10+ → recording rules로 집계 쿼리 최적화 |
| 알림 | 없음 | Alertmanager + DP down/error rate 알림 |
| FRP 메트릭 | 미수집 | frps/frpc exporter 활성화 → 터널 건강 메트릭 |
| 로그 수집 | 미구현 | Loki or 유사 솔루션으로 DP 로그 중앙 수집 |
| Trace | 미구현 | OpenTelemetry trace로 Double Proxy 구간별 추적 |
