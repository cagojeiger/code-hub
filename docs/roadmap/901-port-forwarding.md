# Roadmap 901: 포트 포워딩 (미래)

## Status: Future

> code-server 내부에서 개발 서버(3000, 5000 등)를 띄웠을 때 브라우저 접근 지원

---

## 문제 상황

```
Browser → Control Plane (/w/{id}/) → code-server:8080 (만 프록시)
                                         ↓
                               개발서버 :3000, :5000 → 접근 불가
```

**원인**: Workspace 컨테이너가 host 포트를 노출하지 않음 (K8s 호환 + 보안 목적)

---

## 검토 중인 방안

| 방식 | 설명 | 적합성 |
|------|------|--------|
| Control Plane 확장 | `/w/{id}/port/{port}/` 동적 라우팅 | ❌ CP 부담 과다 |
| code-server 내장 | `/w/{id}/proxy/{port}/` 활용 | ⚠️ 단순 케이스만 |
| **FRP 사이드카** | frpc 사이드카 + frps 중앙 서버 | ✅ 검토 중 |

---

## 방식별 분석

### 방식 1: Control Plane 프록시 확장

**URL 패턴**: `/w/{workspace_id}/port/{port}/`

- ❌ Control Plane에 과도한 동적 부담 (포트 노출/해제마다 라우팅 변경)
- ❌ 상태 관리 복잡도 (각 Workspace의 열린 포트 실시간 추적)
- ❌ 트래픽 집중

**결론**: 권장하지 않음

### 방식 2: code-server 내장 포트 포워딩

**URL 패턴**: `/w/{workspace_id}/proxy/{port}/`

- ✅ 환경변수 1개만 추가 (`VSCODE_PROXY_URI`)
- ✅ VS Code Ports 패널과 통합
- ⚠️ FE+BE 분리 개발 시 문제 (FE에서 BE API 호출 시 경로 충돌)
- ❌ code-server 전용 (다른 IDE 지원 불가)

**결론**: 단순 케이스(FE만 또는 BE만)에 적합

### 방식 3: FRP 사이드카 컨테이너 ✅ 검토 중

**아키텍처**:
```
┌─────────────────────────────────────┐
│  Pod                                │
│  ┌─────────────┐  ┌──────────────┐  │
│  │ code-server │  │ frp-sidecar  │  │
│  │ :8080       │  │ (frpc)       │  │
│  │ :3000 (dev) │──│              │  │
│  └─────────────┘  └──────────────┘  │
└─────────────────────────────────────┘
          ↓
    ┌───────────┐
    │ frp-server│ (Control Plane or 별도)
    └───────────┘
          ↓
    브라우저: https://3000-{workspace_id}.your-cde.com
```

**장점:**
- IDE 독립적 (code-server 외 다른 이미지 지원)
- FE+BE 분리 개발 시 각 포트에 별도 도메인 (`3000-{id}.domain.com`)
- TCP/UDP 프로토콜 지원
- K8s 사이드카 패턴과 자연스러움

**구성요소:**
- frp-server: Control Plane에 내장 또는 별도 서비스
- frp-sidecar: Workspace Pod에 사이드카 컨테이너
- 포트포워드 UI/API: 사용자가 포트 등록/해제

---

## 방식 1 vs 방식 2: 핵심 차이

> 둘 다 프록시인데 뭐가 다른가?

**핵심 질문**: 누가 추가 포트를 알고 있어야 하는가?

```
방식 1 (Control Plane 확장):
─────────────────────────────────────────────────────────
Browser → Control Plane → code-server:8080  (기본)
                ↓
Browser → Control Plane → code-server:3000  (추가 포트)
                ↓
Browser → Control Plane → code-server:5000  (추가 포트)

Control Plane이 3000, 5000 포트를 알아야 함
→ 포트 등록/해제마다 라우팅 설정 변경 필요
→ 각 Workspace별 열린 포트 상태 추적 필요
→ 동적 상태 관리 부담
```

```
방식 2 (code-server 내장):
─────────────────────────────────────────────────────────
Browser → Control Plane → code-server:8080 (항상 8080만)
                                ↓
                    code-server 내부에서 /proxy/3000/ 처리
                                ↓
                          localhost:3000 연결

Control Plane은 8080만 알면 됨 (변경 없음)
→ code-server가 내부에서 알아서 /proxy/{port}/ 처리
→ Control Plane 코드 수정 불필요
→ 환경변수 1개만 추가
```

**결론**: 방식 1은 Control Plane이 "포트 상태"를 관리해야 하고, 방식 2는 그렇지 않음.

---

## 실제 CDE 제품 참고

- **GitHub Codespaces**: VS Code 기반 → 방식 2 스타일
- **Gitpod, Coder**: 범용 IDE 지원 → 방식 3 스타일 (별도 터널링 계층)

---

## 의존성

- K8s 환경 (M3) 이후 구현이 자연스러움
- Local Docker에서는 방식 2로 임시 대응 가능

---

## References

- [frp GitHub](https://github.com/fatedier/frp)
- [code-server Proxy](https://coder.com/docs/code-server/guide)
