# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-12-23

### Added

#### Core Features
- Workspace 생명주기 관리 (CREATED → PROVISIONING → RUNNING → STOPPING → STOPPED → DELETING → DELETED)
- Workspace CRUD API (생성, 조회, 수정, 삭제)
- 내장 HTTP/WebSocket 리버스 프록시 (외부 게이트웨이 불필요)
- code-server 컨테이너 통합 (웹 기반 VS Code)
- 실시간 상태 업데이트 (Server-Sent Events)
- Startup Recovery (서버 재시작 시 전이 상태 자동 복구)

#### Authentication & Authorization
- 세션 기반 인증 (24시간 TTL)
- 소유자 기반 접근 제어 (403 Forbidden)
- 지수 백오프 rate limiting (브루트포스 방지)
- WebSocket handshake 시점 인증

#### Frontend
- Master-Detail 반응형 UI (사이드바 + 메인 컨텐츠)
- 워크스페이스 상태 실시간 표시
- 로그인/로그아웃 페이지
- Pagination 지원

#### Infrastructure
- Docker Compose 기반 배포
- PostgreSQL 17 + Alembic 마이그레이션
- Redis 7 (Pub/Sub for multi-worker SSE)
- Multi-worker 지원 (uvicorn 4 workers)
- E2E 테스트 자동화

#### Documentation
- ADR-001: MVP Tech Stack
- ADR-002: MVP Project Structure
- ADR-003: Login Rate Limiting
- ADR-005: Redis Pub/Sub for Multi-Worker SSE
