# TTL Manager (M2)

> [README.md](../README.md)로 돌아가기

---

## 개요

TTL Manager는 비활성 워크스페이스의 TTL을 체크하고 desired_state를 변경하는 컴포넌트입니다.

| 항목 | 값 |
|------|---|
| 역할 | idle/archive TTL 만료 시 desired_state 변경 |
| 실행 주기 | 1분 (기본) |
| 단일 인스턴스 | Coordinator에서 실행 |

---

## 핵심 원칙

```
┌─────────────────────────────────────────────────────────────┐
│  TTL Manager는 desired_state만 변경한다                      │
│  (observed_status는 HealthMonitor, operation은 StateReconciler)│
│                                                             │
│  TTL 만료 → desired_state 변경 → StateReconciler가 수렴     │
└─────────────────────────────────────────────────────────────┘
```

---

## TTL 종류

| TTL | 대상 | 동작 |
|-----|-----|------|
| **standby_ttl** | RUNNING 워크스페이스 | idle → desired_state = STANDBY |
| **archive_ttl** | STANDBY 워크스페이스 | 장기 미사용 → desired_state = PENDING |

---

## 입력

### DB 읽기

| 컬럼 | 용도 |
|-----|------|
| `observed_status` | 현재 상태 (TTL 체크 대상 필터) |
| `operation` | 진행 중 작업 (NONE이 아니면 skip) |
| `standby_ttl_seconds` | RUNNING → STANDBY TTL |
| `archive_ttl_seconds` | STANDBY → PENDING TTL |
| `last_access_at` | 마지막 접근 시점 |

### Redis 읽기

| 키 | 용도 |
|---|------|
| `ws_conn:{workspace_id}` | WebSocket 연결 수 |
| `idle_timer:{workspace_id}` | 5분 idle 타이머 |

---

## 출력

### DB 쓰기 (단일 Writer 원칙)

| 컬럼 | 설명 |
|-----|------|
| `desired_state` | 목표 상태 변경 |

> **단일 Writer**: desired_state는 API와 TTL Manager가 공유
> (Known Issue: 경쟁 조건 가능, Last-Write-Wins)

---

## 알고리즘

### Standby TTL 체크 (RUNNING → STANDBY)

```python
async def check_standby_ttl():
    """RUNNING 워크스페이스의 idle 체크"""

    # 1. 대상 조회: RUNNING이고 operation 없는 것
    workspaces = await db.execute("""
        SELECT * FROM workspaces
        WHERE observed_status = 'RUNNING'
          AND operation = 'NONE'
          AND deleted_at IS NULL
    """)

    for ws in workspaces:
        # 2. 활성 연결 체크 (Redis)
        conn_count = await redis.get(f"ws_conn:{ws.id}")
        if conn_count and int(conn_count) > 0:
            continue  # 활성 상태

        # 3. idle 타이머 체크 (Redis)
        idle_timer = await redis.exists(f"idle_timer:{ws.id}")
        if idle_timer:
            continue  # 5분 대기 중

        # 4. TTL 만료 → desired_state 변경
        logger.info(f"Workspace {ws.id}: Standby TTL expired")
        await db.execute("""
            UPDATE workspaces
            SET desired_state = 'STANDBY'
            WHERE id = $1 AND desired_state = 'RUNNING'
        """, ws.id)
```

### Archive TTL 체크 (STANDBY → PENDING)

```python
async def check_archive_ttl():
    """STANDBY 워크스페이스의 archive TTL 체크"""

    # DB 기반 (Redis 아님)
    workspaces = await db.execute("""
        SELECT * FROM workspaces
        WHERE observed_status = 'STANDBY'
          AND operation = 'NONE'
          AND deleted_at IS NULL
          AND NOW() - last_access_at > archive_ttl_seconds * INTERVAL '1 second'
    """)

    for ws in workspaces:
        logger.info(f"Workspace {ws.id}: Archive TTL expired")
        await db.execute("""
            UPDATE workspaces
            SET desired_state = 'PENDING'
            WHERE id = $1 AND desired_state = 'STANDBY'
        """, ws.id)
```

### 메인 루프

```python
async def run_loop(self):
    """TTL Manager 메인 루프"""
    while True:
        try:
            await self.check_standby_ttl()
            await self.check_archive_ttl()
        except Exception as e:
            logger.error(f"TTL Manager error: {e}")

        await asyncio.sleep(60)
```

---

## 활동 감지 흐름

### Standby TTL (WebSocket 기반)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Standby TTL 흐름                                      │
└─────────────────────────────────────────────────────────────────────────────┘

