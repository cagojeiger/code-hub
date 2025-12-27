# ADR-010: 패키지 분리 아키텍처 (Core / Control / Adapters)

## 상태
Proposed

## 컨텍스트

### 문제 상황

M2에서 Reconciler 패턴, 선언형 API, Object Storage 등 복잡한 기능이 추가된다. 기존 M1 구조는 단일 `backend/app/` 디렉토리에 모든 코드가 혼재되어 있다.

현재 구조:
```
backend/app/
├── api/v1/           # REST API
├── services/
│   ├── workspace_service.py  # 오케스트레이션
│   ├── instance/
│   │   ├── interface.py      # 인터페이스
│   │   └── local_docker.py   # Docker 구현체
│   └── storage/
│       ├── interface.py      # 인터페이스
│       └── local_dir.py      # 로컬 구현체
├── proxy/            # HTTP/WS 프록시
├── db/               # 데이터베이스
└── core/             # 유틸리티
```

### 핵심 문제

**순수 비즈니스 로직과 인프라 코드 혼재**: Reconciler 알고리즘(상태 수렴 로직)을 테스트하려면 실제 Docker가 필요하다. 상태 머신 규칙 같은 순수 로직도 인프라 없이 테스트할 수 없다.

**구현체 교체 어려움**: Docker에서 Kubernetes로 전환하려면 여러 파일을 수정해야 한다. 인터페이스와 구현체가 같은 디렉토리에 있어서 경계가 불명확하다.

**테스트 속도 저하**: 단위 테스트도 Docker, PostgreSQL, Redis가 필요해서 CI/CD 파이프라인이 느려진다.

## 결정

**3계층 패키지 분리**를 도입한다.

```
packages/
├── core/       # 순수 함수, 인터페이스 정의
├── control/    # Reconciler, API, Proxy 구현
└── adapters/   # Docker, MinIO 등 인프라 구현체

apps/
└── backend/    # 패키지 조립, 설정
```

**의존성 방향**:
- core → (의존 없음)
- control → core
- adapters → core
- backend → core, control, adapters

```
                    apps/backend (조립)
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
   packages/adapters packages/control     │
            │              │              │
            └──────────────┴──────────────┘
                           │
                           ▼
                     packages/core
```

## 상세 구조

### Core 패키지 (`packages/core/`)

순수 함수와 인터페이스만 포함. 외부 의존성 최소화 (pydantic만).

```
src/codehub_core/
├── domain/
│   ├── workspace.py       # WorkspaceStatus, WorkspaceOperation Enum
│   └── state_machine.py   # 레벨 정의, 전환 규칙
├── algorithms/
│   ├── reconcile.py       # determine_action() 순수 함수
│   └── transitions.py     # step_up/down 규칙
└── interfaces/
    ├── instance.py        # InstanceController ABC
    ├── storage.py         # StorageProvider ABC
    └── events.py          # EventPublisher ABC
```

핵심 코드 예시:

```python
# domain/workspace.py
class WorkspaceStatus(str, Enum):
    PENDING = "PENDING"    # Level 0
    COLD = "COLD"          # Level 10
    WARM = "WARM"          # Level 20
    RUNNING = "RUNNING"    # Level 30
    ERROR = "ERROR"
    DELETED = "DELETED"

class WorkspaceOperation(str, Enum):
    NONE = "NONE"
    INITIALIZING = "INITIALIZING"   # PENDING → COLD
    RESTORING = "RESTORING"         # COLD → WARM
    STARTING = "STARTING"           # WARM → RUNNING
    STOPPING = "STOPPING"           # RUNNING → WARM
    ARCHIVING = "ARCHIVING"         # WARM → COLD
    DELETING = "DELETING"           # * → DELETED

# algorithms/reconcile.py
def determine_action(
    status: WorkspaceStatus,
    operation: WorkspaceOperation,
    desired_state: WorkspaceStatus
) -> ReconcileAction:
    """순수 함수 - 현재 상태와 목표를 비교해 다음 액션 결정"""
    if operation != WorkspaceOperation.NONE:
        return ReconcileAction("wait")
    if status == desired_state:
        return ReconcileAction("done")
    if status.level < desired_state.level:
        return ReconcileAction("step_up", get_step_up_operation(status))
    return ReconcileAction("step_down", get_step_down_operation(status))
```

