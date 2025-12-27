# 패키지 분리 설계

> Core / Control / Adapters 3계층 분리의 개념과 원칙

---

## 1. 분리 원칙

### 1.1 순수 함수 vs 부작용

코드는 두 종류로 나눌 수 있다:

| 종류 | 특징 | 예시 |
|------|------|------|
| **순수 함수** | 같은 입력 → 항상 같은 출력, 외부 영향 없음 | `1 + 1 = 2` |
| **부작용** | 외부 상태를 변경하거나 의존함 | DB 저장, API 호출 |

```python
# 순수 함수 (Core)
def determine_action(status, operation, desired):
    if status.level < desired.level:
        return "step_up"
    # 항상 같은 입력 → 같은 출력
    # DB 안 봄, Docker 안 봄, 아무것도 안 함

# 부작용 (Adapters)
async def start_container(workspace_id):
    docker.containers.run(...)  # 외부 상태 변경!
```

**분리 원칙**: 순수 함수는 Core로, 부작용은 Adapters로 분리한다.

---

### 1.2 의존성 역전 원칙 (DIP)

```
잘못된 방향:
  Control → DockerInstanceController (구체 클래스)

  문제: Docker를 K8s로 바꾸면 Control도 수정해야 함

올바른 방향:
  Control → InstanceController (인터페이스)
  DockerInstanceController → InstanceController (구현)

  장점: Docker를 K8s로 바꿔도 Control은 그대로
```

```
┌─────────────────────────────────────────────────────────────┐
│                        Control                              │
│                                                             │
│   engine = ReconcilerEngine(instance, storage)              │
│   await engine._instance.start(...)                         │
│                                                             │
│   # Control은 "instance"가 Docker인지 K8s인지 모름          │
│   # 그냥 start() 메서드가 있다는 것만 앎                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ 인터페이스만 의존
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Core (인터페이스 정의)                     │
│                                                             │
│   class InstanceController(ABC):                            │
│       @abstractmethod                                       │
│       async def start(self, workspace_id, image, volume)    │
│       @abstractmethod                                       │
│       async def stop(self, workspace_id)                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ 인터페이스 구현
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Adapters (구현체)                         │
│                                                             │
│   class DockerInstanceController(InstanceController):       │
│       async def start(self, workspace_id, image, volume):   │
│           self._client.containers.run(...)                  │
│                                                             │
│   class K8sInstanceController(InstanceController):          │
│       async def start(self, workspace_id, image, volume):   │
│           self._api.create_pod(...)                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### 1.3 테스트 피라미드 철학

```
                    /\
                   /  \
                  / E2E \           느림, 불안정, 비쌈
                 /  (5%) \          → 최소한만
                /──────────\
               / Integration\       실제 인프라 필요
              /    (15%)     \      → 핵심 경로만
             /────────────────\
            /   Unit (Mock)    \    빠름, 안정
           /      (30%)         \   → 로직 검증
          /──────────────────────\
         /     Unit (Pure)        \  가장 빠름, 가장 안정
        /        (50%)             \ → 규칙 검증
       /────────────────────────────\
```

**목표**: 테스트의 50%+는 인프라 없이 실행 가능해야 한다.

---

## 2. 각 레이어 테스트 개념

### 2.1 Core 테스트: "규칙이 맞는가?"

**검증 대상**: 상태 머신 규칙, 전환 로직, 레벨 비교

**특징**:
- 입력값만 주면 출력값 검증
- DB 없음, Docker 없음, 네트워크 없음
- 0.1초 만에 수백 개 테스트 가능

```python
# 테스트: "WARM에서 RUNNING으로 가려면 STARTING이 필요한가?"

def test_warm_to_running_requires_starting():
    action = determine_action(
        status=WorkspaceStatus.WARM,
        operation=WorkspaceOperation.NONE,
        desired_state=WorkspaceStatus.RUNNING,
    )

    assert action.type == "step_up"
    assert action.operation == WorkspaceOperation.STARTING
```

```python
# 테스트: "작업 진행 중이면 대기하는가?"

def test_wait_when_operation_in_progress():
    action = determine_action(
        status=WorkspaceStatus.WARM,
        operation=WorkspaceOperation.STARTING,  # 진행 중!
        desired_state=WorkspaceStatus.RUNNING,
    )

    assert action.type == "wait"  # 대기해야 함
```

**테스트가 검증하는 것**:
- 상태 전환 규칙이 spec과 일치하는가?
- 레벨 비교가 올바른가?
- 예외 상태(ERROR, DELETED) 처리가 맞는가?

---

### 2.2 Control 테스트: "조율이 맞는가?"

**검증 대상**: Reconciler가 올바른 순서로 올바른 메서드를 호출하는가

**특징**:
- Mock 구현체 사용 (가짜 Docker, 가짜 Storage)
- DB는 In-Memory로 대체 가능
- 1초 만에 전체 플로우 테스트 가능

```python
# Mock 구현체: 실제로 아무것도 안 하고 기록만 함

