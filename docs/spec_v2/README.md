# M2 Specification

> M2 마일스톤을 위한 스펙 문서

---

## 개요

M2는 완성형 아키텍처를 구축합니다. M3에서는 Instance Controller와 Storage Provider 구현체만 교체하여 K8s에서 동작합니다.

### M1 → M2 주요 변경

| 항목 | M1 (MVP) | M2 |
|------|----------|-----|
| Storage | Bind Mount | Docker Volume + Object Storage |
| 상태 모델 | CREATED/RUNNING/STOPPED | PENDING/COLD/WARM/RUNNING (Ordered) |
| 상태 전환 | 명령형 (start/stop) | 선언형 (desired_state) |
| 전환 주체 | API 직접 실행 | Reconciler |
| TTL | 없음 | RUNNING→WARM→COLD 자동 전환 |
| Auto-wake | 없음 | WARM 상태에서 프록시 접속 시 자동 시작 |

---

## 문서 목록

### 핵심 문서

| 문서 | 설명 |
|------|------|
| [states.md](./states.md) | Ordered State Machine 기반 상태 정의 + Health Check |
| [schema.md](./schema.md) | DB 스키마 변경 사항 |
| [flows.md](./flows.md) | 주요 플로우 (생성, Auto-wake, TTL, Archive/Restore) |

### 레이어별 문서

| 문서 | 설명 |
|------|------|
| [storage.md](./storage.md) | Storage 동작 (archive/restore/purge) |
| [instance.md](./instance.md) | Instance 동작 (start/stop/delete) |
| [events.md](./events.md) | SSE 이벤트 정의 |

### 정책 문서

| 문서 | 설명 |
|------|------|
| [activity.md](./activity.md) | 활동 감지 메커니즘 (WebSocket 기반) |
| [limits.md](./limits.md) | RUNNING 워크스페이스 제한 |

---

## 참조

- [ADR-008: Ordered State Machine](../adr/008-ordered-state-machine.md)
- [ADR-006: Reconciler 패턴](../adr/006-reconciler-pattern.md)
- [ADR-007: Reconciler 구현](../adr/007-reconciler-implementation.md)
- [Roadmap: M2 Draft](../roadmap/002-m2-draft.md)
