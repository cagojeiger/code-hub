# ADR-001: MVP Tech Stack

## 상태
Accepted

## 컨텍스트
code-hub MVP 개발을 위한 기술 스택 결정이 필요합니다.
- 목표: 빠른 개발 및 검증
- 스케일: 워크스페이스 최대 10개
- Production 버전은 Go로 재작성 예정

## 결정

### 언어 및 프레임워크
| 항목 | 선택 | 이유 |
|------|------|------|
| 언어 | Python 3.13 | async 성능 1.5배 향상, 개발 속도 |
| 프레임워크 | FastAPI | 고성능 async, 자동 OpenAPI 문서 |
| ASGI 서버 | uvicorn | libuv 기반, HTTP/1.1 |

### 데이터베이스
| 항목 | 선택 | 이유 |
|------|------|------|
| DB | ~~SQLite~~ → PostgreSQL 17 | MVP 검증 완료 후 스케일업 ([ADR-004](004-postgresql-migration.md)) |
| Migration | Alembic | 스키마 버전 관리, 롤백 지원 |

### 프록시
| 항목 | 선택 | 이유 |
|------|------|------|
| HTTP 프록시 | FastAPI 내장 (httpx) | 동적 업스트림, 인증 통합 |
| WebSocket 프록시 | FastAPI 내장 (websockets) | 단일 프로세스, 구현 단순 |
| 외부 프록시 | 사용 안 함 | 10개 워크스페이스에 불필요 |

### Docker 연동
| 항목 | 선택 | 이유 |
|------|------|------|
| SDK | docker-py | 성숙한 라이브러리 |
| 비동기 처리 | asyncio.to_thread | 동기 SDK를 스레드풀에서 실행 |

### 프로세스 모델
| 항목 | 선택 | 이유 |
|------|------|------|
| 프로세스 | 단일 (uvicorn 1 worker) | SQLite 호환, 상태 공유 불필요 |
| 동시성 | async/await | I/O bound 작업에 최적 |

### 프로토콜
| 항목 | 선택 | 이유 |
|------|------|------|
| HTTP | 1.1 | code-server가 1.1 사용, 충분한 성능 |
| WebSocket | HTTP/1.1 Upgrade | 표준 방식 |

## 부하 추정치

> ⚠️ 아래 수치는 추정치이며, 실제 부하는 사용 패턴에 따라 다를 수 있습니다.

### 워크스페이스당 예상 부하
| 항목 | 추정치 |
|------|--------|
| WebSocket (터미널) | 3-5개 |
| WebSocket (LSP, 확장) | 2-3개 |
| HTTP (code-server UI) | ~50-100 req/min |
| HTTP (API 폴링) | ~10 req/min |

### 10개 워크스페이스 총 부하 (추정)
| 항목 | 추정치 |
|------|--------|
| 동시 WebSocket | ~80개 |
| HTTP 요청 | ~50 req/sec (피크) |

### FastAPI 처리 능력 대비
| 항목 | 추정 필요량 | 참고 용량 |
|------|------------|----------|
| HTTP | ~50 req/sec | 10,000+ req/sec |
| WebSocket | ~80개 | 1,000+ 동시 연결 |
| 여유율 | ~125배 | - |

## 결과

### 장점
- 빠른 개발 속도 (Python 생산성)
- 충분한 성능 여유

### 단점
- Production 스케일에서 한계
- Go 재작성 필요 (계획됨)

### 대안 (고려했으나 선택 안 함)
| 대안 | 미선택 이유 |
|------|------------|
| Go | MVP 개발 속도 우선 |
| ~~PostgreSQL~~ | ~~단일 프로세스에서 불필요한 복잡도~~ → 채택 ([ADR-004](004-postgresql-migration.md)) |
| Nginx/Traefik | 동적 업스트림 설정 복잡, ForwardAuth 필요 |
| 멀티 프로세스 | ~~SQLite 동시성 문제~~, WebSocket 상태 공유 필요 |
| Python 3.14 (free-threading) | 라이브러리 호환성 미성숙 |

## 참고 자료
- [Python 3.12 vs 3.13 Performance](https://en.lewoniewski.info/2024/python-3-12-vs-python-3-13-performance-testing/)
- [fastapi-proxy-lib](https://github.com/WSH032/fastapi-proxy-lib)
- [FastAPI WebSocket Documentation](https://fastapi.tiangolo.com/advanced/websockets/)
