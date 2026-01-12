# ADR-012: Coordinator DB Connection 전략

## 상태
Accepted

## 컨텍스트

### 배경
- Coordinator가 PostgreSQL Advisory Lock으로 리더 선출
- tick() 내에서 SQLModel ORM으로 DB 트랜잭션 수행
- K8S 환경에서 다중 Pod 스케일링 고려 필요

### 문제점
Advisory Lock과 ORM 트랜잭션의 connection 관계:

| 방식 | Lock Connection | Transaction Connection | 정합성 |
|------|-----------------|------------------------|--------|
| 분리 | conn A | conn B (pool) | X |
| 동일 | conn | conn | O |

분리 시 장애 시나리오:
```
t0: Lock 획득 (conn A)
t1: 트랜잭션 시작 (conn B)
t2: UPDATE 실행
t3: conn B 끊김 → 트랜잭션 롤백
t4: Lock 유지 (conn A) → Zombie Lock
```

### 요구사항
- Lock + Transaction 원자적 실패 보장
- SQLModel ORM 기능 유지 (타입 힌트, 자동완성)
- K8S 스케일링 고려 (connection 비용 최소화)

## SQLAlchemy 내부 동작 분석

### bind 타입에 따른 connection 획득 방식

SQLAlchemy 소스코드 분석 결과 (`sqlalchemy/orm/session.py:1179-1188`):

```python
# SessionTransaction._connection_for_bind()
if isinstance(bind, engine.Connection):
    conn = bind                 # Connection → 그대로 사용
else:
    conn = bind.connect()       # Engine → pool에서 새로 가져옴
    local_connect = True
```

| bind 타입 | 내부 동작 | 새 연결? |
|-----------|----------|----------|
| `Engine` | `bind.connect()` | **YES** (pool에서 매번) |
| `Connection` | `conn = bind` | **NO** (그대로 사용) |

### 코드 예시

```python
# ❌ Pool에서 connection (Advisory Lock과 다를 수 있음)
async_sessionmaker(bind=engine)

# ✅ 기존 connection 그대로 사용 (Advisory Lock과 동일)
AsyncSession(bind=conn)
```

## 결정

### Connection 전략: 동일 Connection 사용

```python
class CoordinatorBase(ABC):
    def __init__(self, conn: AsyncConnection, ...):
        self._conn = conn  # Lock + Transaction 동일 connection
        self._leader = LeaderElection(conn, self.COORDINATOR_TYPE)

class WorkspaceController(CoordinatorBase):
    async def tick(self) -> None:
        # IMPORTANT: Advisory Lock과 동일한 connection 사용
        # - bind=Engine → pool에서 새 connection (Lock과 다를 수 있음)
        # - bind=Connection → 지정한 connection 그대로 사용
        # See: ADR-012, sqlalchemy/orm/session.py:1179-1188
        async with AsyncSession(bind=self._conn) as session:
            stmt = select(Workspace).where(...)
            ...
```

### 블로킹 완화 전략

1. **Random Initial Delay**: 시작 시 0-5초 jitter
2. **짧은 트랜잭션**: tick() 내 작업 최소화
3. **배치 크기 제한**: 한 번에 처리할 row 수 제한

## API Handler와의 차이

| 사용처 | 리더 선출 | 패턴 | 이유 |
|--------|----------|------|------|
| API Handler | 없음 | `get_session()` (pool) | 독립 요청, Lock 없음 |
| Coordinator | 있음 | `AsyncSession(bind=conn)` | Lock과 정합성 필요 |

## 결과

### 장점
- Lock + Transaction 원자적 실패 보장
- Zombie Lock 방지
- 장애 시 자동 복구 (connection 끊김 → Lock 해제 + 롤백)
- SQLModel ORM 기능 100% 유지

### 단점
- Coordinator 간 블로킹 위험 (지연일 뿐 실패 아님)
- tick() 실행 중 다른 Coordinator 대기

