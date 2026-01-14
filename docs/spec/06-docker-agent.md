# Docker Agent API Spec

> [README.md](./README.md)로 돌아가기

---

## 개요

Docker Agent는 단일 노드에서 workspace 리소스(Container, Volume)를 관리하는 API 서버입니다.

### 설계 원칙

1. **Kubelet 패턴**: API 응답이나 내부 상태가 아닌 **실제 Docker/S3 상태**를 기준으로 판단
2. **Dual Check**: 완료 판정 시 **두 개의 독립적인 소스**(S3 + Docker)를 확인
3. **Idempotent**: 모든 API는 멱등성 보장 - 같은 요청을 여러 번 해도 안전
4. **Stateless**: Agent는 상태를 저장하지 않음 - Docker와 S3가 Source of Truth

---

## Resources

### Docker Resources

| 리소스 | 네이밍 | 설명 |
|--------|--------|------|
| Container | `{prefix}{workspace_id}` | Workspace 실행 컨테이너 |
| Volume | `{prefix}{workspace_id}-home` | Workspace 데이터 저장소 |
| Job Container | `codehub-job-{type}-{id}` | Archive/Restore 작업용 |

### S3 Resources

```
s3://{bucket}/{prefix}/{workspace_id}/
├── {archive_op_id}/
│   ├── home.tar.zst           # Archive 데이터
│   └── home.tar.zst.meta      # Archive 완료 마커
└── .restore_marker            # Restore 완료 마커
```

---

## State Verification (Dual Check)

### 원칙

```
완료 판정 = S3 상태 + Docker 상태
```

- 단일 소스 조작으로 완료 위조 불가
- 장애 시 재시도로 복구 가능

### Operation별 체크 항목

| Operation | S3 Check | Docker Check | 완료 조건 |
|-----------|----------|--------------|-----------|
| Provision | - | Volume exists | Volume exists |
| Start | - | Container running, Volume exists | Both true |
| Stop | - | Container NOT exists, Volume exists | Container gone, Volume kept |
| Archive | `.meta` exists | Volume NOT exists | Both true |
| Restore | `.restore_marker` exists | Volume exists | Both true |
| Delete | - | Container NOT exists, Volume NOT exists | Both gone |

---

## API Endpoints

### Base URL

```
http://{agent-host}:{port}/api/v1/workspaces
```

---

### 4.1 Observe

전체 workspace 상태를 조회합니다.

```
GET /workspaces
```

#### Response

```json
{
  "workspaces": [
    {
      "workspace_id": "ws-123",
      "container": {
        "running": true,
        "healthy": true
      },
      "volume": {
        "exists": true
      },
      "archive": {
        "exists": true,
        "archive_key": "prefix/ws-123/op-456/home.tar.zst"
      },
      "restore": {
        "restore_op_id": "restore-789",
        "archive_key": "prefix/ws-123/op-456/home.tar.zst"
      }
    }
  ]
}
```

#### 필드 설명

| 필드 | 타입 | 설명 |
|------|------|------|
| `container` | object \| null | Container 상태 (없으면 null) |
| `container.running` | bool | 실행 중 여부 |
| `container.healthy` | bool | 트래픽 수신 가능 여부 |
| `volume` | object \| null | Volume 상태 (없으면 null) |
| `volume.exists` | bool | 존재 여부 |
| `archive` | object \| null | 최신 Archive 정보 (없으면 null) |
| `archive.exists` | bool | 존재 여부 |
| `archive.archive_key` | string | S3 key (`.meta` 있는 최신 archive) |
| `restore` | object \| null | 마지막 Restore 정보 (없으면 null) |
| `restore.restore_op_id` | string | Restore 작업 ID |
| `restore.archive_key` | string | 복원된 archive의 S3 key |

---

### 4.2 Lifecycle Operations

#### Provision

Volume을 생성합니다.

```
POST /workspaces/{workspace_id}/provision
```

