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
- 64-bit lock ID (SHA-256 해시, 충돌 방지)

### 안정성 패턴

| 패턴 | 구현 | 효과 |
|------|------|------|
| 재진입 방지 | 이미 리더면 DB 스킵 | 락 누적 방지 |
| 파라미터 바인딩 | SQL 인젝션 방지 | 보안 강화 |
| 타임아웃 | `asyncio.timeout(5s)` | 무한 대기 방지 |
| 주기적 확인 | VERIFY_INTERVAL=10초 | Split Brain 감지 |
| Jitter | ±30% | Thundering Herd 방지 |
| 작업 전 확인 | `verify_holding()` | tick 중 Split Brain 방지 |

### Split Brain 방지

```
┌────────────────────────────────────────────────────┐
│               Tick Execution Flow                   │
│                                                     │
│  1. _ensure_leadership()                            │
│     └─ try_acquire() 호출 (VERIFY_INTERVAL마다)     │
│                                                     │
│  2. _execute_tick()                                 │
│     └─ verify_holding() ─→ pg_locks 조회            │
│        ├─ True: tick() 실행                         │
│        └─ False: tick 스킵, 리더십 재획득 시도      │
│                                                     │
│  3. tick()                                          │
│     └─ 실제 작업 수행                               │
└────────────────────────────────────────────────────┘
```

**타이밍:**
- VERIFY_INTERVAL: 10초 (±30% jitter)
- verify_holding 타임아웃: 2초
- 최악 Split Brain 감지: ~12초

### 엣지 케이스

| 시나리오 | 현재 대응 |
|---------|---------|
| 네트워크 파티션 | verify_holding()으로 ~2초 내 감지 |
| 롤링 배포 | Idempotent 설계로 안전 |
| DB 응답 지연 | 타임아웃 후 리더십 포기 |
| 재진입 락 | is_leader 체크로 DB 호출 스킵 |

---

## 힌트 가속 (Redis PUB/SUB)

```
┌─────────┐  PUBLISH "codehub:wake:wc"  ┌────────────┐
│   API   │ ────────────────────────→  │    Redis   │
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
| `codehub:wake:ob` | EventListener | Observer |
| `codehub:wake:wc` | EventListener, TTL | WC |

> **채널 설정**: `RedisChannelConfig.wake_prefix` (기본: `codehub:wake`)

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
