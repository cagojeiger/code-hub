# Roadmap 903: 배포 플랫폼 (미래)

## Status: Future

> 빌드된 이미지를 Helm으로 배포하고 FRP로 외부 노출. 그룹 기반 네임스페이스 + Quota 관리.

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│  code-hub                                                               │
│                                                                         │
│  ┌─────────────────────────────────┐  ┌─────────────────────────────┐  │
│  │  Workspaces (개발)              │  │  Deployments (실행)         │  │
│  │  namespace: code-hub-workspaces │  │  namespace: group-{name}    │  │
│  │  ┌───────────┐                  │  │  ┌───────────┐              │  │
│  │  │code-server│──build──┐        │  │  │  App Pod  │              │  │
│  │  │           │         │        │  │  │ (Helm)    │              │  │
│  │  └───────────┘         │        │  │  └───────────┘              │  │
│  └────────────────────────│────────┘  └──────────▲──────────────────┘  │
│                           │                      │                      │
│                           ▼                      │                      │
│                    ┌───────────┐          ┌──────┴─────┐               │
│                    │ Registry  │          │  Helm      │               │
│                    │ (MinIO)   │◀─────────│ Controller │               │
│                    └───────────┘   pull   └──────▲─────┘               │
│                                                  │                      │
│  ┌───────────────────────────────────────────────┴──────────────────┐  │
│  │  code-hub UI                                                      │  │
│  │  ┌──────────────┬──────────────┬──────────────┐                  │  │
│  │  │ Workspaces   │ Deployments  │  Storage     │                  │  │
│  │  │ (기존)       │ (새 탭)      │ (새 탭)      │                  │  │
│  │  └──────────────┴──────────────┴──────────────┘                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 핵심 개념

### 1. 그룹 기반 네임스페이스

```
┌─────────────────────────┐  ┌─────────────────────────┐
│ Group A                 │  │ Group B                 │
│ namespace: group-a      │  │ namespace: group-b      │
│ quota: 10 CPU, 20GB     │  │ quota: 5 CPU, 10GB      │
│                         │  │                         │
│ ┌─────┐ ┌─────┐        │  │ ┌─────┐                 │
│ │App1 │ │App2 │        │  │ │App3 │                 │
│ │user1│ │user2│        │  │ │user3│                 │
│ └─────┘ └─────┘        │  │ └─────┘                 │
└─────────────────────────┘  └─────────────────────────┘
```

**특징**:
- 그룹 = K8s Namespace
- 그룹 내 사용자들이 리소스 공유
- 그룹 단위로 Quota 적용

### 2. FRP 서비스 노출

```
┌─────────────────────────────────────────────────────────────────┐
│  FRP Server (중앙)                                               │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Workspace 포트 포워딩 (901)                              │    │
│  │ 3000-{workspace_id}.code-hub.com → Workspace:3000       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ 배포된 앱 노출 (903)                                      │    │
│  │ {app_name}-{deployment_id}.code-hub.com → App Pod       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
         ▲
         │
    Browser: https://myapp-abc123.code-hub.com
```

**901과 903 FRP 통합**:
- 동일한 FRP 인프라 공유
- 도메인 패턴으로 구분

---

## Helm 배포

### 범용 Helm 템플릿

사용자는 image와 기본 설정만 입력하면 배포:

```yaml
# 사용자 입력 (UI에서)
image: registry:5000/user-123/myapp:v1
replicas: 2
port: 8080
env:
  - name: DATABASE_URL
    value: postgres://...
resources:
  cpu: "500m"
  memory: "512Mi"
```

### 내부 Helm Chart

```yaml
# templates/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Values.appName }}
  namespace: {{ .Values.groupNamespace }}
spec:
  replicas: {{ .Values.replicas }}
  template:
    spec:
      containers:
        - name: app
          image: {{ .Values.image }}
          ports:
            - containerPort: {{ .Values.port }}
          env: {{ .Values.env | toYaml | nindent 12 }}
          resources:
            limits:
              cpu: {{ .Values.resources.cpu }}
              memory: {{ .Values.resources.memory }}
```

---

## UI: Deployments 탭

### 기능

| 기능 | 설명 |
|------|------|
| **이미지 선택** | Registry에서 빌드된 이미지 목록 조회 |
| **배포 생성** | Helm values 입력 → 배포 |
| **배포 관리** | 스케일, 재시작, 삭제 |
| **로그 조회** | 실행 중인 Pod 로그 |
| **상태 모니터링** | Pod 상태, 리소스 사용량 |
| **URL 확인** | FRP 노출 URL 표시 |

### UI 목업

```
┌──────────────────────────────────────────────────────────────┐
│  Deployments                                    [+ 새 배포]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ myapp-abc123                                           │ │
│  │ Image: registry:5000/user-123/myapp:v1                │ │
│  │ Status: Running (2/2)                                  │ │
│  │ URL: https://myapp-abc123.code-hub.com                │ │
│  │ CPU: 0.3/1.0  Memory: 256Mi/512Mi                     │ │
│  │                                                        │ │
│  │ [Scale] [Restart] [Logs] [Delete]                     │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ backend-xyz789                                         │ │
│  │ Image: registry:5000/user-456/backend:latest          │ │
│  │ Status: Running (1/1)                                  │ │
│  │ URL: https://backend-xyz789.code-hub.com              │ │
│  │ ...                                                    │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Group + Quota

### 데이터 모델

```yaml
group:
  id: "group-123"
  name: "team-alpha"
  namespace: "group-team-alpha"
  members:
    - user-123
    - user-456
    - user-789
  quota:
    cpu: "10"           # 전체 그룹 CPU 한도
    memory: "20Gi"      # 전체 그룹 메모리 한도
    storage: "50Gi"     # Registry 이미지 포함
    deployments: 10     # 최대 배포 수
```

### K8s ResourceQuota

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: group-quota
  namespace: group-team-alpha
spec:
  hard:
    requests.cpu: "10"
    requests.memory: "20Gi"
    limits.cpu: "20"
    limits.memory: "40Gi"
    persistentvolumeclaims: "10"
    pods: "20"
```

---

## 동작 흐름

```
1. 개발 (Workspace)
   code-server → Dockerfile 작성 → build 요청 → Registry push
   (902-container-build.md 참조)

2. 배포 (UI)
   code-hub UI → Deployments 탭 → "새 배포"
   → 이미지 선택: registry:5000/user-123/myapp:v1
   → 설정 입력 (replicas, port, env, resources)
   → 배포 실행

3. Helm Controller
   → values.yaml 생성
   → helm install/upgrade 실행
   → K8s Deployment 생성

4. FRP 노출
   → frpc 사이드카가 Pod 시작 감지
   → frps에 터널 등록
   → URL 활성화: myapp-abc123.code-hub.com

5. 사용
   → 브라우저에서 URL 접속
   → FRP → Pod → App
```

---

## 의존성

| 의존성 | 설명 |
|--------|------|
| **901** | FRP 인프라 공유 |
| **902** | BuildKit + Registry |
| **M3** | K8s 환경 필수 |

---

## 미결정 사항

1. **Helm Controller 구현**: Helm SDK 직접 사용? Flux/ArgoCD 연동?
2. **시크릿 관리**: 환경변수에 민감 정보를 어떻게?
3. **네트워크 정책**: 그룹 간 통신 제한?
4. **로그/메트릭**: 중앙 로깅 시스템 연동?

---

## References

- [Helm SDK](https://helm.sh/docs/topics/advanced/)
- [K8s ResourceQuota](https://kubernetes.io/docs/concepts/policy/resource-quotas/)
- [frp GitHub](https://github.com/fatedier/frp)
