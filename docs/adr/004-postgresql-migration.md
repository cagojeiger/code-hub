# ADR-004: PostgreSQL Migration

## 상태
Accepted

## 컨텍스트
MVP 검증이 완료되고 Kubernetes 환경에서 100+ 워크스페이스 운영을 계획하면서, 데이터베이스 전략 재검토가 필요합니다.

### 배경
- MVP 검증 완료
- Kubernetes 기반 운영 환경 전환 예정
- 100+ 워크스페이스 스케일업 목표

### 문제점
1. **스키마 마이그레이션 부재**: `SQLModel.metadata.create_all()`은 새 테이블만 생성하고, 기존 테이블에 컬럼 추가 불가
2. **프로덕션 배포 실패**: Rate Limiting 기능(PR #39) 머지 후, 새 컬럼 부재로 서버 시작 실패
3. **스케일 한계**: SQLite는 단일 writer 제약으로 100+ 워크스페이스에서 병목 예상
4. **K8s 비호환**: SQLite는 파일 기반으로 Pod 재시작/스케일링 시 데이터 유실 위험

### 요구사항
- 안정적인 스키마 마이그레이션 도구
- 100+ 워크스페이스 지원
- 분산 환경 대비

## 결정

### PostgreSQL + Alembic 도입

| 항목 | 선택 | 이유 |
|------|------|------|
| Database | PostgreSQL 17 | 동시성, 스케일, 분산 환경 지원 |
| Migration | Alembic | SQLAlchemy 표준, 버전 관리, 롤백 지원 |
| 테스트 DB | SQLite in-memory 유지 | CI 속도, 외부 의존성 제거 |

### 주요 변경

| 항목 | 변경 내용 |
|------|----------|
| 의존성 | asyncpg, alembic 추가 |
| Docker Compose | PostgreSQL 17 서비스 추가 |
| 기본 DB URL | SQLite → PostgreSQL |
| 스키마 관리 | `create_all()` → Alembic 마이그레이션 |

### 배포 전략

#### 마이그레이션 분리 패턴

멀티 워커/인스턴스 환경에서 마이그레이션 충돌을 방지하기 위해 마이그레이션을 별도 서비스로 분리합니다.

| 단계 | 서비스 | 동작 |
|------|--------|------|
| 1 | migrate | `alembic upgrade head` 실행 후 종료 |
| 2 | backend | 마이그레이션 완료 후 uvicorn 시작 (N workers) |

#### Docker Compose 흐름

```
postgres (healthy) → migrate (완료/종료) → backend (N workers)
```

#### K8s 적용 시

- **Init Container**: 마이그레이션 실행 후 Pod 내 앱 컨테이너 시작
- **또는 Job**: 별도 마이그레이션 Job 완료 후 Deployment 롤아웃

## 결과

### 장점
- 스키마 변경 이력 관리 (버전 관리)
- 롤백 가능 (downgrade)
- 100+ 워크스페이스 동시성 지원
- 향후 분산 배포 대비

### 단점
- 운영 복잡도 증가 (PostgreSQL 관리)
- 로컬 개발 환경에 Docker 필요
- 마이그레이션 스크립트 관리 필요

### 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| SQLite + Alembic | 스케일 한계, 단일 writer 병목 |
| SQLite + Auto-migration | 복잡한 스키마 변경 대응 어려움, 롤백 불가 |
| SQLite 유지 | MVP 이후 스케일 요구사항 미충족 |

### ADR-001 영향
- ADR-001에서 SQLite 선택 이유: "단일 프로세스에 적합, 배포 단순"
- 상황 변화: MVP 검증 완료, 100+ 스케일 목표
- ADR-001 업데이트 필요: PostgreSQL 전환 기록

## 참고 자료
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [SQLAlchemy Async with Alembic](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
- [PostgreSQL vs SQLite](https://www.postgresql.org/about/)
