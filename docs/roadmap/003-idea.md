# Roadmap 003: Runtime Agent API (Idea)

- **Status**: Idea
- **Created**: 2025-01-12

---

## 버전 전략

```
v0.2.0 (현재)
    │
    │  Control Plane이 Docker 직접 호출
    │
    ▼
v0.2.1
    │
    │  Control Plane + Docker Agent (Agent API 기반 리팩토링)
    │  - docker-compose로 함께 배포
    │  - Agent API 계약 확정
    │
    ▼
v0.3.0
    │
    │  K8s Agent 추가 (Control Plane 수정 없이!)
    │  - Agent API 계약 그대로 사용
    │  - K8s Runtime만 구현
    │
    ▼
```

**핵심 원칙**: v0.2.1에서 Agent API 계약을 확정하고, v0.3.0에서는 Control Plane 수정 없이 K8s Agent만 추가한다.

---

## 핵심 아이디어

**모든 인프라 백엔드(Docker, K8s 등)를 동일한 Agent 인터페이스로 추상화한다.**

---

## 1. 문제: 직접 호출의 한계

현재 Control Plane이 Docker를 직접 호출합니다:

```
┌─────────────────┐
│  Control Plane  │
│                 │
│  ┌───────────┐  │
│  │ docker-py │──┼──────────► Docker Engine
│  └───────────┘  │
│                 │
└─────────────────┘
```

K8s를 추가하면 **분기 코드**가 생깁니다:

```
if backend == "docker":
    docker-py 호출
elif backend == "k8s":
    kubernetes-client 호출
```

**문제점:**
- 백엔드마다 Control Plane 코드 수정 필요
- 테스트 복잡도 증가
- 관심사가 섞임

---

## 2. 해결: Agent 레이어 도입

모든 백엔드를 **Runtime Agent**로 감싸고, **동일한 프로토콜**로 통신합니다:

```
┌─────────────────┐
│  Control Plane  │
│                 │
│  ┌───────────┐  │
│  │  HTTP     │  │
│  │  Client   │  │
│  └─────┬─────┘  │
└────────┼────────┘
         │
         │  ← 동일한 프로토콜 (HTTP)
         │
    ┌────┴────┬─────────────┐
    │         │             │
    ▼         ▼             ▼
┌───────┐ ┌───────┐   ┌───────────┐
│Docker │ │ K8s   │   │ Future    │
│Runtime│ │Runtime│   │  ???      │
└───┬───┘ └───┬───┘   └─────┬─────┘
    │         │             │
    ▼         ▼             ▼
 Docker    K8s API        ???
 Engine    Server
```

**Control Plane 입장에서 모든 Agent가 똑같이 생겼습니다.**

---

## 3. 아키텍처 결정

### 3.1 배포 형태: 독립 서비스

Agent가 별도 컨테이너/Pod로 실행, Control Plane과 HTTP 통신

### 3.2 인증 방식: API Key

- 단순하고 구현이 쉬움
- 환경변수로 전달
- 향후 mTLS로 업그레이드 가능

### 3.3 Agent 등록: DB 등록

- `agents` 테이블에 Agent 정보 저장
- 동적으로 추가/삭제 가능
- Health 상태 추적

### 3.4 Workspace 할당: 1 Agent = 1 Cluster

- Agent가 하나의 Docker/K8s 클러스터를 담당
- Workspace 생성 시 `cluster_id` 지정
- 명확한 책임 경계

### 3.5 장애 처리: Health Check + Retry

- Agent 헬스체크 주기적 실행
- 실패 시 retry (exponential backoff)
- 반복 실패 시 Workspace ERROR 상태로 전환

---

## 4. Agent API 스펙

### 4.1 Instance 관리

```
POST   /instances/{workspace_id}/start     - 컨테이너 시작
DELETE /instances/{workspace_id}           - 컨테이너 삭제
GET    /instances/{workspace_id}/status    - 상태 확인
GET    /instances/{workspace_id}/upstream  - 프록시 주소 반환
GET    /instances                          - 전체 목록 (prefix 필터)
```

### 4.2 Volume 관리

```
POST   /volumes/{workspace_id}             - 볼륨 생성
DELETE /volumes/{workspace_id}             - 볼륨 삭제
GET    /volumes/{workspace_id}/exists      - 존재 확인
GET    /volumes                            - 전체 목록
```

### 4.3 Job 실행

```
POST   /jobs/archive                       - 아카이브 Job 실행
POST   /jobs/restore                       - 복원 Job 실행
```

### 4.4 Health

```
GET    /health                             - Agent 상태
```

---

## 5. DB 스키마 확장

### 5.1 agents 테이블

