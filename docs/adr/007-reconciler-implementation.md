# ADR-007: Reconciler 구현 전략

## 상태
Proposed

## 컨텍스트

### 배경
- ADR-006에서 선언적 Reconciler 패턴 채택 결정
- 멀티 워커 환경에서 단일 Reconciler 실행 필요
- 즉시 반응성과 안정성 균형 필요

### 요구사항
- 크래시 시 즉시 복구 (리더 교체)
- 폴링 기반이지만 빠른 반응 지원
- 인프라 과부하 방지
- 중복 처리 방지

## 결정

### Leader Election: PostgreSQL Advisory Lock

| 방식 | 선택 이유 |
|------|----------|
| **PostgreSQL Advisory Lock** | 크래시 시 즉시 해제, 추가 인프라 불필요 |

- `pg_try_advisory_lock(lock_id)` 사용
- 프로세스 크래시 → 세션 종료 → 락 즉시 해제
- 다른 워커가 즉시 리더 획득 가능

### Hints: Redis Pub/Sub

| 항목 | 결정 |
|------|------|
| **채널** | `reconciler:hints` |
| **메시지** | `workspace_id` |
| **유실** | OK (폴링 fallback) |

- API가 desired_state 변경 시 hint 발행
- Reconciler가 즉시 wake-up
- 유실되어도 다음 폴링 주기에 처리

### 폴링 주기

| 항목 | 값 | 이유 |
|------|-----|------|
| **기본 주기** | 30초 | hints가 있으므로 여유 있게 |
| **최소 간격** | 1초 | 과부하 방지 |

### 동시성 제한

| 항목 | 결정 |
|------|------|
| **Semaphore** | 최대 10개 워크스페이스 동시 처리 |
| **워크스페이스별** | 직렬 처리 (ADR-006) |

### 중복 처리 방지

3중 방어:
1. **Queue 중복 제거**: Set 기반, 같은 workspace_id 중복 불가
2. **Dirty Flag**: 처리 중 변경 시 재큐잉
3. **CAS 검증**: `WHERE status IN (...)` 조건부 업데이트

## 결과

### 장점
- 크래시 복구 즉시 (Advisory Lock)
- 빠른 반응 (Redis Hints)
- 인프라 과부하 방지 (Semaphore)
- 중복 처리 방지 (3중 방어)

### 단점
- Redis 의존성 (hints용, 이미 ADR-005에서 도입)
- 구현 복잡도 증가

### 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| Consul/etcd Leader Election | 추가 인프라 필요 |
| DB 폴링만 (hints 없음) | 반응 지연 (폴링 주기만큼) |
| 무제한 동시 처리 | 인프라 과부하 위험 |

## 참고 자료
- [PostgreSQL Advisory Locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)
- [Kubernetes Workqueue](https://pkg.go.dev/k8s.io/client-go/util/workqueue)