### 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| Connection Pool 분리 | Zombie Lock 위험, 정합성 깨짐 |
| Coordinator별 Connection | 리소스 증가, 스케일링 제한 |
| Transaction-level Lock | Session Lock과 다른 동작 |

## 사고 실험: Connection 끊김 시나리오

### 시나리오 1: 동일 Connection - 끊김

```
시간  상태
─────────────────────────────────────
t0    Connection A 생성
t1    Lock 획득 ✓
t2    Transaction 시작
t3    UPDATE workspace SET status='running'
t4    ─── Connection A 끊김 ───
      │
      ├─ Lock: 자동 해제 (session 종료)
      └─ Transaction: 자동 롤백 (연결 끊김)

t5    다른 Pod가 Lock 획득 가능 ✓
t6    DB 상태: 변경 없음 (롤백됨) ✓
```

**결과**: 깔끔한 복구. 다른 리더가 이어받을 수 있음.

### 시나리오 2: 분리 Connection - A만 끊김 (Lock)

```
시간  Connection A (Lock)    Connection B (Transaction)
──────────────────────────────────────────────────────
t0    생성                    생성
t1    Lock 획득 ✓             -
t2    -                       Transaction 시작
t3    -                       UPDATE workspace...
t4    ─── 끊김 ───            (진행 중)
      │
      └─ Lock 해제됨

t5    다른 Pod Lock 획득!     아직 UPDATE 진행 중...
t6    다른 Pod도 UPDATE!      COMMIT 시도
t7    ─── 충돌! ───
```

**결과**: 두 Coordinator가 동시에 같은 workspace 수정 → **데이터 충돌**

### 시나리오 3: 분리 Connection - B만 끊김 (Transaction)

```
시간  Connection A (Lock)    Connection B (Transaction)
──────────────────────────────────────────────────────
t0    생성                    생성
t1    Lock 획득 ✓             -
t2    -                       Transaction 시작
t3    -                       UPDATE workspace...
t4    (유지)                  ─── 끊김 ───
                              │
                              └─ 롤백됨

t5    Lock 여전히 유지        (없음)
t6    다른 Pod: Lock 획득 불가!
t7    ─── Zombie Lock ───
```

**결과**: Lock은 유지되지만 작업은 실패 → **Zombie Lock** (아무도 진행 못함)

### 시나리오 4: 분리 Connection - 둘 다 끊김

```
시간  Connection A (Lock)    Connection B (Transaction)
──────────────────────────────────────────────────────
t0    생성                    생성
t1    Lock 획득 ✓             -
t2    -                       Transaction 시작
t3    -                       UPDATE workspace...
t4    ─── 끊김 ───            ─── 끊김 ───
      │                       │
      └─ Lock 해제            └─ 롤백

t5    다른 Pod Lock 획득 가능 ✓
```

**결과**: 우연히 괜찮음. 하지만 **타이밍에 의존** (시나리오 2, 3이 더 자주 발생)

### 요약

| 시나리오 | Lock | Transaction | 결과 |
|----------|------|-------------|------|
| 동일 conn 끊김 | 해제 | 롤백 | ✅ 정상 복구 |
| 분리, A만 끊김 | 해제 | 진행 중 | ❌ 동시 수정 충돌 |
| 분리, B만 끊김 | 유지 | 롤백 | ❌ Zombie Lock |
| 분리, 둘 다 끊김 | 해제 | 롤백 | ⚠️ 우연히 OK |

**동일 Connection**만 모든 상황에서 안전함.

## 구현 변경사항

| 파일 | 변경 |
|------|------|
| `control/coordinator/base.py` | `self._conn` 추가 + tick() 사용 가이드 주석 |

## 참고 자료
- [PostgreSQL Advisory Locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)
- [SQLAlchemy AsyncSession](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- SQLAlchemy 소스: `sqlalchemy/orm/session.py:1179-1188`
