# ADR-005: Redis Pub/Sub for Multi-Worker SSE

## 상태
Proposed

## 컨텍스트

### 배경
- ADR-004에서 PostgreSQL 전환 및 멀티 워커 배포 결정
- uvicorn 4 workers 환경에서 SSE(Server-Sent Events) 운영

### 문제점
현재 SSE 구현이 in-memory `asyncio.Queue`를 사용하여 **멀티 워커 환경에서 이벤트가 전달되지 않음**.

- Client A가 Worker 1에 SSE 연결
- Client B가 Worker 3에서 워크스페이스 시작 요청
- Worker 3이 이벤트 발행 → Worker 3 메모리에만 전달
- **Client A는 이벤트를 받지 못함**

### 요구사항
- 모든 워커에서 발행된 이벤트가 모든 SSE 클라이언트에게 전달
- 기존 사용자별 필터링 유지
- 향후 세션/캐시 등 확장 가능성

## 결정

### Redis Pub/Sub 도입

| 항목 | 선택 | 이유 |
|------|------|------|
| Message Broker | Redis 7 | 가볍고 빠름, Pub/Sub 네이티브 지원, 운영 단순 |
| 라이브러리 | `redis[hiredis]` | 공식 async 지원, C 기반 파서 |
| 채널 구조 | `events:user:{user_id}` | 사용자별 필터링 유지 |
| Fallback | 없음 | 프론트엔드가 폴링 자체 처리 |

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

## 참고 자료
- [Redis Pub/Sub](https://redis.io/docs/interact/pubsub/)
- [redis-py Async](https://redis-py.readthedocs.io/en/stable/examples/asyncio_examples.html)