class MockInstanceController(InstanceController):
    def __init__(self):
        self.calls = []  # 호출 기록

    async def start(self, workspace_id, image, volume):
        self.calls.append(("start", workspace_id))

    async def stop(self, workspace_id):
        self.calls.append(("stop", workspace_id))
```

```python
# 테스트: "Reconciler가 step_up 시 start를 호출하는가?"

async def test_reconciler_calls_start_on_step_up():
    # Arrange: Mock 설정
    mock_instance = MockInstanceController()
    mock_storage = MockStorageProvider()
    repo = InMemoryRepository()

    engine = ReconcilerEngine(mock_instance, mock_storage, repo)

    # 테스트 데이터: WARM → RUNNING
    workspace = Workspace(
        id="ws-1",
        status=WorkspaceStatus.WARM,
        operation=WorkspaceOperation.NONE,
        desired_state=WorkspaceStatus.RUNNING,
    )
    await repo.save(workspace)

    # Act: Reconciler 실행
    await engine.reconcile_one("ws-1")

    # Assert: start가 호출되었는가?
    assert ("start", "ws-1") in mock_instance.calls
```

```python
# 테스트: "Reconciler가 step_down 시 stop을 호출하는가?"

async def test_reconciler_calls_stop_on_step_down():
    mock_instance = MockInstanceController()
    engine = ReconcilerEngine(mock_instance, mock_storage, repo)

    # RUNNING → WARM
    workspace = Workspace(
        id="ws-1",
        status=WorkspaceStatus.RUNNING,
        desired_state=WorkspaceStatus.WARM,
    )
    await repo.save(workspace)

    await engine.reconcile_one("ws-1")

    assert ("stop", "ws-1") in mock_instance.calls
```

**테스트가 검증하는 것**:
- Core 함수의 결과에 따라 올바른 인터페이스를 호출하는가?
- 상태 업데이트가 올바른 순서로 되는가?
- 에러 발생 시 롤백이 되는가?

---

### 2.3 Adapters 테스트: "실제로 동작하는가?"

**검증 대상**: 인터페이스 구현체가 실제 인프라를 올바르게 제어하는가

**특징**:
- 실제 Docker, MinIO 필요
- TestContainers 또는 docker-compose 사용
- 느림 (10초+), 하지만 필수

```python
# 테스트: "DockerInstanceController가 실제로 컨테이너를 만드는가?"

@pytest.fixture
def docker_controller():
    return DockerInstanceController()

@pytest.fixture
def cleanup(docker_controller):
    yield
    # 테스트 후 정리
    docker_controller.delete("test-ws")

async def test_docker_creates_real_container(docker_controller, cleanup):
    # Act: 실제 Docker 컨테이너 생성
    await docker_controller.start(
        workspace_id="test-ws",
        image="alpine:latest",
        volume="/tmp/test",
    )

    # Assert: 실제로 컨테이너가 있는가?
    client = docker.from_env()
    container = client.containers.get("ws-test-ws")

    assert container.status == "running"
```

```python
# 테스트: "MinIOStorageProvider가 실제로 파일을 저장하는가?"

async def test_minio_uploads_archive(minio_provider):
    # Act: 실제 MinIO에 업로드
    archive_key = await minio_provider.archive("volume-123")

    # Assert: 실제로 파일이 있는가?
    objects = list(minio_provider._client.list_objects("archives"))
    assert any(obj.object_name == archive_key for obj in objects)
```

**테스트가 검증하는 것**:
- Docker API 호출이 올바른가?
- MinIO 업로드/다운로드가 동작하는가?
- 에러 처리가 올바른가?

---

## 3. 레이어 간 계약

### 3.1 Core ↔ Control 계약

```
┌─────────────────────────────────────────────────────────────┐
│                        Control                              │
│                                                             │
│   # 1. DB에서 "값"을 읽음                                    │
│   workspace = await repo.get("ws-1")                        │
│                                                             │
│   # 2. Core에게 "값"만 전달 (DB 객체 X, Enum 값만)           │
│   action = determine_action(                                │
│       status=workspace.status,        # Enum 값             │
│       operation=workspace.operation,  # Enum 값             │
│       desired_state=workspace.desired_state,  # Enum 값     │
│   )                                                         │
│                                                             │
│   # 3. Core가 반환한 "액션"에 따라 처리                      │
│   if action.type == "step_up":                              │
│       await self._execute_step_up(workspace, action)        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ 값만 전달
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                         Core                                │
│                                                             │
│   def determine_action(status, operation, desired):         │
│       # DB 모름, ORM 모름                                   │
│       # 그냥 Enum 값만 비교                                  │
│       if operation != NONE:                                 │
│           return ReconcileAction("wait")                    │
│       if status.level < desired.level:                      │
│           return ReconcileAction("step_up", ...)            │
│       ...                                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**계약**:
- Control은 Core에게 **Enum 값**만 전달한다
- Core는 **ReconcileAction**을 반환한다
- Core는 DB, 네트워크, 파일시스템을 절대 사용하지 않는다