Browser                    Proxy                     Redis                  TTL Manager
   │                         │                         │                         │
   │─── WebSocket Connect ──▶│                         │                         │
   │                         │─── INCR ws_conn:{id} ──▶│                         │
   │                         │─── DEL idle_timer:{id} ▶│                         │
   │                         │                         │                         │
   │       (사용 중...)       │                         │                         │
   │                         │                         │                         │
   │─── WebSocket Close ────▶│                         │                         │
   │                         │─── DECR ws_conn:{id} ──▶│                         │
   │                         │                         │                         │
   │                         │   (count == 0)          │                         │
   │                         │─ SETEX idle_timer 300 ─▶│                         │
   │                         │                         │                         │
   │                         │                         │─── 5분 후 자동 만료 ───▶│
   │                         │                         │                         │
   │                         │                         │          TTL Manager 체크
   │                         │                         │◀── ws_conn=0, no timer ─│
   │                         │                         │                         │
   │                         │                         │   desired_state=STANDBY │
   │                         │                         │                         │
```

### Archive TTL (DB 기반)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Archive TTL 흐름                                      │
└─────────────────────────────────────────────────────────────────────────────┘

                           Database                             TTL Manager
                              │                                      │
      STOPPING 완료 시        │                                      │
    last_access_at 업데이트 ─▶│                                      │
                              │                                      │
                              │        (archive_ttl_seconds 경과)    │
                              │                                      │
                              │◀─────── TTL Manager 주기 체크 ───────│
                              │                                      │
                              │   NOW() - last_access_at > ttl?     │
                              │                                      │
                              │─── desired_state = PENDING ─────────▶│
                              │                                      │
```

---

## 에러 처리

### Redis 연결 실패

| 상황 | 처리 |
|-----|------|
| Redis 읽기 실패 | 해당 워크스페이스 skip, 다음 tick에 재시도 |
| Redis 전체 장애 | 모든 standby TTL 체크 skip |

### DB 업데이트 실패

| 상황 | 처리 |
|-----|------|
| UPDATE 실패 | 로그 기록, 다음 tick에 재시도 |
| 경쟁 조건 | Last-Write-Wins (Known Issue) |

---

## 다른 컴포넌트와의 상호작용

### 의존

| 컴포넌트 | 의존 내용 |
|---------|---------|
| Proxy | ws_conn, idle_timer 관리 |
| HealthMonitor | observed_status 제공 |

### 의존받음

| 컴포넌트 | 의존 내용 |
|---------|---------|
| StateReconciler | desired_state 변경 트리거 |

### 잠재적 충돌

| 시나리오 | 영향 | 완화 |
|---------|-----|------|
| API와 동시에 desired_state 변경 | Last-Write-Wins | CAS 쿼리 (`WHERE desired_state = ?`) |
| Proxy 장애로 ws_conn 미업데이트 | 잘못된 TTL 만료 | Redis 키 TTL 설정 (자동 정리) |

---

## Known Issues / Limitations

### 1. desired_state 경쟁 조건

```
T1: 사용자가 API로 desired_state=RUNNING 설정
T2: TTL Manager가 idle_timer 만료 감지 (동시)
T3: TTL Manager가 desired_state=STANDBY 덮어쓰기
T4: 사용자 의도(RUNNING)가 무시됨
```

**현재 동작**: Last-Write-Wins

**잠재적 해결책** (M2에서는 미구현):
- CAS: `WHERE desired_state = ?`
- 우선순위: 수동 변경 > TTL 변경
- 버전: Optimistic Locking

### 2. last_access_at 업데이트 시점

- 현재: STOPPING 완료 시 업데이트
- 문제: WebSocket 연결 중 업데이트 안 됨
- 영향: archive_ttl 정확도

---

## 설정

| 환경변수 | 기본값 | 설명 |
|---------|-------|------|
| `TTL_MANAGER_INTERVAL` | 60 | 체크 주기 (초) |
| `DEFAULT_STANDBY_TTL` | 300 | 기본 standby TTL (초) |
| `DEFAULT_ARCHIVE_TTL` | 86400 | 기본 archive TTL (초, 24시간) |
| `IDLE_TIMER_SECONDS` | 300 | idle 타이머 (초, 5분) |

---

## 참조

- [coordinator.md](./coordinator.md) - Coordinator 프로세스
- [state-reconciler.md](./state-reconciler.md) - StateReconciler (desired_state 소비자)
- [../activity.md](../activity.md) - 활동 추적 상세
- [../schema.md](../schema.md) - DB 스키마