```sql
CREATE TABLE agents (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    endpoint VARCHAR(255) NOT NULL,      -- https://agent.example.com
    api_key_hash VARCHAR(255) NOT NULL,  -- bcrypt hash
    type VARCHAR(50) NOT NULL,           -- docker, k8s
    status VARCHAR(50) DEFAULT 'active', -- active, inactive, error
    capacity INT,                        -- 최대 workspace 수
    current_load INT DEFAULT 0,          -- 현재 workspace 수
    region VARCHAR(100),                 -- ap-northeast-2
    labels JSONB,                        -- 추가 메타데이터
    last_health_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.2 workspaces 테이블 수정

```sql
ALTER TABLE workspaces ADD COLUMN agent_id UUID REFERENCES agents(id);
```

---

## 6. 프로젝트 구조

모노레포 내 패키지로 구성:

```
code-hub/
├── src/
│   ├── codehub/              # 기존 Control Plane
│   └── codehub_agent/        # 새로운 Runtime Agent 패키지
│       ├── __init__.py
│       ├── app/
│       │   ├── main.py       # FastAPI app
│       │   ├── api/
│       │   │   ├── instances.py
│       │   │   ├── volumes.py
│       │   │   ├── jobs.py
│       │   │   └── health.py
│       │   └── config.py
│       ├── runtimes/
│       │   ├── docker/       # Docker Runtime 구현
│       │   └── k8s/          # K8s Runtime 구현
│       └── core/
│           └── interfaces.py
├── containers/
│   ├── storage-job/          # 기존
│   └── agent/                # Runtime Agent Dockerfile
│       └── Dockerfile
└── pyproject.toml            # uv workspace 설정
```

---

## 7. 네트워크 유연성

### Case 1: 같은 네트워크 (직접 연결)

```
Control Plane ────► Agent (직접 HTTP)
```

### Case 2: 다른 네트워크 (VPN)

```
Control Plane ──(VPN)──► Agent
```

### Case 3: NAT 뒤 (FRP 터널)

```
Control Plane ──► FRP Server ◄── Agent (outbound tunnel)
```

**Control Plane 코드 변경 없이** endpoint 설정만 바꾸면 됩니다.

---

## 8. 마일스톤

### v0.2.1: Control Plane + Docker Agent

**목표**: Agent API 계약 확정 + Docker 환경 배포

#### Phase 1: Agent API 설계
- [ ] Runtime Agent API 인터페이스 정의
- [ ] OpenAPI 스펙 작성
- [ ] API 계약 문서화

#### Phase 2: 인프라 변경
- [ ] agents 테이블 추가
- [ ] workspaces.agent_id 컬럼 추가
- [ ] DB 마이그레이션

#### Phase 3: Agent 구현
- [ ] codehub-agent 패키지 생성
- [ ] Docker Runtime 구현 (기존 코드 이전)
- [ ] Agent Health 엔드포인트

#### Phase 4: Control Plane 리팩토링
- [ ] Control Plane → Agent HTTP Client 구현
- [ ] 기존 직접 호출 코드 제거
- [ ] Agent 등록/관리 API

#### Phase 5: 배포
- [ ] docker-compose 업데이트 (Control Plane + Docker Agent)
- [ ] E2E 테스트
- [ ] 문서화

---

### v0.3.0: K8s Agent

**목표**: Control Plane 수정 없이 K8s 지원 추가

#### Phase 1: K8s Runtime 구현
- [ ] K8s Runtime 구현 (Pod, PVC 관리)
- [ ] K8s용 StorageClass/PVC 설정

#### Phase 2: 배포
- [ ] K8s 배포 매니페스트 (Helm Chart)
- [ ] Agent Deployment/Service

#### Phase 3: 테스트
- [ ] E2E 테스트 (minikube/kind)
- [ ] Docker + K8s 혼합 환경 테스트
- [ ] 장애 복구 시나리오 테스트
- [ ] 문서화

---

## 9. 핵심 가치

| 가치 | 설명 |
|------|------|
| 확장성 | 새 백엔드 = 새 Agent만 구현, Control Plane 수정 불필요 |
| 단순성 | Control Plane이 백엔드 구현을 몰라도 됨, HTTP 통신만 |
| 유연성 | 네트워크 구성을 자유롭게 변경 가능 |
| 격리 | Agent 장애가 다른 클러스터에 영향 없음 |
| 테스트 | Mock Agent로 Control Plane 테스트 가능 |

---

## 10. 고려사항

| 항목 | 내용 |
|------|------|
| 추가 컴포넌트 | Docker Agent가 별도 서비스로 분리됨, 관리 포인트 증가 |
| 네트워크 홉 | +1 홉 추가, 약간의 지연 (~1-5ms 로컬 환경) |
| 보안 설정 | API Key 관리 필요, 향후 mTLS 검토 |

---

## 11. Open Questions

- [ ] Agent Health Check 주기는? (제안: 30초)
- [ ] Agent 타임아웃은? (제안: 30초)
- [ ] Workspace 생성 시 agent_id 자동 할당 로직?
- [ ] FRP 사용 시 HA 구성은?

---

## References

- 현재 구현: `src/codehub/adapters/instance/`
- 인터페이스: `src/codehub/core/interfaces/`