| 항목 | 값 |
|------|---|
| Precondition | 없음 |
| Action | Volume 생성 |
| Idempotency | Volume 존재 시 `already_exists` 반환 |
| Completion | Volume exists |

**Response**

```json
{
  "status": "completed",  // completed | already_exists
  "workspace_id": "ws-123"
}
```

---

#### Start

Container를 시작합니다.

```
POST /workspaces/{workspace_id}/start
```

**Request**

```json
{
  "image": "code-server:latest"  // optional, default image 사용
}
```

| 항목 | 값 |
|------|---|
| Precondition | Volume exists (없으면 에러) |
| Action | Container 생성 및 시작 |
| Idempotency | 이미 running이면 `already_running` 반환 |
| Completion | Container running AND Volume exists |

**Response**

```json
{
  "status": "completed",  // completed | already_running
  "workspace_id": "ws-123"
}
```

**Errors**

| Code | 조건 |
|------|------|
| `VOLUME_NOT_FOUND` | Volume이 없음 |

---

#### Stop

Container를 중지합니다 (Volume 유지).

```
POST /workspaces/{workspace_id}/stop
```

| 항목 | 값 |
|------|---|
| Precondition | 없음 |
| Action | Container 중지 및 삭제 |
| Idempotency | Container 없으면 `already_stopped` 반환 |
| Completion | Container NOT exists AND Volume exists |

**Response**

```json
{
  "status": "completed",  // completed | already_stopped
  "workspace_id": "ws-123"
}
```

---

#### Delete

Workspace를 완전히 삭제합니다 (Container + Volume).

```
DELETE /workspaces/{workspace_id}
```

| 항목 | 값 |
|------|---|
| Precondition | 없음 |
| Action | Container 삭제 → Job Container 삭제 → Volume 삭제 |
| Idempotency | 리소스 없으면 `deleted` 반환 |
| Completion | Container NOT exists AND Volume NOT exists |

**Response**

```json
{
  "status": "deleted",
  "workspace_id": "ws-123"
}
```

---

### 4.3 Persistence Operations

#### Archive

Volume을 S3에 아카이브합니다.

```
POST /workspaces/{workspace_id}/archive
```

**Request**

```json
{
  "archive_op_id": "op-456"
}
```

| 항목 | 값 |
|------|---|
| Precondition | Container NOT running, Volume exists |
| Action | Job 실행 (Volume → S3) → Volume 삭제 |
| Idempotency | Job 실행 중이면 `in_progress`, 완료됐으면 `completed` |
| Completion | S3 `.meta` exists AND Volume NOT exists |

**Response**

```json
{
  "status": "completed",  // completed | in_progress
  "workspace_id": "ws-123",
  "archive_key": "prefix/ws-123/op-456/home.tar.zst"
}
```

**Errors**

| Code | 조건 |
|------|------|
| `CONTAINER_RUNNING` | Container가 실행 중 |
| `VOLUME_NOT_FOUND` | Volume이 없음 |

**S3 결과물**

```
{prefix}/{workspace_id}/{archive_op_id}/
├── home.tar.zst       # Archive 데이터
└── home.tar.zst.meta  # 완료 마커 (Job script가 생성)
```

---

#### Restore

S3 archive를 Volume으로 복원합니다.

```
POST /workspaces/{workspace_id}/restore
```

**Request**

```json
{
  "archive_key": "prefix/ws-123/op-456/home.tar.zst",
  "restore_op_id": "restore-789"
}
```

| 항목 | 값 |
|------|---|
| Precondition | Container NOT running, Archive exists |
| Action | Volume 생성 (없으면) → Job 실행 (S3 → Volume) → Marker 기록 |
| Idempotency | Job 실행 중이면 `in_progress`, 완료됐으면 `completed` |
| Completion | S3 `.restore_marker` exists AND Volume exists |

**Response**

```json
{
  "status": "completed",  // completed | in_progress
  "workspace_id": "ws-123",
  "restore_marker": "restore-789"
}
```

