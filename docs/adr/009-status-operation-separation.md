# ADR-009: Status와 Operation 분리

## 상태
Proposed

## 컨텍스트

### 문제 상황

Ordered State Machine(ADR-008)을 적용하면서 전이 상태(transitional states)를 어떻게 표현할지 결정해야 했다.

기존 방식은 단일 status 컬럼에 안정 상태와 전이 상태를 모두 포함:
- 안정 상태: PENDING, COLD, WARM, RUNNING
- 전이 상태: INITIALIZING, RESTORING, STARTING, STOPPING, ARCHIVING, DELETING
- 예외 상태: ERROR, DELETED

총 12개 상태가 단일 컬럼에 혼재되어 있었다.

### 핵심 문제

**레벨 비교의 모호함**: 전이 상태가 어느 레벨에 속하는지 불명확했다.
- STARTING은 WARM 레벨인가, RUNNING 레벨인가?
- STOPPING은 RUNNING 레벨인가, WARM 레벨인가?

Reconciler가 `current_level < target_level`을 비교할 때마다 전이 상태를 안정 상태에 매핑하는 로직이 필요했다.

**의미론적 모순**: 단일 status로는 "현재 어떤 리소스가 존재하는가"와 "현재 어떤 작업이 진행 중인가"를 동시에 표현해야 했다.
- `status = STARTING`일 때 컨테이너가 있는가? → 없다 (아직 시작 중)
- `status = STOPPING`일 때 컨테이너가 있는가? → 있다 (아직 정지 중)
- 같은 "전이 상태"인데 리소스 존재 여부가 다르다

## 결정

**status와 operation을 별도 컬럼으로 분리한다.**

- **status**: 현재 리소스 존재 상태 (안정 상태만)
- **operation**: 진행 중인 작업 (전이 상태)

예시:
- "컨테이너 시작 중" → `status = WARM, operation = STARTING`
- "컨테이너 정지 중" → `status = RUNNING, operation = STOPPING`

## 장점

### 레벨 비교 단순화

status가 항상 안정 상태이므로 레벨 값을 직접 비교할 수 있다.

기존에는 STARTING, STOPPING 같은 전이 상태를 만날 때마다 "이 상태는 어느 레벨로 취급할 것인가"를 매핑해야 했다. 분리 후에는 status.level 값을 그대로 사용하면 된다.

### 전환 진행 여부 판단 명확

`operation != NONE`이면 전환 진행 중임을 바로 알 수 있다.

기존에는 12개 상태 중 어떤 것이 전이 상태인지 코드 전반에서 알아야 했다. 분리 후에는 operation 컬럼 하나만 확인하면 된다.

### 리소스 상태와 작업 상태 분리

status는 "현재 어떤 리소스가 존재하는가"만 나타내고, operation은 "현재 어떤 작업이 진행 중인가"만 나타낸다.

`status = RUNNING, operation = STOPPING`은 "컨테이너가 아직 있고, 정지 작업 진행 중"이라는 의미가 명확하다.

### 상태 모순 방지

기존에는 `status = STARTING`인데 실제 컨테이너가 없는 상황에서 "RUNNING이 아니니까 컨테이너가 없다"고 판단하기 어려웠다. 분리 후에는 status만 보면 리소스 존재 여부를 바로 알 수 있다.

### Reconciler 로직 단순화

Reconciler의 수렴 루프가 직관적으로 변한다:
1. `operation != NONE`이면 대기 (전환 진행 중)
2. `status == desired_state`이면 완료
3. `status.level < desired_state.level`이면 step_up
4. 그렇지 않으면 step_down

### 확장성

새로운 operation을 추가해도 status 체계에 영향을 주지 않는다. 예를 들어 MIGRATING, SNAPSHOTTING 같은 새 작업이 필요하면 operation에만 추가하면 된다.

## 단점

### 컬럼 수 증가

DB 스키마에 operation 컬럼이 추가된다. 기존 시스템에서 마이그레이션이 필요하다.

### 복합 상태 조회

기존에는 `status = 'STARTING'`으로 단순 조회했지만, 분리 후에는 `status = 'WARM' AND operation = 'STARTING'`으로 조회해야 한다. 쿼리와 인덱스 설계가 복잡해진다.

### 상태 일관성 유지 부담

두 컬럼을 항상 함께 업데이트해야 한다. operation = STARTING으로 설정했다가 완료 시 operation = NONE, status = RUNNING으로 함께 변경해야 한다. 하나만 업데이트하면 불일치 상태가 된다.

### API 응답 변화

기존에는 status 하나만 반환했지만, 분리 후에는 status와 operation을 함께 반환해야 한다. 클라이언트 코드 변경이 필요하다.

## 대안 (선택하지 않음)

### 단일 status 유지 + 레벨 매핑 테이블

전이 상태를 안정 상태에 매핑하는 테이블을 관리하는 방식.

미선택 이유: 코드 전반에 매핑 로직이 흩어지고, 새 전이 상태 추가 시 매핑도 추가해야 함.

### is_transitioning 플래그만 추가

status는 단일 컬럼 유지하고 `is_transitioning: boolean`만 추가하는 방식.

미선택 이유: 어떤 전환이 진행 중인지 알 수 없음. STARTING인지 STOPPING인지 구분 불가.

### 별도 transition_history 테이블

전환 이력을 별도 테이블에 기록하는 방식.

미선택 이유: 현재 상태 조회 시 JOIN 필요, 복잡도 증가.
