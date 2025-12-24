# 핵심 플로우

> [README.md](./README.md)로 돌아가기

---

## Workspace Open (`/w/{workspace_id}`)

1. 세션 확인
2. owner 인가 (불일치 시 403)
3. ResolveUpstream 후 프록시 연결
   - 연결 실패 시 502 에러

> WebSocket 업그레이드 필수 (code-server)

> 상태 확인은 프록시에서 하지 않음. 사용자는 대시보드(API)에서 상태 확인.

---

## Workspace 생성 플로우

1. workspace_id 생성 (ULID/UUID)
2. home_store_key 계산: `users/{user_id}/workspaces/{workspace_id}/home`
3. DB에 메타데이터 저장:
   - status = CREATED
   - image_ref = config.workspace.default_image
   - instance_backend = local-docker
   - storage_backend = config.home_store.backend
   - home_ctx = NULL
4. 응답: `{ id, name, status, url }`

> url은 `{public_base_url}/w/{id}/`로 계산 (trailing slash 포함, DB 저장 X)

> 컨테이너는 StartWorkspace에서 생성

---

## StartWorkspace 플로우

> CREATED, STOPPED, ERROR 상태에서만 호출 가능

1. DB 상태를 PROVISIONING으로 원자 변경
   - `WHERE id=? AND status IN ('CREATED', 'STOPPED', 'ERROR')`
   - 실패 시 409 INVALID_STATE
2. Storage Provider.Provision(home_store_key, DB.home_ctx) 호출
   - 기존 ctx가 있으면 내부에서 자동 정리 후 새 ctx 생성
   - 반환: `{ home_mount, home_ctx }`
3. DB에 `home_ctx` 저장
4. Instance Controller.StartWorkspace 호출 (home_mount, image_ref)
5. GetStatus 폴링 (config 기반: 간격/최대 대기시간)
6. healthy=true → 상태를 RUNNING으로 변경
7. unhealthy(타임아웃) → 상태를 ERROR로 변경

> Provision이 existing_ctx를 받아 자동 정리하므로 리소스 누수 방지

---

## StopWorkspace 플로우

> RUNNING 또는 ERROR 상태에서 호출 가능 (ERROR에서 재시도 허용)

1. DB 상태를 STOPPING으로 원자 변경
   - `WHERE id=? AND status IN ('RUNNING', 'ERROR')`
   - 실패 시 409 INVALID_STATE
2. Instance Controller.StopWorkspace 호출
3. Storage Provider.Deprovision(home_ctx) 호출
   - `local-dir`: no-op
   - `object-store`: 내부적으로 persist + cleanup 처리
4. DB home_ctx = NULL
5. 성공 → 상태를 STOPPED로 변경
6. 실패 → 상태를 ERROR로 변경

> 백엔드 분기 없이 무조건 Deprovision 호출. 백엔드 내부에서 적절히 처리.

---

## DeleteWorkspace 플로우

> CREATED, STOPPED, ERROR 상태에서만 호출 가능

1. DB 상태를 DELETING으로 원자 변경
   - `WHERE id=? AND status IN ('CREATED', 'STOPPED', 'ERROR')`
   - 실패 시 409 INVALID_STATE
2. Instance Controller.DeleteWorkspace 호출 (컨테이너 삭제)
3. home_ctx가 있으면 Storage Provider.Deprovision(home_ctx) 호출
   - DB home_ctx = NULL
4. 성공 → Soft delete (deleted_at 기록, status = DELETED)
5. 실패 → 상태를 ERROR로 변경

> 컨테이너 삭제 후 스토리지 해제 (생성의 역순)

> MVP에서는 Home Store 데이터 삭제 안 함 (Purge 호출 X). 리텐션 정책은 추후 정의.

> DELETED 상태의 워크스페이스는 존재하지 않는 것으로 처리 (404 반환)

---

## Instance Controller 인터페이스

```
StartWorkspace(workspace_id, image_ref, home_mount) -> error?
StopWorkspace(workspace_id) -> error?
DeleteWorkspace(workspace_id) -> error?
ResolveUpstream(workspace_id) -> { host, port } | error
GetStatus(workspace_id) -> { exists, running, healthy, port? } | error
```