---

### 3.2 Control ↔ Adapters 계약

```
┌─────────────────────────────────────────────────────────────┐
│                        Control                              │
│                                                             │
│   # 인터페이스만 알고 있음                                   │
│   self._instance: InstanceController                        │
│   self._storage: StorageProvider                            │
│                                                             │
│   # 인터페이스 메서드만 호출                                 │
│   await self._instance.start(workspace_id, image, volume)   │
│   await self._storage.archive(volume_key)                   │
│                                                             │
│   # Docker인지 K8s인지 모름!                                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ 인터페이스 호출
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        Adapters                              │
│                                                             │
│   class DockerInstanceController(InstanceController):       │
│       async def start(self, workspace_id, image, volume):   │
│           # 실제 Docker API 호출                            │
│           self._client.containers.run(...)                  │
│                                                             │
│   class K8sInstanceController(InstanceController):          │
│       async def start(self, workspace_id, image, volume):   │
│           # 실제 K8s API 호출                               │
│           self._api.create_namespaced_pod(...)              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**계약**:
- Control은 **인터페이스 메서드**만 호출한다
- Adapters는 **인터페이스를 구현**한다
- Control은 구현 세부사항을 알 필요 없다

---

### 3.3 조립: Backend에서 연결

```python
# apps/backend/di.py

from codehub_core.interfaces import InstanceController, StorageProvider
from codehub_control.reconciler import ReconcilerEngine
from codehub_adapters.instance.docker import DockerInstanceController
from codehub_adapters.storage.minio import MinIOStorageProvider

def create_reconciler(settings: Settings) -> ReconcilerEngine:
    # 여기서 구현체를 선택해서 주입
    instance: InstanceController = DockerInstanceController(
        docker_host=settings.docker_host,
    )

    storage: StorageProvider = MinIOStorageProvider(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )

    return ReconcilerEngine(
        instance=instance,  # 인터페이스 타입으로 전달
        storage=storage,
    )
```

```python
# M3에서 K8s로 전환할 때

def create_reconciler(settings: Settings) -> ReconcilerEngine:
    # Docker → K8s로 변경
    instance: InstanceController = K8sInstanceController(
        kubeconfig=settings.kubeconfig,
    )

    # Control 코드는 변경 없음!
    return ReconcilerEngine(instance=instance, storage=storage)
```

---

## 4. 개발 흐름 예시

### 4.1 새로운 기능: "SNAPSHOT 상태 추가"

```
Step 1: Core 수정 (순수 함수)
  - WorkspaceStatus에 SNAPSHOT 추가
  - 전환 규칙 추가 (WARM → SNAPSHOT → COLD)
  - 테스트: 순수 단위 테스트로 규칙 검증

Step 2: Control 수정 (조율 로직)
  - Reconciler에 SNAPSHOTTING 처리 추가
  - 테스트: Mock으로 호출 순서 검증

Step 3: Adapters 수정 (구현체)
  - StorageProvider에 snapshot() 메서드 추가
  - MinIOStorageProvider에 구현
  - 테스트: 실제 MinIO로 스냅샷 생성 검증

Step 4: 통합 테스트
  - E2E로 전체 플로우 검증
```

---

### 4.2 구현체 교체: "Docker → K8s"

```
Step 1: Adapters에 K8sInstanceController 추가
  - Core, Control 변경 없음!

Step 2: K8sInstanceController 테스트
  - 실제 K8s 클러스터에서 테스트

Step 3: Backend에서 DI 설정 변경
  - DockerInstanceController → K8sInstanceController

Step 4: 기존 테스트 실행
  - Core 테스트: 통과 (변경 없음)
  - Control 테스트: 통과 (Mock 사용)
  - E2E 테스트: K8s 환경에서 실행
```

---

## 5. 요약

| 레이어 | 역할 | 테스트 방법 | 검증 대상 |
|--------|------|------------|----------|
| **Core** | 규칙 정의 | 순수 단위 테스트 | 상태 전환 규칙이 맞는가? |
| **Control** | 조율/실행 | Mock + In-Memory | 올바른 순서로 호출하는가? |
| **Adapters** | 실제 동작 | 실제 인프라 | 인프라가 동작하는가? |
| **Backend** | 조립 | E2E | 전체가 동작하는가? |

```
Core의 질문: "WARM에서 RUNNING으로 가려면 뭘 해야 해?"
Core의 대답: "STARTING 작업을 해"

Control의 질문: "STARTING이래, 누구한테 시켜?"
Control의 대답: "instance.start()를 호출해"

Adapters의 질문: "start()가 뭔데?"
Adapters의 대답: "docker run을 실행해"
```

---

## 참조

- [ADR-010: 패키지 분리 아키텍처](../adr/010-package-separation.md)
- [states.md](./states.md) - 상태 다이어그램
- [spec_v2/states.md](../spec_v2/states.md) - 상태 스펙
