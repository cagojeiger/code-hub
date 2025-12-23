# ADR-006: 선언적 Reconciler 패턴 채택

## 상태
Proposed

## 컨텍스트

### 배경
- MVP에서 명령형(Imperative) 방식으로 구현
- API 호출 → 즉시 작업 수행 → 결과 반환
- 멀티 워커/K8s 환경 확장 시 상태 관리 복잡도 증가

### 문제점
명령형 방식의 한계:
- 중간 실패 시 상태 불일치 발생 가능
- 외부 변화(수동 컨테이너 삭제 등) 감지 불가
- 복구 로직이 별도로 필요 (현재 startup_recovery)

### 요구사항
- 선언적(Declarative) 아키텍처로 전환
- desired_state 선언 → 시스템이 자동으로 수렴
- 이벤트 유실에도 안정적으로 동작
- K8s Controller 패턴과 철학적 일관성

## 결정

### 명령형에서 선언적으로 전환

| 항목 | 명령형 (Imperative) | 선언적 (Declarative) |
|------|---------------------|---------------------|
| **중심** | 작업 (Action) | 상태 (State) |
| **동작** | "X를 해라" | "X 상태가 되어야 한다" |
| **실행** | API가 직접 수행 | Reconciler가 수렴 |
| **이벤트 유실** | 장애 | 정상 (다음 루프에서 복구) |
| **중복 실행** | 사고 | 정상 (멱등) |
| **외부 변화** | 감지 불가 | 폴링으로 자동 감지 |

### 핵심 철학
> "이벤트에 의존하지 말고, 상태를 끈질기게 맞춘다"

- API는 desired_state만 변경 (선언)
- Reconciler가 주기적으로 실제 상태와 비교
- 차이가 있으면 수렴하도록 조정
- 이벤트는 "힌트"일 뿐, 놓쳐도 다음 루프에서 복구

### 워크스페이스 단위 직렬화

K8s workqueue 패턴 채택:
- **같은 워크스페이스**: 직렬 처리 (충돌 방지)
- **다른 워크스페이스**: 병렬 처리 (처리량 확보)

## 결과

### 장점
- 상태 중심 사고 - 복잡한 상태 전이를 단순화
- 장애 복구 단순화 - 별도 recovery 로직 불필요
- 외부 변화 자동 대응 - 수동 컨테이너 삭제 등 감지
- K8s 철학과 일관성 - 향후 K8s Controller 확장 용이

### 단점
- 즉시 실행 보장 안 됨 (폴링 주기만큼 지연 가능)
- 상태 조회 오버헤드 (주기적 폴링)

### 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| 명령형 + Worker 모델 | 이벤트 유실 시 장애, 외부 변화 감지 불가 |
| 명령형 유지 + 복구 강화 | 복잡도 증가, K8s 패턴과 불일치 |

## 참고 자료
- [Kubernetes Controller Pattern](https://kubernetes.io/docs/concepts/architecture/controller/)
- [Level Triggering and Reconciliation in Kubernetes](https://hackernoon.com/level-triggering-and-reconciliation-in-kubernetes-1f17fe30333d)
