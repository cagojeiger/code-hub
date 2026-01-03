# Coordination Infrastructure

> Coordinator 공통 인프라: 배포 모델, 리더 선출, 힌트 채널

---

## 배포 모델

| 컴포넌트 | 실행 위치 | 스케일링 |
|---------|----------|----------|
| API | FastAPI 프로세스 | N replicas (수평 확장) |
| Coordinator | FastAPI lifespan | 1 active (리더) |

```
┌─────────────────────────────────────┐
│           FastAPI Process           │
│  ┌─────────┐  ┌─────────────────┐  │
│  │   API   │  │   Coordinator   │  │
│  │ (HTTP)  │  │ (background)    │  │
│  └─────────┘  └─────────────────┘  │
│        ↓              ↓            │
│      asyncio event loop (공유)     │
└─────────────────────────────────────┘
```

---

## 리더 선출

| 기술 | 방식 | 용도 |
|------|------|------|
| PG Advisory Lock | `pg_try_advisory_lock(LOCK_ID)` | 단일 Coordinator 보장 |

**특징:**
- 다중 파드/워커 환경에서 하나만 리더
- 리더 실패 시 다른 인스턴스가 즉시 인수
- 세션 기반 잠금 (연결 끊기면 자동 해제)

---

## 힌트 가속 (Redis PUB/SUB)

```
┌─────────┐     PUBLISH "wc:wake"     ┌────────────┐
│   API   │ ───────────────────────→  │    Redis   │
│   TTL   │                           │  PUB/SUB   │
│  Proxy  │                           └─────┬──────┘
└─────────┘                                 │
                                            │ SUBSCRIBE
                                            ▼
                                    ┌──────────────┐
                                    │      WC      │
                                    │ (즉시 깨어남) │
                                    └──────────────┘
```

### 채널 목록

| Channel | Publishers | Subscriber |
|---------|-----------|------------|
| `wc:wake` | API, TTL, Proxy | WC |
| `gc:wake` | API | GC |

### Hybrid Push/Pull

- **Hint 있으면** → 즉시 tick (ms 단위)
- **Hint 없어도** → 폴링으로 보완 (Level-Triggered)

---

## 폴링 전략

| 모드 | 주기 | 조건 |
|------|------|------|
| Idle | 10s | 진행 중 작업 없음 |
| Active | 2s | operation 진행 중 |
| Hint | 즉시 | Redis PUBLISH 수신 |

---

## 멱등성 키

| 방식 | 구현 | 용도 |
|------|------|------|
| UUID v4 | `uuid.uuid4()` | CAS 충돌 방지, 중복 실행 방지 |

---

## DB 연결 전략

### Connection per Coordinator

각 Coordinator는 **독립된 DB 연결**을 사용:

```
┌─────────────────────────────────────────────────────┐
│                 FastAPI Process                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │  Observer   │  │     WC      │  │    TTL/GC   │  │
│  │  (conn #1)  │  │  (conn #2)  │  │ (conn #3,4) │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │
│         │                │                │         │
│         ▼                ▼                ▼         │
│      PostgreSQL Connection Pool (4 connections)     │
└─────────────────────────────────────────────────────┘
```

**이유:**
- SQLAlchemy AsyncConnection은 concurrent task 간 공유 불가
- 각 연결은 독립된 트랜잭션 컨텍스트 유지
- Advisory Lock도 연결별로 동작 (동일 Lock ID = 서로 블로킹)

**Advisory Lock 동작:**

| Coordinator | Lock Key | 같은 타입 다른 Pod | 다른 타입 같은 Pod |
|-------------|----------|-------------------|-------------------|
| Observer | `coordinator:observer` | ❌ 블로킹 | ✅ 독립 |
| WC | `coordinator:wc` | ❌ 블로킹 | ✅ 독립 |
| TTL | `coordinator:ttl` | ❌ 블로킹 | ✅ 독립 |
| GC | `coordinator:gc` | ❌ 블로킹 | ✅ 독립 |

→ 동일 타입 Coordinator는 클러스터 전체에서 1개만 리더로 동작
→ 서로 다른 타입은 같은 프로세스 내에서도 병렬 실행 가능
