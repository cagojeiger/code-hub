# HealthMonitor (M2)

> [README.md](../README.md)로 돌아가기

---

## 개요

HealthMonitor는 실제 리소스 상태를 관측하고 DB에 반영하는 **관측자** 컴포넌트입니다.

| 항목 | 값 |
|------|---|
| 역할 | 실제 리소스 관측 → observed_status 갱신 |
| 실행 주기 | 30초 (기본) |
| 단일 인스턴스 | Coordinator에서 실행 |

---

## 핵심 원칙

```
┌─────────────────────────────────────────────────────────────┐
│  진실(Reality) = 실제 리소스 (컨테이너, 볼륨, S3)           │
│  DB = Last Observed Truth (마지막 관측의 합의된 결과)       │
│                                                             │
│  HealthMonitor가 멈추면 DB는 "고정된 거짓말"이 된다         │
└─────────────────────────────────────────────────────────────┘
```

---

## 입력

### DB 읽기

| 컬럼 | 용도 |
|-----|------|
| `id` | 워크스페이스 식별 |
| `error_info` | ERROR 상태 판정 |
| `archive_key` | PENDING 상태에서 복원 가능 여부 |

### Redis 읽기

| 키 | 용도 |
|---|------|
| `monitor:trigger` | 즉시 관측 요청 (hint) |

### 실제 리소스 관측

| 대상 | 관측 항목 |
|-----|---------|
| Container | 존재 여부, 실행 상태 (running/stopped) |
| Volume | 존재 여부 |

---

## 출력

### DB 쓰기 (단일 Writer 원칙)

| 컬럼 | 설명 |
|-----|------|
| `observed_status` | 관측된 상태 (PENDING/STANDBY/RUNNING/ERROR) |
| `observed_at` | 관측 시점 |

> **단일 Writer**: observed_status는 HealthMonitor만 씁니다.

### Redis 발행

| 채널 | 용도 |
|-----|------|
| `workspace:{id}` | SSE 상태 변경 알림 |

---

## 알고리즘

### 상태 계산

```python
def compute_observed_status(
    container: ContainerState,
    volume: VolumeState,
    error_info: Optional[ErrorInfo],
    archive_key: Optional[str]
) -> Status:
    """실제 상태로부터 observed_status 계산"""

    # 1. ERROR 판정 (terminal error가 있으면 ERROR)
    if error_info and error_info.get('is_terminal'):
        return ERROR

    # 2. 불변식 위반 체크
    if container.exists and not volume.exists:
        # 컨테이너는 있는데 볼륨이 없음 = 불가능한 상태
        return ERROR

    # 3. 정상 상태 계산
    if container.exists and container.running:
        return RUNNING

    if volume.exists:
        return STANDBY  # 볼륨만 있음

    # 4. 둘 다 없음
    if archive_key:
        return PENDING  # 아카이브에서 복원 가능
    else:
        return ERROR    # DataLost (복구 불가)
```

### 관측 및 업데이트

```python
async def observe_and_update(ws: Workspace):
    """단일 워크스페이스 관측 및 DB 업데이트"""

    # 1. 실제 리소스 관측
    container = await container_provider.get_state(ws.id)
    volume = await volume_provider.get_state(ws.id)

    # 2. observed_status 계산
    new_status = compute_observed_status(
        container=container,
        volume=volume,
        error_info=ws.error_info,
        archive_key=ws.archive_key
    )

    # 3. 변경된 경우만 업데이트
    if new_status != ws.observed_status:
        await db.execute("""
            UPDATE workspaces
            SET observed_status = $1, observed_at = NOW()
            WHERE id = $2
        """, new_status, ws.id)

        # 4. SSE 알림
        await redis.publish(f"workspace:{ws.id}", json.dumps({
            "event": "state_changed",
            "data": {
                "workspace_id": ws.id,
                "status": new_status,
                "operation": ws.operation,
                "desired_state": ws.desired_state
            }
        }))

        logger.info(f"Workspace {ws.id}: {ws.observed_status} → {new_status}")
```

### 메인 루프

```python
async def run_loop(self):
    """HealthMonitor 메인 루프"""
    while True:
        # 1. 모든 활성 워크스페이스 조회
        workspaces = await db.execute("""
            SELECT * FROM workspaces
            WHERE deleted_at IS NULL
        """)

        # 2. 각 워크스페이스 관측
        for ws in workspaces:
            try:
                await self.observe_and_update(ws)
            except Exception as e:
                logger.error(f"Observe failed for {ws.id}: {e}")

        # 3. 다음 주기까지 대기
        await asyncio.sleep(30)
```

