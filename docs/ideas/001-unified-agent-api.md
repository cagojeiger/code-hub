# Idea: Unified Agent API

- **Status**: Idea
- **Created**: 2025-01-10

## Summary

Docker와 Kubernetes 백엔드를 동일한 REST API 스키마로 통합하여, Control Plane이 백엔드 종류와 무관하게 일관된 방식으로 워크스페이스를 관리할 수 있도록 한다.

## Motivation

현재 구조:
- `LocalDockerInstanceController`: Control Plane 내에서 docker-py로 직접 호출
- K8s 지원 추가 시: 별도 패턴 필요

문제점:
- 백엔드별 분기 코드 증가
- 새 백엔드 추가 시 Control Plane 수정 필요
- 테스트 복잡도 증가

## Proposal

### 통합 아키텍처

```
Control Plane ──HTTP──→ Docker Agent ──docker-py──→ Docker Engine
Control Plane ──HTTP──→ K8s Agent ──k8s-client──→ K8s API Server
                    │
                    └── 동일한 API 스키마
```

### 통합 API 스키마

모든 Agent가 동일한 REST API를 구현:

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/v1/health` | Agent 헬스체크 |
| GET | `/api/v1/info` | Agent 정보 (backend_type 등) |
| GET | `/api/v1/instances` | 인스턴스 목록 |
| GET | `/api/v1/instances/{id}` | 인스턴스 상태 |
| POST | `/api/v1/instances/{id}` | 인스턴스 생성/시작 |
| POST | `/api/v1/instances/{id}:stop` | 인스턴스 정지 |
| DELETE | `/api/v1/instances/{id}` | 인스턴스 삭제 |
| GET | `/api/v1/instances/{id}/upstream` | 프록시 upstream 정보 |
| GET | `/api/v1/volumes/{id}` | 볼륨 상태 |
| POST | `/api/v1/volumes/{id}` | 볼륨 생성 |
| DELETE | `/api/v1/volumes/{id}` | 볼륨 삭제 |

### Agent 종류

1. **Docker Agent**: docker-py로 Docker Engine 제어
2. **K8s Agent**: kubernetes-client로 K8s API 제어
3. (향후) **Remote Docker Agent**: 원격 Docker Host 제어

### 네트워크 구성 옵션

```
Case 1: Same VPC
Control Plane ──Private IP──→ Agent

Case 2: VPN/Peering
Control Plane ──VPN──→ Agent

Case 3: Public (mTLS 필수)
Control Plane ──Public + mTLS──→ Agent

Case 4: FRP (NAT 통과)
Control Plane ──→ FRP Server ←── Agent (outbound tunnel)
```

### 데이터 모델

```python
class Cluster(SQLModel, table=True):
    id: str                    # "local" | "cluster-prod-1"
    display_name: str
    backend_type: str          # "docker" | "kubernetes"
    agent_endpoint: str        # "http://docker-agent:8081"
    connection_type: str       # "direct" | "frp"
    auth_method: str           # "none" | "api_key" | "mtls"
    is_default: bool
    is_active: bool
```

## Benefits

- Control Plane 코드 단순화 (백엔드 분기 제거)
- 플러그인 아키텍처 (새 백엔드 = 새 Agent 구현)
- Mock Agent로 테스트 용이
- 일관된 보안 모델

## Drawbacks

- 추가 컴포넌트 (Docker Agent 서비스)
- 네트워크 홉 증가 (약간의 지연)
- 설정 복잡도 증가

## Open Questions

- [ ] API 스키마 버전 관리 방식
- [ ] Agent 헬스체크 및 장애 복구 전략
- [ ] FRP 사용 시 HA 구성 방법
- [ ] Docker Agent를 별도 프로세스 vs 사이드카로 배포할지

## References

- 현재 InstanceController 인터페이스: `backend/app/services/instance/interface.py`
- 현재 LocalDockerInstanceController: `backend/app/services/instance/local_docker.py`
