# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-01-12

### Added

#### Coordinator Architecture
- Coordinator 기반 워크스페이스 관리 (선언형 상태 수렴)
- Reconciler 패턴 도입 (Level-Triggered, Single Writer)
- Ordered State Machine (PENDING → ARCHIVED → STANDBY → RUNNING)
- TTL Manager (비활성 워크스페이스 자동 강등)
- Archive GC (orphan archive 정리)

#### Observability
- Prometheus 메트릭 (인프라/API/워커)
- Grafana 대시보드 (Overview, Performance, Capacity)
- 구조화된 로깅 개선 (진단 정보 포함)
- WebSocket 메트릭

#### Frontend
- 패널 리사이즈 개선
- 오버플로 수정
- Tailwind CSS 최적화

### Fixed
- Docker 컨테이너 라이프사이클 처리 개선
- 404 시 컨테이너 재생성 전 기존 컨테이너 정리
- 빈 아카이브 생성 시 .meta 파일 포함
- Permanent error가 circuit breaker 트리거하지 않도록 수정
- DNS VPN/네트워크 안정성 개선

### Changed
- 프로젝트 구조 변경 (backend/ → src/codehub/)
- Coordinator 내 Plan 로직 분리 (wc_planner)
- 여러 모듈 함수 추출 및 코드 정리

### Documentation
- ADR-006: Reconciler 패턴 채택
- ADR-007: Reconciler 구현 전략
- ADR-008: Ordered State Machine 패턴
- ADR-009: Status/Operation 분리
- ADR-010: 패키지 분리 아키텍처
- ADR-011: 선언형 Conditions
- ADR-012: Coordinator 연결 전략

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
