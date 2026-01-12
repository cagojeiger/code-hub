# ADR-011: Conditions 기반 상태 표현

## 상태
Proposed

## 한 줄 요약

상태를 "한 칸짜리 값(state)"으로 저장하지 말고, **"체크리스트(conditions)"를 저장**해두고 화면에 보이는 상태(phase)는 거기서 계산하자.

---

## 기존 방식의 문제

현재 설계는 이런 구조:

```
observed_status = RUNNING/STANDBY/...  (리소스가 뭐가 있냐)
operation = STARTING/ARCHIVING/...     (지금 뭐 하는 중이냐)
health_status = OK/ERROR               (건강하냐)
error_info = {...}                     (왜 안 됐냐)
```

**문제**: 상태를 표현하는 축이 여러 개라서 조합 모순이 생김.

예: "RUNNING인데 error_info.is_terminal=true면 ERROR야? RUNNING이야?"
→ 이런 조합마다 "계약"을 정의해야 함 (현재 9개)

---

## 새로운 방식: 조건(Condition) 체크리스트

"워크스페이스가 잘 돌아간다"를 말하려면 이런 체크가 필요함:

- 볼륨 있냐?
- 컨테이너 살아있냐?
- 불변식 깨졌냐? (컨테이너 있는데 볼륨 없음)
- 마지막 작업이 성공했냐?

이걸 **각각 조건으로 분리해서 True/False로 저장**:

```
VolumeReady = True/False
ContainerReady = True/False
Healthy = True/False (reason: "ContainerWithoutVolume" 등)
```

---

## Phase는 "저장"이 아니라 "계산"

DB에 `phase=RUNNING`을 박아두는 게 아니라, 조건을 보고 계산:

```python
def calculate_phase(conditions):
    if not conditions.healthy:
        return "ERROR"  # reason에 왜 실패했는지 있음
    if conditions.container_ready and conditions.volume_ready:
        return "RUNNING"
    if conditions.volume_ready:
        return "STANDBY"
    return "PENDING"
```

**핵심**: ERROR가 "상태 값"이 아니라 "조건이 깨진 결과"가 됨.

---

## 왜 계약이 줄어드냐

기존: "A필드와 B필드가 이럴 때는 C여야 한다" 같은 관계 계약이 계속 늘어남

조건 방식:
- **조건만** 잘 기록하면 됨
- 상태/에러는 그 조건에서 계산하면 됨

규칙이 흩어져 있던 것 → "조건 → phase 계산 규칙" 한 군데로 모임

---

## reason/message의 장점

기존:
- `operation`에서 "뭐 하는 중인지" 보고
- "왜 실패했는지"는 `error_info`를 또 봐야 함

조건 방식: 실패한 조건 하나에 이유가 붙어있음

```
Healthy = False
  reason: "Timeout"
  message: "ARCHIVING 30분 초과"
```

디버깅이 "상태=ERROR" 같은 무의미한 한 줄이 아니라,
**"어떤 조건이 왜 False냐"**로 바로 내려감.

---

## 쉬운 비유

**기존**:
"오늘 기분=좋음, 건강=나쁨, 숙제=하는중, 실패=있음"
→ 값들이 따로 있어서 해석이 사람마다 다름

**조건 방식**:
"체온 정상? O / 기침? X / 식욕? O / 수면? X" 같은 체크리스트
→ "아프다/괜찮다"는 체크리스트로 계산

---

## 결정

Kubernetes의 Conditions 패턴을 참고하여:

1. **조건이 기본 단위**: 각 조건은 True/False + reason/message
2. **Phase는 파생 값**: 조건들의 조합으로 최종 상태를 계산
3. **단일 소유자**: 각 조건은 하나의 컴포넌트만 갱신
4. **동시성은 분리**: operation/op_id CAS는 그대로 유지

---

## 장점

| 장점 | 설명 |
|------|------|
| 조합 모순 해소 | "RUNNING인데 ERROR?" 같은 문제 없음 |
| 디버깅 용이 | 어떤 조건이 왜 False인지 바로 보임 |
| 계약 감소 | 필드 간 관계 계약 → 파생 규칙 하나로 통합 |
| 확장성 | 새 조건 추가해도 기존 조건에 영향 없음 |

---

## 단점

| 단점 | 대응 |
|------|------|
| 학습 곡선 | 문서화 및 예시 제공 |
| 마이그레이션 필요 | 기존 데이터 구조 변경 |

---

## 업계 사례

| 시스템 | 패턴 | 특징 |
|--------|------|------|
| **Kubernetes** | Conditions + Phase | Pod, Node, Deployment 등 모든 리소스에 적용 |
| **Knative** | Conditions | Ready, ContainerHealthy, RoutesReady 등 |
| **Tekton** | Conditions | Succeeded, Running 등 파이프라인 상태 |

---

## Partially Supersedes

- **ADR-008**: Ordered State Machine 패턴
  - **Superseded**: observed_status 표현 방식 → Conditions 패턴으로 대체
  - **Still Valid**: desired_state 레벨 개념, step_up/step_down 전이 규칙, Active/Archive 분리

- **ADR-009**: Status와 Operation 분리
  - **Superseded**: status 분리 개념 → Conditions 패턴으로 대체
  - **Still Valid**: operation 분리 개념, operation/op_id CAS 동시성 제어

---

## 변경 이력

| 날짜 | 변경 내용 |
|------|----------|
| 2026-01 | Proposed |

## 참고 자료
- [Kubernetes API Conventions - Conditions](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md#typical-status-properties)
- [Knative Conditions](https://knative.dev/docs/serving/spec/knative-api-specification-1.0/#conditions)
