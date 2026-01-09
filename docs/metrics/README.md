# Code-Hub Metrics System

> 25개 Prometheus 메트릭을 운영 목적별로 분류한 모니터링 시스템

## 📊 개요

Code-Hub의 메트릭 시스템은 **운영 목적별**로 3가지 카테고리로 구성됩니다:

1. **[Health Check](./health-check.md)** (7개) - 시스템 가용성 및 상태 모니터링
2. **[Performance](./performance.md)** (9개) - 성능 및 처리량 측정
3. **[Business Logic](./business-logic.md)** (9개) - 비즈니스 작업 추적

## 🎯 타당성 평가

| 카테고리 | 메트릭 수 | 완성도 | 평가 |
|---------|----------|--------|------|
| Health Check | 7 | 100% | ✅ 완벽 |
| Performance | 9 | 100% | ✅ 완벽 |
| Business Logic | 9 | 100% | ✅ 완벽 |
| **전체** | **25** | **100%** | ✅ **Production Ready** |

상세 분석: **[Validity Analysis](./validity-analysis.md)**

## 📂 문서 구조

```
docs/metrics/
├── README.md                    # 이 파일 - 메트릭 시스템 개요
├── health-check.md              # Health Check 메트릭 (7개)
├── performance.md               # Performance 메트릭 (9개)
├── business-logic.md            # Business Logic 메트릭 (9개)
└── validity-analysis.md         # 타당성 분석 및 평가
```

## 🔧 기술 스택

- **수집**: Prometheus (Scrape Interval: 15s)
- **노출**: FastAPI `/metrics` endpoint (Port: 18000)
- **라이브러리**: `prometheus_client` (Multiprocess mode)
- **시각화**: Grafana 12.3.1

## 🚀 빠른 시작

### 1. 메트릭 확인

```bash
# 전체 메트릭 조회
curl http://localhost:18000/metrics

# Code-Hub 메트릭만 조회
curl -s http://localhost:18000/metrics | grep "^codehub_"

# 메트릭 개수 확인
curl -s http://localhost:18000/metrics | grep "^codehub_" | cut -d'{' -f1 | sort -u | wc -l
# 예상 결과: 28개 (25개 base + histogram _bucket/_count/_sum)
```

### 2. 카테고리별 확인

```bash
# Health Check - DB 상태
curl -s http://localhost:18000/metrics | grep "codehub_db_up"

# Performance - Workspace 작업 시간
curl -s http://localhost:18000/metrics | grep "workspace_operation_duration"

# Business Logic - 작업 성공률
curl -s http://localhost:18000/metrics | grep "workspace_operations_total"
```

## 📈 주요 메트릭 하이라이트

### 🔴 CRITICAL (필수 모니터링)

| 메트릭 | 현재 값 | 알림 조건 |
|--------|---------|----------|
| `codehub_db_up` | 1.0 (UP) | 0 = DOWN |
| `codehub_coordinator_leader_status` | 5/5 리더 | < 5 = 일부 중단 |
| `codehub_circuit_breaker_state` | 0 (CLOSED) | 2 = OPEN |
| `codehub_workspace_operations_total` | 100% 성공 | 성공률 < 95% |

### 🟡 HIGH (권장 모니터링)

- **DB Pool 사용률**: 현재 100% ⚠️ (Pool 크기 증가 권장)
- **Workspace Operation Duration**: P95 < 5초 ✅
- **Coordinator Tick Duration**: P95 < 0.1초 ✅

## 🔗 관련 문서

- [Architecture V2](../architecture_v2/) - 시스템 아키텍처
- [TTL Manager](../architecture_v2/ttl-manager.md) - TTL 메트릭 관련
- [Garbage Collector](../architecture_v2/garbage-collector.md) - GC 메트릭 관련

## 📝 변경 이력

### 2026-01-09
- ✅ TTL Manager 메트릭 추가 (`WORKSPACE_TTL_EXPIRY`)
- ✅ GC 메트릭 추가 (`COORDINATOR_GC_ORPHANS_DELETED`)
- ✅ 운영 목적별 카테고리 분류 완료
- ✅ 타당성 분석 완료 (100/100 점)
