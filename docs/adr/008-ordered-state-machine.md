# ADR-008: Ordered State Machine 패턴 채택

## 상태
Proposed

## 컨텍스트

### 배경
- M2에서 WARM/COLD 상태 추가로 상태 모델 확장 필요
- Reconciler 패턴 (ADR-006) 도입으로 상태 전환 로직 설계 필요
- 다양한 상태 간 전환 경로 관리 복잡도 증가

### 기존 모델 문제점 (직접 전환 방식)

모든 상태 쌍에 대해 전환 로직을 개별 구현하는 방식:

```
상태 A ───────────→ 상태 B
      직접 전환
```

| 문제 | 설명 |
|------|------|
| **조합 폭발** | N개 상태 → N×(N-1) 전환 조합 |
| **코드 중복** | 유사한 동작이 여러 전환에 반복 |
| **중간 실패** | 복합 전환 중 실패 시 상태 불일치 |
| **확장 어려움** | 상태 1개 추가 시 2×(N-1) 조합 추가 |

예시 (4개 상태):
```python
# 직접 전환: 12개 조합 (4×3)
if current == "A" and target == "B": ...
if current == "A" and target == "C": ...
if current == "A" and target == "D": ...
if current == "B" and target == "A": ...
# ... 8개 더
```

### 요구사항
- 상태 전환 로직 단순화
- 중간 실패 시 안전한 복구
- 새로운 상태 추가 용이
- Reconciler와 자연스러운 통합

## 결정

### Ordered State Machine 패턴 채택

상태에 순서(레벨)를 부여하고, 인접 상태로만 전환하는 방식:

```
레벨:    0         1         2         3
       PENDING → COLD → WARM → RUNNING
               ←      ←      ←
```

### 핵심 원칙

1. **순서 기반 전환**: 상태는 정수 레벨을 가짐
2. **인접 전환만 허용**: 한 번에 한 칸씩만 이동
3. **방향 판단 단순화**: `target_level - current_level`로 방향 결정
4. **순차 실행**: 목표까지 step-by-step 이동

### 상태 정의

#### 안정 상태 (Stable States)
Reconciler의 `desired_state`로 설정 가능한 상태

| 상태 | 레벨 | Container | Volume | Object Storage | 설명 |
|------|------|-----------|--------|----------------|------|
| PENDING | 0 | - | - | - | 최초 생성, 리소스 없음 |
| COLD | 1 | - | - | ✅ (또는 없음) | 아카이브됨 |
| WARM | 2 | - | ✅ | - | Volume만 존재 |
| RUNNING | 3 | ✅ | ✅ | - | 실행 중 |

#### 전이 상태 (Transitional States)
전환 진행 중을 나타내는 상태

| 상태 | 전환 | 설명 |
|------|------|------|
| INITIALIZING | PENDING → COLD | 최초 리소스 준비 |
| RESTORING | COLD → WARM | 아카이브에서 복원 중 |
| STARTING | WARM → RUNNING | 컨테이너 시작 중 |
| STOPPING | RUNNING → WARM | 컨테이너 정지 중 |
| ARCHIVING | WARM → COLD | 아카이브 생성 중 |
| DELETING | * → DELETED | 삭제 진행 중 |

#### 최종/예외 상태
| 상태 | 설명 |
|------|------|
| DELETED | 소프트 삭제됨 |
| ERROR | 오류 발생, 복구 필요 |

### 전환 알고리즘

```python
STATE_ORDER = ["PENDING", "COLD", "WARM", "RUNNING"]

async def reconcile(workspace):
    current_idx = STATE_ORDER.index(workspace.status)
    target_idx = STATE_ORDER.index(workspace.desired_state)

    while current_idx != target_idx:
        if current_idx < target_idx:
            await step_up(workspace)   # 활성화 방향
            current_idx += 1
        else:
            await step_down(workspace) # 비활성화 방향
            current_idx -= 1

async def step_up(workspace):
    """한 단계 위로 (활성화)"""
    match workspace.status:
        case "PENDING":
            # PENDING → COLD: 초기화
            workspace.status = "COLD"
        case "COLD":
            # COLD → WARM: restore 또는 provision
            if workspace.archive_key:
                await storage.restore(...)
            else:
                await storage.provision(...)
            workspace.status = "WARM"
        case "WARM":
            # WARM → RUNNING: 컨테이너 시작
            await instance.start(...)
            workspace.status = "RUNNING"

async def step_down(workspace):
    """한 단계 아래로 (비활성화)"""
    match workspace.status:
        case "RUNNING":
            # RUNNING → WARM: 컨테이너 정지
            await instance.stop(...)
            workspace.status = "WARM"
        case "WARM":
            # WARM → COLD: 아카이브
            await storage.archive(...)
            workspace.status = "COLD"
        case "COLD":
            # COLD → PENDING: (일반적으로 사용 안 함)
            workspace.status = "PENDING"
```

### 상태 다이어그램

```
                 step_up()                    step_up()                   step_up()
    PENDING ─────────────→ COLD ─────────────→ WARM ─────────────→ RUNNING
       0                     1                   2                     3
                ←─────────────     ←─────────────     ←─────────────
                 step_down()        step_down()        step_down()

                                    │
                                    ▼ (어디서든)
                                 DELETING → DELETED

                                 ERROR (복구 필요)
```

## 결과

### 장점

| 장점 | 설명 |
|------|------|
| **복잡도 선형화** | N개 상태 → 2×(N-1) 전환 함수 (vs N×(N-1)) |
| **코드 재사용** | 각 동작이 정확히 한 곳에만 존재 |
| **중간 실패 안전** | 실패 시 현재 상태에서 멈춤, 다음 reconcile에서 재시도 |
| **확장 용이** | 새 상태 추가 시 2개 함수만 추가 |
| **디버깅 용이** | 단계별 상태 추적 가능 |
| **테스트 단순화** | step_up/step_down 각각 단위 테스트 |

### 단점

| 단점 | 설명 | 대응 |
|------|------|------|
| **건너뛰기 불가** | COLD→RUNNING 직접 전환 불가 | 순차 실행으로 자연스럽게 처리 |
| **전환 시간 증가** | 여러 단계 거쳐야 함 | 각 단계가 빠르면 무시 가능 |
| **중간 상태 노출** | COLD→RUNNING 중 WARM 상태 노출 | 전이 상태로 표현 |

### 업계 사례

| 시스템 | 상태 모델 | 패턴 |
|--------|----------|------|
| **Kubernetes** | Pending → Running → Succeeded/Failed | Ordered |
| **Gitpod** | Pending → Creating → Initializing → Running → Stopping → Stopped | Ordered |
| **AWS EC2** | pending → running → stopping → stopped → terminated | Ordered |
| **Docker** | created → running → paused → exited | Ordered |

## 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| **직접 전환** | 조합 폭발, 중간 실패 복구 복잡 |
| **이벤트 기반 FSM** | 이벤트 유실 시 상태 불일치 |
| **그래프 기반 FSM** | 복잡한 경로 탐색 필요 |

## 참고 자료
- [Kubernetes Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)
- [Finite State Machine](https://en.wikipedia.org/wiki/Finite-state_machine)
- [Gitpod Workspace Phases](https://github.com/gitpod-io/gitpod/blob/main/components/ws-manager-api/go/crd/v1/workspace_types.go)