---

## 즉시 관측 (Edge Hint)

### 목적

StateReconciler가 operation 완료 후 빠른 상태 반영을 위해 즉시 관측 요청.

### 구현

```python
async def listen_for_hints(self):
    """Redis에서 즉시 관측 요청 수신"""
    pubsub = redis.pubsub()
    await pubsub.subscribe("monitor:trigger")

    async for message in pubsub.listen():
        if message["type"] == "message":
            workspace_id = message["data"]
            ws = await db.get_workspace(workspace_id)
            if ws:
                await self.observe_and_update(ws)
```

### 사용 예시 (StateReconciler에서)

```python
async def execute_starting(ws):
    # 1. 컨테이너 시작
    await container_provider.start(ws.id)

    # 2. HealthMonitor에 즉시 관측 요청
    await redis.publish("monitor:trigger", ws.id)
```

---

## 상태 전이 다이어그램

```
                     ┌──────────────────────────────────────────┐
                     │            HealthMonitor                 │
                     │         (30초마다 관측)                   │
                     └──────────────────────────────────────────┘
                                        │
                                        │ observe
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Actual Resources                                    │
│                                                                             │
│   Container: exists? running?                                               │
│   Volume: exists?                                                           │
│   error_info: is_terminal?                                                  │
│   archive_key: exists?                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        │ compute
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        observed_status 결정                                  │
│                                                                             │
│   error_info.is_terminal = true     → ERROR                                │
│   container + !volume               → ERROR (불변식 위반)                   │
│   container + running               → RUNNING                               │
│   volume (no container)             → STANDBY                               │
│   !volume + archive_key             → PENDING                               │
│   !volume + !archive_key            → ERROR (DataLost)                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        │ update
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Database                                        │
│   UPDATE workspaces SET observed_status = ?, observed_at = NOW()           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 에러 처리

### 관측 실패

| 상황 | 처리 |
|-----|------|
| Container API 실패 | 로그 기록, 다음 tick에 재시도 |
| Volume API 실패 | 로그 기록, 다음 tick에 재시도 |
| DB 업데이트 실패 | 로그 기록, 다음 tick에 재시도 |

> **원칙**: 개별 워크스페이스 실패가 전체 루프를 중단시키지 않음

### Stale Observation 경고

```python
STALE_THRESHOLD = timedelta(minutes=2)

async def check_stale_observations():
    """오래된 관측 경고"""
    stale = await db.execute("""
        SELECT id FROM workspaces
        WHERE observed_at < NOW() - INTERVAL '2 minutes'
          AND deleted_at IS NULL
    """)
    if stale:
        logger.warning(f"Stale observations: {[w.id for w in stale]}")
```

---

## 다른 컴포넌트와의 상호작용

### 의존

| 컴포넌트 | 의존 내용 |
|---------|---------|
| Container Provider | 컨테이너 상태 조회 |
| Volume Provider | 볼륨 상태 조회 |

### 의존받음

| 컴포넌트 | 의존 내용 |
|---------|---------|
| StateReconciler | observed_status 읽기, 즉시 관측 요청 |
| TTL Manager | observed_status 읽기 |

### 잠재적 충돌

| 시나리오 | 영향 | 완화 |
|---------|-----|------|
| StateReconciler가 operation 실행 중 관측 | 중간 상태 관측 가능 | 정상 동작 (Level-Triggered) |
| HealthMonitor 멈춤 | observed_status가 stale | Stale 경고, 모니터링 |

---

## 설정

| 환경변수 | 기본값 | 설명 |
|---------|-------|------|
| `HEALTH_MONITOR_INTERVAL` | 30 | 관측 주기 (초) |
| `HEALTH_MONITOR_STALE_THRESHOLD` | 120 | Stale 경고 임계값 (초) |

---

## Known Issues / Limitations

### 1. 관측 지연

- 최대 30초 지연 (폴링 주기)
- 완화: Redis hint로 즉시 관측 요청

### 2. 부분 상태

- 컨테이너 시작 중 관측 시 inconsistent 상태 가능
- 영향 없음: 다음 tick에서 정확한 상태 반영

### 3. ERROR에서 복구

- ERROR 상태에서 자동 복구 불가
- 관리자가 문제 해결 후 error_info 초기화 필요

---

## 참조

- [coordinator.md](./coordinator.md) - Coordinator 프로세스
- [state-reconciler.md](./state-reconciler.md) - StateReconciler (observed_status 소비자)
- [../error.md](../error.md) - 에러 정책
- [../schema.md](../schema.md) - DB 스키마