### Control 패키지 (`packages/control/`)

Reconciler 엔진, API, Proxy 구현. Core의 알고리즘과 인터페이스를 사용.

```
src/codehub_control/
├── reconciler/
│   ├── engine.py          # ReconcilerEngine
│   └── worker.py          # 폴링 루프
├── api/
│   └── routes/            # 선언형 REST API
├── proxy/
│   └── autowake.py        # Auto-wake 트리거
└── db/
    └── models.py          # SQLModel (Core Enum 사용)
```

핵심 코드 예시:

```python
# reconciler/engine.py
class ReconcilerEngine:
    def __init__(
        self,
        instance: InstanceController,  # 인터페이스만 앎
        storage: StorageProvider,
        repository: WorkspaceRepository,
    ):
        self._instance = instance
        self._storage = storage
        self._repo = repository

    async def reconcile_one(self, workspace_id: str) -> None:
        workspace = await self._repo.get(workspace_id)

        # Core의 순수 함수 호출
        action = determine_action(
            workspace.status,
            workspace.operation,
            workspace.desired_state,
        )

        if action.type == "step_up":
            await self._execute_step_up(workspace, action.operation)
```

### Adapters 패키지 (`packages/adapters/`)

실제 인프라 구현체. Core의 인터페이스를 구현.

```
src/codehub_adapters/
├── instance/
│   ├── docker.py          # DockerInstanceController
│   └── kubernetes.py      # K8sInstanceController (M3)
├── storage/
│   ├── docker_volume.py   # DockerVolumeProvider
│   └── minio.py           # MinIOStorageProvider
└── events/
    └── redis.py           # RedisEventPublisher
```

핵심 코드 예시:

```python
# instance/docker.py
class DockerInstanceController(InstanceController):
    def __init__(self, docker_host: str | None = None):
        self._client = docker.from_env()

    async def start(self, workspace_id: str, image: str, volume: str) -> None:
        self._client.containers.run(
            image,
            name=f"ws-{workspace_id}",
            volumes={volume: {"bind": "/home/coder"}},
            detach=True,
        )
```

### Backend 앱 (`apps/backend/`)

패키지 조립 및 설정.

```python
# di.py
from codehub_control.reconciler import ReconcilerEngine
from codehub_adapters.instance.docker import DockerInstanceController
from codehub_adapters.storage.minio import MinIOStorageProvider

def create_reconciler(settings: Settings) -> ReconcilerEngine:
    return ReconcilerEngine(
        instance=DockerInstanceController(settings.docker_host),
        storage=MinIOStorageProvider(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        ),
        repository=PostgresRepository(settings.database_url),
    )
```

## 개발 순서

### Phase 1: Core 개발

순수 함수와 인터페이스 먼저 개발. 외부 의존성 없이 테스트 가능.

```
1. domain/workspace.py
   - WorkspaceStatus, WorkspaceOperation Enum
   - 레벨 정의

2. algorithms/transitions.py
   - get_step_up_operation()
   - get_step_down_operation()
   - get_next_status()

3. algorithms/reconcile.py
   - determine_action()

4. interfaces/*.py
   - InstanceController ABC
   - StorageProvider ABC
   - EventPublisher ABC
```

테스트: `pytest` (외부 의존성 없음, 빠름)

```python
def test_step_up_from_cold():
    action = determine_action(
        status=WorkspaceStatus.COLD,
        operation=WorkspaceOperation.NONE,
        desired_state=WorkspaceStatus.RUNNING,
    )
    assert action.type == "step_up"
    assert action.operation == WorkspaceOperation.RESTORING
```

### Phase 2: Control 개발 (Mock 사용)

Mock 구현체로 Reconciler 전체 플로우 테스트.

