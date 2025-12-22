# ADR-005: Redis Pub/Sub for Multi-Worker SSE

## 상태
Proposed

## 컨텍스트

### 배경
- ADR-004에서 PostgreSQL 전환 완료
- uvicorn 4 workers로 멀티 워커 배포 설정
- 실시간 워크스페이스 상태 업데이트를 위한 SSE(Server-Sent Events) 구현

### 문제점
현재 SSE 구현이 in-memory `asyncio.Queue`를 사용하여 **멀티 워커 환경에서 이벤트가 전달되지 않음**.

```
┌─────────────────────────────────────────────────────────┐
│                    Backend (4 workers)                   │
├─────────────┬─────────────┬─────────────┬───────────────┤
│  Worker 1   │  Worker 2   │  Worker 3   │   Worker 4    │
│  ┌───────┐  │  ┌───────┐  │  ┌───────┐  │  ┌───────┐    │
│  │Queue A│  │  │Queue B│  │  │Queue C│  │  │Queue D│    │
│  └───────┘  │  └───────┘  │  └───────┘  │  └───────┘    │
│   (독립)    │   (독립)    │   (독립)    │    (독립)     │
└─────────────┴─────────────┴─────────────┴───────────────┘
```

**문제 시나리오**:
1. Client A가 Worker 1에 SSE 연결 → Queue A에 등록
2. Client B가 Worker 3에서 워크스페이스 시작 요청
3. Worker 3이 이벤트 발행 → Queue C에만 전달
4. **Client A는 이벤트를 받지 못함** (Queue A와 Queue C는 별개 프로세스 메모리)

### 현재 구현
- 이벤트 발행: `backend/app/core/events.py`
- SSE 엔드포인트: `backend/app/api/v1/events.py`
- 프로세스별 독립적인 `_event_queues: dict[str, asyncio.Queue]`

### 요구사항
- 모든 워커에서 발행된 이벤트가 모든 SSE 클라이언트에게 전달
- 기존 사용자별 필터링 유지 (`owner_user_id` 기반)
- 향후 세션/캐시 등 확장 가능성

## 결정

### Redis Pub/Sub 도입

| 항목 | 선택 | 이유 |
|------|------|------|
| Message Broker | Redis 7 | 가볍고 빠름, Pub/Sub 네이티브 지원, 운영 단순 |
| 라이브러리 | `redis[hiredis]>=5.0.0` | 공식 async 지원, C 기반 파서로 성능 향상 |
| 채널 구조 | `events:user:{user_id}` | 사용자별 필터링 유지 |
| Fallback | 없음 (hard dependency) | 프론트엔드가 폴링 자체 처리 |

### 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    Backend (4 workers)                   │
├─────────────┬─────────────┬─────────────┬───────────────┤
│  Worker 1   │  Worker 2   │  Worker 3   │   Worker 4    │
│  subscribe  │  subscribe  │  subscribe  │   subscribe   │
└──────┬──────┴──────┬──────┴──────┬──────┴───────┬───────┘
       │             │             │              │
       └─────────────┴──────┬──────┴──────────────┘
                            │
                     ┌──────▼──────┐
                     │    Redis    │
                     │   Pub/Sub   │
                     └─────────────┘
```

**동작 흐름**:
1. Client A → Worker 1 SSE 연결 → Redis `subscribe("events:user:123")`
2. Client B → Worker 3 워크스페이스 시작 → Redis `publish("events:user:123", event)`
3. Redis가 모든 subscriber에게 브로드캐스트
4. Worker 1이 메시지 수신 → Client A에게 SSE 전송

### 주요 변경

| 항목 | 변경 내용 |
|------|----------|
| 의존성 | `redis[hiredis]>=5.0.0` 추가 |
| Docker Compose | Redis 7 서비스 추가, backend 의존성 설정 |
| 설정 | `RedisConfig` 추가 (`CODEHUB_REDIS__URL`) |
| 이벤트 발행 | sync → async 전환, Redis publish |
| SSE 엔드포인트 | Redis subscribe 기반으로 재구현 |

### 배포 흐름

```
postgres (healthy) → migrate → startup-sync → backend (4 workers)
    ↓                                              ↓
  redis (healthy) ←────────────────────────────────┘
```

## 결과

### 장점
- 멀티 워커 환경에서 모든 클라이언트가 이벤트 수신
- 향후 세션 저장, 캐시 등으로 Redis 활용 확장 가능
- Kubernetes 환경에서도 동일하게 동작

### 단점
- Redis 의존성 추가 (운영 복잡도 증가)
- `notify_*` 함수가 sync → async로 변경 (호출부 수정 필요)

### 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| PostgreSQL LISTEN/NOTIFY | 추가 연결 필요, 메시지 크기 제한 (8KB) |
| RabbitMQ | 오버스펙, 운영 복잡도 높음 |
| In-memory + 단일 워커 | 스케일 포기, ADR-004 목적 상충 |

### ADR-004 영향
- ADR-004에서 멀티 워커 배포 결정
- 본 ADR은 멀티 워커 환경에서 SSE 정상 동작을 위한 후속 결정

## 참고 자료
- [Redis Pub/Sub](https://redis.io/docs/interact/pubsub/)
- [FastAPI SSE](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
- [redis-py Async](https://redis-py.readthedocs.io/en/stable/examples/asyncio_examples.html)
