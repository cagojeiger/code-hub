# CodeHub Documentation

## 문서 구조

```
docs/
├── spec/           # 정의 (What) - 시스템 규칙과 계약
├── architecture/   # 구현 (How) - 알고리즘과 패턴
├── adr/            # 결정 (Why) - 기술 선택 근거
├── roadmap/        # 일정 - 마일스톤과 태스크
└── workflow.md     # 작업 프로세스
```

## 시작점

### 1. 핵심 계약 이해

[spec/00-contracts.md](spec/00-contracts.md) - 10개 핵심 계약

| # | 계약 | 요약 |
|---|------|------|
| 1 | Reality vs DB | WC=Observer+Controller+Judge 단일 컴포넌트 |
| 2 | Level-Triggered | WC는 DB만 읽음 (이벤트 불신) |
| 3 | Single Writer | 관측+제어 컬럼은 WC, 요청 컬럼은 API |
| 4 | Non-preemptive | ERROR 전환은 원자적 |
| 5 | Ordered SM | 인접 레벨만 전이 |
| 6 | Container↔Volume | Container 있으면 Volume 필수 |
| 7 | Archive/Restore | op_id로 멱등, Crash-Only |
| 8 | Ordering | archive_key 저장 → Volume 삭제 |
| 9 | GC Protection | deleted_at 시 보호 해제 |
| 10 | Retry Policy | 단말 에러 또는 MAX_RETRY까지 재시도 |

### 2. 상태 머신 이해

[spec/02-states.md](spec/02-states.md) - Phase, Operation, State Machine

```
PENDING(0) → ARCHIVED(5) → STANDBY(10) → RUNNING(20)
```

### 3. 구현 패턴 이해

[architecture/coordinator-runtime.md](architecture/coordinator-runtime.md) - Coordinator 런타임

| Coordinator | 역할 | 주기 |
|-------------|------|------|
| Observer | 리소스 관측 → conditions | 10s |
| WC | 상태 수렴 (Judge+Control) | 10s |
| TTL Manager | 비활성 워크스페이스 강등 | 60s |
| Archive GC | orphan archive 정리 | 1h |
| EventListener | CDC (PG → Redis) | 실시간 |

## 문서별 역할

### spec/ - 정의 (What)

| 파일 | 내용 |
|------|------|
| 00-contracts.md | 핵심 계약 (규칙/제약) |
| 01-glossary.md | 용어 정의 |
| 02-states.md | State Machine 정의 |
| 03-schema.md | DB 스키마 |
| 04-control-plane.md | Coordinator 계약 |
| 05-data-plane.md | Storage/Instance 계약 |

### architecture/ - 구현 (How)

| 파일 | 내용 |
|------|------|
| coordinator-runtime.md | Tick Loop, Leader Election |
| event-listener.md | CDC + SSE (PUB/SUB) |
| ttl-manager.md | Activity ZSET, TTL 체크 |
| wc.md | Judge + Hybrid Execution |
| wc-judge.md | Phase 계산 로직 |
| wc-observer.md | Bulk Observer 패턴 |

### adr/ - 결정 (Why)

Architecture Decision Records - 기술 선택 근거와 트레이드오프 기록

## 레거시 문서

M1 기반 레거시 문서는 `.archive/docs/`에 보관됩니다.