```
1. tests/mocks/*.py
   - MockInstanceController
   - MockStorageProvider

2. reconciler/engine.py
   - ReconcilerEngine

3. api/routes/*.py
   - 선언형 API

4. proxy/autowake.py
   - Auto-wake 트리거
```

테스트: `pytest` + Mock (Docker 없이 Reconciler 테스트)

```python
class MockInstanceController(InstanceController):
    def __init__(self):
        self.started = []
        self.stopped = []

    async def start(self, workspace_id, image, volume):
        self.started.append(workspace_id)

async def test_reconciler_starts_container():
    mock_instance = MockInstanceController()
    engine = ReconcilerEngine(mock_instance, mock_storage, mock_repo)

    await engine.reconcile_one("ws-1")

    assert "ws-1" in mock_instance.started
```

### Phase 3: Adapters 개발 (실제 인프라)

실제 Docker, MinIO 연결.

```
1. instance/docker.py
2. storage/docker_volume.py
3. storage/minio.py
```

테스트: `pytest` + TestContainers 또는 `docker-compose.test.yml`

### Phase 4: 통합

패키지 조립 및 E2E 테스트.

```
1. apps/backend/di.py
2. E2E 테스트
```

테스트: `docker-compose`로 전체 환경 테스트

## 테스트 피라미드

```
                    /\
                   /  \
                  / E2E \           Phase 4 (5%)
                 /  (5%) \
                /──────────\
               / Integration\       Phase 3 (15%)
              /    (15%)     \
             /────────────────\
            /   Unit (Mock)    \    Phase 2 (30%)
           /      (30%)         \
          /──────────────────────\
         /     Unit (Pure)        \  Phase 1 (50%)
        /        (50%)             \
       /────────────────────────────\
```

## 장점

### 테스트 용이성

Core는 순수 함수로 외부 의존성 없이 테스트 가능. Control은 Mock으로 인프라 없이 Reconciler 테스트 가능. 실제 인프라 테스트는 Adapters에서만 필요.

### 개발 순서 명확화

Core 먼저 완성하고, Control을 Mock으로 테스트하고, Adapters를 마지막에 연결. 90%의 로직을 인프라 없이 검증 가능.

### 구현체 교체 용이

Docker에서 Kubernetes로 전환할 때 Adapters만 수정. Control은 인터페이스만 알므로 영향 없음.

### 관심사 분리

- Core: "무엇을 해야 하는가" (상태 머신 규칙)
- Control: "어떻게 조율하는가" (Reconciler 엔진)
- Adapters: "실제로 어떻게 실행하는가" (Docker API 호출)

## 단점

### 초기 설정 복잡도

패키지 간 의존성 설정이 필요하다. pyproject.toml을 여러 개 관리해야 한다.

### 코드 이동 비용

기존 M1 코드를 새 구조로 마이그레이션해야 한다.

### 인터페이스 변경 시 파급효과

Core 인터페이스가 변경되면 Control과 Adapters 모두 수정해야 한다. 초기에 인터페이스를 신중하게 설계해야 한다.

## 대안 (선택하지 않음)

### 단일 패키지 유지

미선택 이유: 테스트 시 항상 인프라가 필요하고, 순수 로직과 인프라 코드가 혼재되어 관심사 분리가 어렵다.

### 2계층 분리 (Core+Control / Ops)

미선택 이유: Reconciler 알고리즘(순수 함수)과 Reconciler 엔진(인프라 호출)이 같은 패키지에 있으면 여전히 테스트 시 Mock 설정이 복잡하다.

### 멀티 레포로 시작

미선택 이유: 초기 개발 시 인터페이스가 자주 변경되는데, 멀티 레포면 버전 동기화가 복잡하다. 단일 레포에서 패키지 분리로 시작하고, 안정화 후 멀티 레포로 전환하는 것이 현실적이다.

## 관련 ADR

- ADR-006: Reconciler 패턴
- ADR-007: Reconciler 구현
- ADR-008: Ordered State Machine
- ADR-009: Status/Operation 분리