### GetStatus
컨테이너의 현재 상태를 조회합니다.

**반환값:**
- `exists`: 컨테이너 존재 여부
- `running`: 실행 중 여부
- `healthy`: 헬스체크 통과 여부
- `port`: 매핑된 포트 (실행 중일 때만)

**용도:**
- 기존 HealthCheck를 포함한 확장된 상태 조회
- Reconciler 패턴 도입 시 상태 비교에 사용
- ResolveUpstream과 일부 중복되지만 용도가 다름 (프록시 연결용 vs 전체 상태 조회용)

> Instance Controller는 컨테이너 lifecycle만 담당. 상태는 Control Plane이 DB에서 관리.

---

## Instance Controller 구현 규칙 (Local Docker)

- 컨테이너 이름: `codehub-ws-{workspace_id}`
- **멱등성 규칙:**
  - StartWorkspace: 컨테이너 있으면 start, 없으면 create+start
  - StopWorkspace: 컨테이너 없거나 이미 정지 상태면 성공 반환
  - DeleteWorkspace: 컨테이너 없으면 성공 반환 (no-op)
- ResolveUpstream: docker inspect로 포트 매핑 조회 (DB 의존 X)
- 보안: 컨테이너 포트는 `127.0.0.1` 바인딩 (외부 노출 금지)

---

## MVP 제약사항 (local-dir)

MVP는 `local-dir` 백엔드만 지원하며, 다음 특성에 의존합니다:

- **결정적 경로**: `home_mount = workspace_base_dir + home_store_key`로 항상 동일
- **Provision 멱등성**: 같은 key에 대해 항상 같은 경로 반환
- **StartWorkspace 단순화**: 경로가 바뀌지 않으므로 기존 컨테이너 재사용 가능

### K8s 패턴과의 비교

| 원칙 | K8s | local-dir (MVP) | object-store (추후) |
|------|-----|-----------------|---------------------|
| **멱등성 키** | PVC UID 기반 결정적 명명 | ✅ `home_store_key` 기반 결정적 경로 | ✅ 동일 적용 |
| **불변성** | Pod 스펙 변경 시 재생성 | 불필요 (경로 불변) | **필요** (config-aware recreate) |

**멱등성 키 (Deterministic Naming)**
- K8s: 볼륨 이름에 요청 ID 포함 → 재시도 시 기존 것 반환
- local-dir: `workspace_id` 기반 경로 계산 → 항상 같은 경로 반환
- 효과: Orphan Resource 방지

**불변성 (Immutability)**
- K8s: Pod 스펙 수정 불가, 변경 시 삭제 후 재생성
- local-dir: 경로가 바뀌지 않으므로 불필요
- object-store: staging 경로가 바뀔 수 있으므로 필요

> ⚠️ `object-store` 도입 시:
> - StartWorkspace를 **config-aware** 방식으로 업그레이드 (설정 불일치 시 컨테이너 re-create)
> - **GC 메커니즘** 추가 (DB에 없는 staging 주기적 정리)

---

## Storage Provider 인터페이스

```
Provision(home_store_key, existing_ctx?) -> { home_mount, home_ctx }
Deprovision(home_ctx) -> void
Purge(home_store_key) -> void
GetStatus(home_store_key) -> { provisioned, home_ctx?, home_mount? }
```

### Provision
컨테이너가 사용할 home_mount를 준비합니다.

**동작:**
1. existing_ctx가 있으면 먼저 정리 (내부적으로 Deprovision 로직 실행)
2. 새 home_mount 준비
3. 새 home_ctx 반환

**백엔드별 구현:**
- `local-dir`: workspace_base_dir + key 경로를 home_mount로 반환, ctx는 경로 문자열
- `object-store`: 스냅샷 복원 → staging dir 생성 → ctx에 staging 정보 저장

### Deprovision
home_ctx 리소스를 해제합니다.

**동작:**
- 백엔드 내부에서 필요한 정리 수행
- 멱등적: ctx가 NULL이거나 이미 정리됐으면 성공 반환

**백엔드별 구현:**
- `local-dir`: no-op (bind mount는 컨테이너 정지 시 자동 해제)
- `object-store`: staging → object store 영속화 → staging 삭제

