# code-hub Spec

> 프로젝트 소개는 [README.md](../../README.md), 용어 정의는 [glossary.md](../glossary.md) 참조

---

## 문서 구조

| 파일 | 내용 |
|------|------|
| [components.md](./components.md) | 구성요소 및 책임, 고정 규칙 |
| [flows.md](./flows.md) | 핵심 플로우 (Workspace 생성/시작/정지/삭제) |
| [api.md](./api.md) | REST API 스펙 |
| [events.md](./events.md) | Real-time Events (SSE) |
| [schema.md](./schema.md) | DB 스키마 |
| [config.md](./config.md) | 설정 (Config) |

---

## 개요

클라우드 개발 환경(CDE) 플랫폼의 Local MVP 스펙.

---

## 범위

### 포함 (Local MVP)

- 로그인: 기본 계정(id/pw)
- Workspace: 이름/설명/메모 + Home Store Key를 가진 메타데이터
- Workspace Instance: Docker 기반 code-server 컨테이너 (Workspace 1개당 1개)
- 접속: Control Plane이 `/w/{workspace_id}` 리버스 프록시(게이트웨이) 내장
- 보안: 내 워크스페이스만 목록/접속/조작 (owner 강제)
- Home Store: 로컬은 host dir(마운트), 클라우드는 object storage로 확장 가능하도록 인터페이스 고정

### 제외

- Git 자동 clone/pull
- 컨테이너에 Docker 소켓 제공(로컬 Docker 제어)
- 멀티 노드/클러스터 운영
- TTL 자동 stop (MVP 제외, 추후 추가)
