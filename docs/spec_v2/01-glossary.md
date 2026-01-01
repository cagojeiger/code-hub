# 용어집 (Glossary)

> [README.md](./README.md)로 돌아가기

---

## Level-Triggered vs Edge-Triggered

### Level-Triggered Reconciliation ✅ (본 시스템)

이벤트(Edge)가 아니라 **현재 상태(Level)를 주기적으로 관찰**해서 desired state로 수렴시키는 조정 루프.

| 항목 | 설명 |
|------|------|
| 핵심 | desired vs observed 비교 → 차이가 있으면 action 실행 |
| 장점 | 이벤트를 놓쳐도 다음 reconcile에서 복구 (자기 치유) |
| 단점 | 폴링 오버헤드, 반응 지연 |
| 예시 | Kubernetes Controller, Terraform |

### Edge-Triggered

**이벤트 발생 시점에만** 반응하는 방식.

| 항목 | 설명 |
|------|------|
| 핵심 | 상태 변화(edge)가 감지되면 즉시 처리 |
| 장점 | 즉각 반응, 리소스 효율적 |
| 단점 | 이벤트 유실 시 불일치 상태 지속 |
| 예시 | Webhook, Message Queue Consumer |

> 참조: [Kubernetes - Writing a Controller](https://kubernetes.io/blog/2021/06/21/writing-a-controller-for-pod-labels/)

---

## Optimistic Locking vs Pessimistic Locking

### Optimistic Locking ✅ (본 시스템)

충돌은 드물다고 가정하고 **락을 오래 잡지 않고**, 커밋 직전에 **버전 검사(CAS)**로 충돌을 감지.

| 구현 방식 | 설명 |
|----------|------|
| DB 버전 컬럼 | `UPDATE ... SET version=N+1 WHERE version=N` |
| CAS | 영향 행=0이면 경합 발생 → 재시도 |

| 항목 | 설명 |
|------|------|
| 장점 | 락 대기 없음, 읽기 많은 워크로드에 유리 |
| 단점 | 충돌 시 재시도 필요, 충돌 많으면 비효율 |

### Pessimistic Locking

충돌이 **자주 발생한다**고 가정하고 **미리 락을 획득**해서 독점.

| 구현 방식 | 설명 |
|----------|------|
| SELECT FOR UPDATE | 행 레벨 락 |
| Advisory Lock | 명시적 락 |

| 항목 | 설명 |
|------|------|
| 장점 | 충돌 자체를 방지, 재시도 불필요 |
| 단점 | 락 대기로 인한 지연, 데드락 위험 |

> 참조: [Wikipedia - Optimistic Concurrency Control](https://en.wikipedia.org/wiki/Optimistic_concurrency_control)

---

## CAS (Compare-And-Swap)

**원자적(atomic)**으로 "현재 값이 예상 값과 같으면 새 값으로 교체"하는 연산.

### 동작 원리

```
CAS(메모리주소, 예상값, 새값):
    if *메모리주소 == 예상값:
        *메모리주소 = 새값
        return true   # 성공
    else:
        return false  # 실패 (다른 스레드가 먼저 변경)
```

> 위 전체가 **하나의 원자적 연산**으로 실행됨 (중간에 끼어들기 불가)

### 구현 레벨별 예시

| 레벨 | 구현 |
|------|------|
| CPU 명령어 | CMPXCHG (x86), LDREX/STREX (ARM), LL/SC (MIPS) |
| 언어 런타임 | Java `AtomicInteger.compareAndSet()`, Go `atomic.CompareAndSwapInt64()` |
| DB | `UPDATE ... SET x=new WHERE x=old` (영향 행 0이면 실패) |
| 분산 시스템 | etcd/Consul Check-And-Set, Redis `WATCH`+`MULTI` |

### Optimistic Locking과의 관계

| 개념 | 레벨 | 설명 |
|------|------|------|
| CAS | Primitive | 저수준 원자적 연산 |
| Optimistic Locking | Pattern | CAS를 활용한 동시성 제어 패턴 |

> **CAS는 Optimistic Locking의 구현 수단**

### 본 시스템에서의 사용 ✅

| 위치 | 패턴 |
|------|------|
| OperationController | `UPDATE ... SET operation='STARTING' WHERE operation='NONE'` |

### ABA 문제

CAS의 알려진 한계점.

```
1. 스레드A: 값 읽음 (A)
2. 스레드B: A → B → A 변경
3. 스레드A: CAS 성공 (값이 여전히 A라서)
```

| 해결책 | 설명 |
|--------|------|
| 버전 번호 | 값과 함께 monotonic 버전 관리 |
| Tagged Pointer | 포인터에 카운터 포함 |

> 본 시스템은 **버전 컬럼 미사용**, operation 값 자체로 상태 구분하여 ABA 문제 회피

> 참조: [Wikipedia - Compare-and-swap](https://en.wikipedia.org/wiki/Compare-and-swap)

---

## Idempotent vs Non-Idempotent

### Idempotent (멱등) ✅ (본 시스템 목표)

같은 요청/연산을 **여러 번 실행해도 결과가 한 번 실행한 것과 동일**.

| 예시 | 설명 |
|------|------|
| HTTP PUT/DELETE | 멱등 (여러 번 호출해도 동일 결과) |
| 절대값 설정 | `x = 5` (몇 번 해도 5) |
| 파일 덮어쓰기 | 같은 내용으로 덮어쓰기 |

### Non-Idempotent (비멱등)

실행할 때마다 **결과가 누적되거나 달라짐**.

| 예시 | 설명 |
|------|------|
| HTTP POST | 비멱등 (매번 새 리소스 생성) |
| 증가 연산 | `x = x + 1` (실행마다 증가) |
| 이메일 발송 | 매번 새 이메일 발송 |

> 참조: [Wikipedia - Idempotence](https://en.wikipedia.org/wiki/Idempotence)

---

## Crash-Only vs Graceful Shutdown

### Crash-Only Design ✅ (본 시스템)

장애 시 복구 절차 없이 **그냥 죽이고(crash) 다시 시작(restart)**해도 안전하게 복구되도록 설계.

| 원칙 | 설명 |
|------|------|
| No graceful shutdown | 정상 종료 = 비정상 종료 (구분 없음) |
| Idempotent startup | 시작 시 항상 동일한 상태로 복구 |
| Externalized state | 상태는 외부 저장소에 보관 |

### Graceful Shutdown

종료 전 **정리 작업(cleanup)**을 수행하는 방식.

| 단계 | 설명 |
|------|------|
| 1. 신규 요청 거부 | 새 작업 받지 않음 |
| 2. 진행 중 작업 완료 | drain |
| 3. 리소스 정리 | 커넥션 종료, 파일 닫기 |
| 4. 종료 | clean exit |

| 비교 | Crash-Only | Graceful |
|------|-----------|----------|
| 복잡도 | 낮음 (시작 로직만) | 높음 (시작+종료 로직) |
| 복구 경로 | 단일 (항상 restart) | 이중 (정상/비정상) |
| 위험 | 낮음 (테스트 용이) | 높음 (graceful 버그 가능) |

> 참조: [Crash-Only Software (EPFL)](https://dslab.epfl.ch/pubs/crashonly.pdf)

---

## Single Writer vs Multi Writer

### Single Writer Principle ✅ (본 시스템)

한 데이터의 **쓰기는 오직 하나의 실행 컨텍스트만** 담당.

| 해결책 | 설명 |
|--------|------|
| Writer 단일화 | Actor, 싱글 워커, 단일 컨트롤러 |
| 간접 전달 | 명령/이벤트/큐로 요청 전달 |

### Multi Writer (+ 충돌 해결)

여러 컨텍스트가 동시에 쓰기 가능, **충돌 해결 메커니즘 필요**.

| 전략 | 설명 |
|------|------|
| Last-Write-Wins (LWW) | 마지막 쓰기가 이김 (데이터 유실 가능) |
| CRDT | Conflict-free Replicated Data Types |
| OT | Operational Transformation (Google Docs) |
| 수동 병합 | Git merge conflict |

> 참조: [Mechanical Sympathy - Single Writer Principle](https://mechanical-sympathy.blogspot.com/2011/09/single-writer-principle.html)

---

## Non-preemptive vs Preemptive

### Non-preemptive Operation ✅ (본 시스템)

작업이 시작되면 **중간에 강제로 뺏지 않고** 끝까지 실행 (Run-to-Completion).

| 장점 | 단점 |
|------|------|
| 롤백/부분취소 복잡성 회피 | 잘못 시작하면 끝까지 진행 |
| 상태 일관성 보장 | 사전 검증/가드레일 필요 |
| 구현 단순 | 긴 작업 시 응답성 저하 |

### Preemptive

실행 중인 작업을 **강제로 중단**하고 다른 작업 실행 가능.

| 장점 | 단점 |
|------|------|
| 우선순위 높은 작업 즉시 처리 | 롤백/복구 로직 필요 |
| 응답성 좋음 | 상태 일관성 유지 어려움 |
| 취소 가능 | 구현 복잡 |

> 참조: [Wikipedia - Run-to-completion scheduling](https://en.wikipedia.org/wiki/Run-to-completion_scheduling)

---

## Leader Election

여러 인스턴스 중 **딱 하나만 리더(조정자)**로 뽑아서 작업을 대표 수행.

### Advisory Lock 기반 (PostgreSQL) ✅ (본 시스템)

| 함수 | 설명 |
|------|------|
| `pg_try_advisory_lock(key)` | 성공하면 리더 |
| 세션 종료 | 락 자동 해제 → 다른 인스턴스가 리더 획득 |

### 다른 방식들

| 방식 | 설명 |
|------|------|
| Zookeeper | Ephemeral node + watch |
| etcd | Lease + Campaign |
| Redis | SETNX + TTL |
| Raft | 분산 합의 알고리즘 |

> 참조: [AWS - Leader Election in Distributed Systems](https://aws.amazon.com/builders-library/leader-election-in-distributed-systems/)

---

## Garbage Collection (GC)

더 이상 필요 없는 리소스를 **자동으로 정리(clean up)**하는 메커니즘.

### TTL 기반 GC ✅ (본 시스템)

리소스에 유통기한을 달아두고, 시간이 지나면 삭제 대상으로 만들어 정리.

| 특성 | 설명 |
|------|------|
| 비동기 | 삭제가 지연될 수 있음 |
| 안전 지연 | 진행 중인 작업 보호를 위해 TTL 설정 |

### 다른 GC 방식들

| 방식 | 설명 |
|------|------|
| Reference Counting | 참조 수 0이면 삭제 |
| Mark-and-Sweep | 도달 불가능한 객체 삭제 |
| Owner Reference | 소유자 삭제 시 종속 리소스 삭제 (K8s) |

> 참조: [Kubernetes - Garbage Collection](https://kubernetes.io/docs/concepts/architecture/garbage-collection/)

---

## CDC (Change Data Capture)

데이터 변경을 감지하여 **이벤트로 전파**하는 패턴.

### 핵심 아이디어

| 항목 | 설명 |
|------|------|
| 목적 | 데이터 변경을 다른 시스템에 실시간 전파 |
| 장점 | Writer는 이벤트 발행 로직을 모름 (Single Responsibility) |
| 패턴 | DB 변경 → 감지 → 이벤트 발행 |

### PostgreSQL LISTEN/NOTIFY ✅ (본 시스템)

| 구성 요소 | 역할 |
|----------|------|
| Trigger | UPDATE 시 pg_notify() 호출 |
| LISTEN | 애플리케이션이 채널 구독 |
| NOTIFY | 페이로드와 함께 알림 전송 |

```
Writer → UPDATE → Trigger → pg_notify() → Listener
```

### 다른 CDC 방식들

| 방식 | 설명 |
|------|------|
| WAL (Write-Ahead Log) | 트랜잭션 로그 스트리밍 (Debezium, pg_logical) |
| Polling | 주기적으로 변경 조회 (updated_at 비교) |
| Outbox Pattern | 별도 이벤트 테이블에 기록 후 폴링 |
| Application-level | 애플리케이션 코드에서 직접 발행 |

### 본 시스템 사용처

| 위치 | 용도 |
|------|------|
| workspaces 테이블 | 상태 변경 → SSE 이벤트 전달 |

> 참조: [04-control-plane.md#events](./04-control-plane.md#events) - SSE 이벤트 상세

> 참조: [Wikipedia - Change Data Capture](https://en.wikipedia.org/wiki/Change_data_capture)

---

## Ordered State Machine ✅ (본 시스템)

상태에 **순서(정수값)**를 부여하여 전이 방향을 제한하는 상태 머신.

```
PENDING(0) < ARCHIVED(5) < STANDBY(10) < RUNNING(20)
```

| 규칙 | 설명 |
|------|------|
| Step-up | 낮은 상태 → 높은 상태 (복원/생성/시작) |
| Step-down | 높은 상태 → 낮은 상태 (정지/아카이브) |
| 단일 단계 | 한 번에 한 단계만 전이 |

> **ERROR/DELETING/DELETED**: Ordered SM 미적용 (별도 축)
> **상세**: [02-states.md#state-machine](./02-states.md#state-machine)

### 일반 FSM과 비교

| 항목 | Ordered SM | 일반 FSM |
|------|-----------|---------|
| 전이 방향 | 순서에 따라 제한 | 임의 전이 가능 |
| 복잡도 | 낮음 | 높음 (전이 조합 폭발) |
| 검증 | 쉬움 (순서만 체크) | 어려움 (모든 경로 검증) |

---

## 참조

- [02-states.md](./02-states.md) - 상태 정의
- [04-control-plane.md#events](./04-control-plane.md#events) - SSE 이벤트 (CDC 적용)
- [04-control-plane.md#resourceobserver](./04-control-plane.md#resourceobserver) - Reconciler 구현 (ResourceObserver)
- [04-control-plane.md#coordinator](./04-control-plane.md#coordinator) - Leader Election, EventListener
- [05-data-plane.md#archive-gc](./05-data-plane.md#archive-gc) - GC with TTL