### Purge
home_store_key에 해당하는 모든 데이터를 완전 삭제합니다.

**백엔드별 구현:**
- `local-dir`: 디렉토리 삭제
- `object-store`: 오브젝트 삭제

> MVP에서는 Purge 호출 안 함 (데이터 보존)

### GetStatus
현재 프로비저닝 상태를 조회합니다.

**반환값:**
- `provisioned`: 프로비저닝 완료 여부
- `home_ctx`: 현재 ctx (없으면 null)
- `home_mount`: 마운트 경로 (없으면 null)

**백엔드별 구현:**
- `local-dir`: 디렉토리 존재 여부 확인
- `object-store`: staging dir 존재 여부 + 스냅샷 존재 여부 확인

**용도:**
- Reconciler 패턴 도입 시 상태 비교에 사용
- MVP에서는 디버깅/모니터링 용도

> `home_ctx`: opaque context (JSON/string). Provision이 생성하고 Deprovision이 정리.

---

## Startup Recovery (크래시 복구)

서버 시작 시 전이 상태에서 stuck된 워크스페이스를 자동 복구합니다.

### 동작 조건

- 서버 프로세스 시작 시 자동 실행 (API 요청 수락 전)
- Phase 1: 모든 전이 상태 복구 (PROVISIONING, STOPPING, DELETING)
- Phase 2: RUNNING 상태 검증 (컨테이너 존재 여부 확인)

> 서버 재시작 시 모든 백그라운드 작업이 중단되므로, 시간 제한 없이 모든 전이 상태를 복구합니다.

### 복구 매트릭스

| DB 상태 | Instance 상태 | 복구 결과 | 설명 |
|---------|--------------|----------|------|
| PROVISIONING | running + healthy | RUNNING | 시작 성공 후 크래시 |
| PROVISIONING | 그 외 | ERROR | 시작 실패 |
| STOPPING | not running | STOPPED | 정지 성공 후 크래시 |
| STOPPING | running | RUNNING | 정지 명령 실패, 재시도 가능 |
| DELETING | not exists | DELETED | 삭제 성공 후 크래시 |
| DELETING | exists | ERROR | 삭제 실패 |
| RUNNING | running + healthy | (변경 없음) | 정상 상태 |
| RUNNING | 그 외 (not exists/not running/unhealthy) | ERROR | 컨테이너 유실 |

### 의사 코드

```python
def startup_recovery():
    # Phase 1: 전이 상태 복구
    stuck = db.query("""
        SELECT * FROM workspaces
        WHERE status IN ('PROVISIONING', 'STOPPING', 'DELETING')
    """)

    for ws in stuck:
        status = instance_controller.get_status(ws.id)

        if ws.status == 'PROVISIONING':
            if status.running and status.healthy:
                ws.status = 'RUNNING'
            else:
                # home_ctx 정리 (리소스 누수 방지)
                if ws.home_ctx:
                    storage_provider.deprovision(ws.home_ctx)
                    ws.home_ctx = None
                ws.status = 'ERROR'
        elif ws.status == 'STOPPING':
            if not status.running:
                # home_ctx 정리
                if ws.home_ctx:
                    storage_provider.deprovision(ws.home_ctx)
                    ws.home_ctx = None
                ws.status = 'STOPPED'
            else:
                ws.status = 'RUNNING'
        elif ws.status == 'DELETING':
            if not status.exists:
                # home_ctx 정리
                if ws.home_ctx:
                    storage_provider.deprovision(ws.home_ctx)
                    ws.home_ctx = None
                ws.status = 'DELETED'
            else:
                ws.status = 'ERROR'

        db.save(ws)

    # Phase 2: RUNNING 상태 검증 (docker compose down 등 대응)
    running = db.query("""
        SELECT * FROM workspaces
        WHERE status = 'RUNNING' AND deleted_at IS NULL
    """)

    for ws in running:
        status = instance_controller.get_status(ws.id)
        if not status.exists or not status.running:
            ws.status = 'ERROR'
            db.save(ws)
```

> Startup Recovery는 Reconciler의 경량 버전. 서버 재시작 시에만 실행되며, 주기적 실행이 필요하면 Reconciler 도입 검토.