**Errors**

| Code | 조건 |
|------|------|
| `CONTAINER_RUNNING` | Container가 실행 중 |
| `ARCHIVE_NOT_FOUND` | Archive가 S3에 없음 |

**S3 결과물**

```
{prefix}/{workspace_id}/.restore_marker
```

```json
{
  "restore_op_id": "restore-789",
  "archive_key": "prefix/ws-123/op-456/home.tar.zst",
  "restored_at": "2024-01-15T10:30:00Z"
}
```

---

#### Delete Archive

특정 archive를 S3에서 삭제합니다.

```
DELETE /workspaces/archives?archive_key={key}
```

**Response**

```json
{
  "deleted": true,
  "archive_key": "prefix/ws-123/op-456/home.tar.zst"
}
```

---

### 4.4 Routing

#### Get Upstream

프록시 라우팅을 위한 upstream 주소를 반환합니다.

```
GET /workspaces/{workspace_id}/upstream
```

**Response**

```json
{
  "hostname": "codehub-ws-123",
  "port": 8080,
  "url": "http://codehub-ws-123:8080"
}
```

---

### 4.5 Garbage Collection

#### Run GC

보호되지 않은 archive를 삭제합니다.

```
POST /workspaces/gc
```

**Request**

```json
{
  "archive_keys": ["prefix/ws-1/op-1/home.tar.zst"],
  "protected_workspaces": [["ws-2", "op-2"]]
}
```

| 필드 | 설명 |
|------|------|
| `archive_keys` | 보호할 archive key 목록 (RESTORING 대상) |
| `protected_workspaces` | 보호할 (workspace_id, archive_op_id) 튜플 목록 (ARCHIVING 진행 중) |

**Response**

```json
{
  "deleted_count": 3,
  "deleted_keys": [
    "prefix/ws-old/op-old/home.tar.zst"
  ]
}
```

---

## S3 Structure

### Archive 구조

```
s3://{bucket}/{prefix}/{workspace_id}/{archive_op_id}/
├── home.tar.zst           # 압축된 volume 데이터
└── home.tar.zst.meta      # 완료 마커
```

- `.meta` 파일이 있어야 완료된 archive로 인정
- `archive_op_id`로 동일 workspace의 여러 archive 구분

### Restore Marker 구조

```
s3://{bucket}/{prefix}/{workspace_id}/.restore_marker
```

```json
{
  "restore_op_id": "restore-789",
  "archive_key": "prefix/ws-123/op-456/home.tar.zst",
  "restored_at": "2024-01-15T10:30:00Z"
}
```

- `restore_op_id`로 동일 archive의 중복 restore 구분
- Job script가 생성 (Agent가 아닌 container 내부에서)

---

## Error Handling

### Error Response Format

```json
{
  "error": {
    "code": "VOLUME_NOT_FOUND",
    "message": "Volume does not exist for workspace ws-123"
  }
}
```

### Error Codes

| Code | HTTP Status | 설명 |
|------|-------------|------|
| `VOLUME_NOT_FOUND` | 404 | Volume이 없음 |
| `CONTAINER_RUNNING` | 409 | Container가 실행 중 (archive/restore 불가) |
| `ARCHIVE_NOT_FOUND` | 404 | Archive가 S3에 없음 |
| `JOB_FAILED` | 500 | Archive/Restore job 실패 |
| `VOLUME_IN_USE` | 409 | Volume이 사용 중 (삭제 불가) |

### Retry 전략

| 에러 유형 | 재시도 | 설명 |
|----------|--------|------|
| Network timeout | ✓ | 일시적 장애 |
| 5xx errors | ✓ | 서버 에러 |
| `JOB_FAILED` | ✓ | Job 실패 (재시도 가능) |
| 4xx errors | ✗ | 클라이언트 에러 (precondition 미충족) |

---

## Convergence Principles (Kubelet Pattern)

