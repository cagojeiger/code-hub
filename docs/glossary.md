# Glossary

code-hub 프로젝트에서 사용하는 핵심 용어 정의입니다.

---

## C

### CAS (Compare-And-Swap)
DB 상태 변경 시 원자적 조건부 업데이트 패턴. `WHERE status IN (...)` 조건으로 동시성 제어.

### Control Plane
API(`/api/v1/*`) 및 워크스페이스 프록시(`/w/{workspace_id}/*`)를 제공하는 서버. Workspace 메타데이터/상태 관리, 세션 관리를 담당합니다.

---

## H

### Home Store
홈 데이터 원본 저장소. 로컬 환경에서는 host directory, 클라우드 환경에서는 object storage로 구현됩니다.

### Home Store Key
Home Store 내 논리 경로 키. DB에는 절대경로가 아닌 논리 키만 저장됩니다.
- 패턴: `users/{user_id}/workspaces/{workspace_id}/home`
- 로컬: `base_dir + home_store_key`를 bind mount
- 클라우드: `{home_store_key}/home.tar.zst` 같은 오브젝트 키로 사용

### home_ctx
Storage Provider의 Provision이 생성하고 Deprovision이 정리하는 opaque context (JSON/string).
- `object-store`: staging dir, snapshot ref 등 저장
- `local-dir`: 경로 문자열 또는 NULL
- Provision 호출 시 기존 ctx가 있으면 자동 정리 (리소스 누수 방지)

---

## I

### Instance Controller
Workspace Instance 생명주기(lifecycle)를 관리하는 컴포넌트.
- 컨테이너 이름: `codehub-ws-{workspace_id}`
- 멱등적 동작:
  - StartWorkspace: 컨테이너 있으면 start, 없으면 create+start
  - StopWorkspace: 없거나 이미 정지면 성공
  - DeleteWorkspace: 없으면 성공 (no-op)
- DB에 의존하지 않고 docker inspect로 정보 조회
- **ResolveUpstream**: 프록시가 컨테이너 연결 정보(host, port) 조회 시 사용
- **GetStatus**: 컨테이너 존재/실행/헬스 상태 통합 조회. Reconciler 확장 대비.
- 구현체: `local-docker` (로컬), `k8s` (클라우드, 추후)

---

## S

### Session
사용자 인증 상태를 유지하는 DB 레코드. 쿠키에 session.id를 저장하고, 만료/폐기 시각으로 유효성 관리.

### Startup Recovery
서버 시작 시 전이 상태(PROVISIONING, STOPPING, DELETING)에서 stuck된 워크스페이스를 자동 복구하는 메커니즘.
- 실행 시점: 서버 프로세스 시작 시, HTTP 요청 수락 전
- 대상: 모든 전이 상태 (MVP는 단일 프로세스이므로 시간 제한 없음)
- Instance Controller.GetStatus를 호출하여 실제 상태 확인 후 DB 업데이트
- Reconciler의 경량 버전으로, MVP에서 크래시 복구용으로 사용

### Storage Provider
스토리지 프로비저닝을 관리하는 컴포넌트. Home Store 준비/해제/삭제를 담당합니다.
- 구현체: `local-dir`(로컬), `object-store`(클라우드, 추후)
- 주요 인터페이스:
  - **Provision**: 컨테이너에 마운트할 home_mount 준비. 기존 ctx가 있으면 자동 정리.
  - **Deprovision**: home_ctx 리소스 해제. 백엔드 내부에서 필요한 정리 수행.
  - **Purge**: 홈 데이터 완전 삭제 (MVP에서는 호출 안 함)
  - **GetStatus**: 프로비저닝 상태 조회. Reconciler 확장 대비.

---

## W

### Workspace
메타데이터 단위(DB 레코드). 이름, 설명, 메모, Home Store Key, 상태 등의 정보를 포함합니다.

### Workspace Instance
실행 중인 code-server 인스턴스. 로컬 환경에서는 Docker 컨테이너, 클라우드 환경에서는 K8s Pod으로 구현됩니다. Workspace 1개당 1개의 Instance가 매핑됩니다.

### Workspace 상태
- **CREATED**: 생성됨, 아직 시작 안 함
- **PROVISIONING**: 컨테이너 시작 중, GetStatus 폴링 대기
- **RUNNING**: 실행 중, 접속 가능
- **STOPPING**: 정지 중
- **STOPPED**: 정지됨
- **DELETING**: 삭제 중
- **ERROR**: 오류 발생 (재시도 또는 삭제 가능)
- **DELETED**: 삭제됨 (soft delete)
