# Instance Operations (M2)

> [README.md](./README.md)로 돌아가기

---

## 핵심 원칙

1. **InstanceController는 컨테이너만 관리** - Volume은 StorageProvider
2. **DB를 모름** - Reconciler가 DB 관리
3. **Volume은 내부 계산** - `ws-{workspace_id}-home`
4. **리소스 레벨 멱등성** - 컨테이너 존재 여부로 직접 체크
5. **즉시 종료** - SIGKILL 직접 전송 (Volume 유지로 데이터 안전)
6. **순서 보장** - Container 삭제 → Volume 삭제 (역순 금지)
7. **계약 기반 추상화** - K8s 내부 상태 노출 안 함

---

## 불변식

1. STARTING 전에 Volume 반드시 존재
2. Container 삭제 → Volume 삭제 순서
3. Container는 workspace당 1개 (`ws-{workspace_id}`)
4. 모든 InstanceController 메서드는 멱등

---

## 네이밍 규칙

| 항목 | 형식 | 예시 |
|------|------|------|
| container_name | `ws-{workspace_id}` | `ws-abc123` |
| volume_key | `ws-{workspace_id}-home` | `ws-abc123-home` |
| mount_path | `/home/coder` | - |

> K8s DNS-1123 호환: 하이픈(`-`) 사용, 언더스코어(`_`) 금지

---

## observed_status별 리소스

| observed_status | Container | Volume |
|-----------------|-----------|--------|
| PENDING | - | - |
| STANDBY | - | ✅ |
| RUNNING | ✅ | ✅ |

---

## InstanceController 인터페이스

| 메서드 | 역할 | 멱등성 |
|--------|------|--------|
| start(workspace_id, image_ref) | 컨테이너 시작 + Ready 대기 | running이면 무시 |
| delete(workspace_id) | SIGKILL + 삭제 | 없으면 무시 |
| is_running(workspace_id) | 트래픽 수신 가능 여부 | - |

### start() 계약

- 성공 반환 = 트래픽 수신 가능 (`is_running() == True`)
- not running 상태면 정리 후 재생성
- volume_key는 내부 계산 (`ws-{id}-home`)

### is_running() 계약

> "프록시가 이 컨테이너로 요청을 보내도 되는가?"

| 백엔드 | 조건 |
|--------|------|
| Docker | state.Running == true |
| K8s | containerStatuses[*].ready == true |

---

## Operation별 동작

### STARTING (STANDBY → RUNNING)

| 항목 | 값 |
|------|---|
| 전제 조건 | observed_status=STANDBY, Volume 존재 |
| Actuator | start(workspace_id, image_ref) |
| 완료 조건 | is_running() == True |

### STOPPING (RUNNING → STANDBY)

| 항목 | 값 |
|------|---|
| 전제 조건 | observed_status=RUNNING |
| Actuator | delete(workspace_id) |
| 완료 조건 | is_running() == False, Volume 유지 |

> **즉시 종료 이유**: Volume 유지로 데이터 안전, IDE 자동 저장, TTL 트리거 상황

### DELETING

| 순서 | 동작 | 주체 |
|------|------|------|
| 1 | deleted_at 설정 (Soft Delete) | API |
| 2 | Container 삭제 | InstanceController (via Reconciler) |
| 3 | Volume 삭제 | StorageProvider (via Reconciler) |
| 4 | 리소스 없음 관측 → observed_status=DELETED | HealthMonitor |

> Archive는 GC가 2시간 후 정리
>
> **Single Writer 원칙**: observed_status는 HealthMonitor만 변경. Reconciler는 리소스 정리만 담당.

---

## Timeout

| 단계 | 값 | 설명 |
|------|---|------|
| startup_timeout | 300초 | 이미지 pull + 컨테이너 생성 |
| health_check_timeout | 30초 | running 상태 확인 |

---

## 에러 코드

| 코드 | 설명 | 복구 |
|------|------|------|
| IMAGE_PULL_FAILED | 이미지 다운로드 실패 | 자동 재시도 |
| HEALTH_CHECK_FAILED | 헬스체크 타임아웃 | 자동 재시도 |
| CONTAINER_CREATE_FAILED | 컨테이너 생성 실패 | 관리자 개입 |
| VOLUME_NOT_FOUND | Volume 없음 | 관리자 개입 |

---

## 백엔드별 구현

| 항목 | Docker | K8s |
|------|--------|-----|
| 컨테이너 생성 | docker run + 시작 대기 | Pod 생성 + Ready 대기 |
| 컨테이너 삭제 | docker rm -f | Pod 삭제 (grace-period=0) |
| 트래픽 수신 가능 | state.Running | Ready condition |
| Volume 마운트 | -v volume:/path | PVC mount |

---

## 참조

- [components/state-reconciler.md](./components/state-reconciler.md) - StateReconciler
- [components/health-monitor.md](./components/health-monitor.md) - HealthMonitor
- [storage.md](./storage.md) - StorageProvider
- [states.md](./states.md) - 상태 전환