Agent와 WC의 협력 관계는 **Kubelet 패턴**을 따릅니다.

### 6가지 수렴 조건

| 조건 | 정의 | 구현 |
|------|------|------|
| **1. 고정점 존재성** | 모든 operation은 언젠가 완료 상태에 도달 | Archive=tar.zst+.meta, Restore=.restore_marker+volume |
| **2. 진행성** | 불일치가 있으면 줄이는 행동을 함 | WC가 매 tick마다 phase≠desired 체크 |
| **3. 멱등성** | 재시도가 상태를 악화시키지 않음 | archive_op_id로 S3 경로 결정 |
| **4. Single-Writer** | 같은 리소스를 경쟁적으로 바꾸지 않음 | 아래 책임 분리 참조 |
| **5. 비증가 불일치** | 수렴 과정에서 불일치가 늘지 않음 | archive_key 비교로 검증 |
| **6. 환경 가정** | 외부 시스템이 eventually available | Circuit breaker, retry |

### Single-Writer 책임 분리

```
┌─────────────────────────────────────────────────────────────┐
│                     Single-Writer 원칙                       │
├─────────────────┬───────────────────────────────────────────┤
│ 리소스          │ Writer                                    │
├─────────────────┼───────────────────────────────────────────┤
│ Container       │ WC (runtime.delete)                       │
│ Volume          │ WC (runtime.delete)                       │
│ S3 Archive      │ Agent (archive.sh → tar.zst + .meta)     │
│ S3 Restore Marker │ Agent (restore.sh → .restore_marker)   │
└─────────────────┴───────────────────────────────────────────┘
```

**중요:** Agent는 Volume을 삭제하지 않습니다. Archive 완료 후 Volume 삭제는 WC가 담당합니다.

### Fire-and-Forget 패턴

Agent API는 **즉시 응답**할 수 있습니다:

```
┌─────┐         ┌───────┐         ┌────────┐
│ WC  │──req──▶│ Agent │──job──▶│ Docker │
│     │◀─202───│       │         │   S3   │
│     │         │       │         │        │
│     │  ...    │       │         │        │
│     │         │       │         │        │
│     │◀─observe────────│◀────────│ (완료) │
└─────┘         └───────┘         └────────┘
```

**작동 원리:**

1. **Agent 즉시 응답**: Job 시작 후 `in_progress` 또는 `completed` 반환
2. **WC 재시도**: 완료 조건 미충족 시 다음 tick에서 재시도
3. **Observer 감지**: 실제 Docker/S3 상태를 conditions에 반영
4. **완료 판정**: WC가 conditions 기반으로 완료 확인

**이 패턴이 가능한 이유:**

- **Stateless**: Agent는 상태를 저장하지 않음
- **Idempotent**: 같은 요청 여러 번 해도 안전
- **Dual Check**: 완료는 API 응답이 아닌 실제 상태로 판정
- **Eventually Consistent**: WC가 주기적으로 상태 수렴

**예시: Archive 흐름**

```
1. WC: POST /archive {archive_op_id: "op-1"}
2. Agent: Job 시작, 즉시 {status: "in_progress"} 반환
3. (Job 실행 중...)
4. Observer: S3에서 .meta 감지 → conditions.archive 업데이트
5. WC: conditions 확인 → archive_ready AND !volume_ready
6. WC: DELETE /workspaces/{id} (volume 삭제)
7. Observer: volume 없음 감지 → conditions.volume = null
8. WC: 완료 조건 충족 → operation = NONE
```

---

## Appendix: Status Values

### Operation Status

| Status | 설명 |
|--------|------|
| `completed` | 작업 완료 |
| `in_progress` | 작업 진행 중 (job running) |
| `already_exists` | 이미 존재 (provision) |
| `already_running` | 이미 실행 중 (start) |
| `already_stopped` | 이미 중지됨 (stop) |
| `deleted` | 삭제 완료 |
