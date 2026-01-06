# Resilience Patterns

> 외부 서비스 (Docker, S3) 장애 대응을 위한 안정성 패턴

---

## Overview

외부 인프라 장애 시 시스템 안정성을 위해 두 가지 패턴 적용:

| 패턴 | 목적 | 적용 위치 |
|------|------|----------|
| Retry + Jitter | Transient 에러 복구 | WC Coordinator |
| Circuit Breaker | Cascade Failure 방지 | WC Coordinator |

---

## Retry with Jitter

### 목적
- 일시적 네트워크/서비스 에러 자동 복구
- Thundering Herd 방지 (동시 재시도 분산)

### 동작

```
실패 → 재시도1 (1s ± jitter) → 재시도2 (2s ± jitter) → 재시도3 (4s ± jitter) → 실패
```

| 설정 | 값 | 설명 |
|------|-----|------|
| max_retries | 3 | 최대 재시도 횟수 |
| base_delay | 1s | 초기 대기 시간 |
| max_delay | 30s | 최대 대기 시간 |
| jitter | ±50% | 랜덤 분산 범위 |

### 에러 분류

| 분류 | 재시도 | 예시 |
|------|--------|------|
| retryable | O | ConnectError, 429, 5xx, Throttling |
| permanent | X | 4xx, AccessDenied, NoSuchBucket |
| unknown | X | 미분류 에러 (보수적 처리) |

---

## Circuit Breaker

### 목적
- Cascade Failure 방지 (장애 전파 차단)
- 장애 서비스 보호 (복구 시간 확보)
- 빠른 실패 (Fast Fail)

### 상태 전이

```
       5회 실패
CLOSED ──────────→ OPEN
   ↑                 │
   │   2회 성공      │ 30초 후
   └──── HALF_OPEN ←─┘
              │
              │ 1회 실패
              └────────→ OPEN
```

| 상태 | 동작 | 전이 조건 |
|------|------|----------|
| CLOSED | 요청 통과 | 5회 연속 실패 → OPEN |
| OPEN | 즉시 실패 (CircuitOpenError) | 30초 후 → HALF_OPEN |
| HALF_OPEN | 제한 통과 (테스트) | 2회 성공 → CLOSED / 1회 실패 → OPEN |

### 설정

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| failure_threshold | 5 | OPEN 전환 실패 횟수 |
| success_threshold | 2 | CLOSED 복구 성공 횟수 |
| timeout | 30s | OPEN → HALF_OPEN 대기 시간 |

---

## 적용 범위

### 전역 Circuit Breaker

단일 Docker/S3 인프라 환경이므로 전역 CB ("external") 사용:

```
┌──────────────────────────────────────────┐
│              WC Coordinator              │
│                                          │
│  workspace1 ─┐                           │
│  workspace2 ─┼──→ [Circuit Breaker] ──→  │ Docker/S3
│  workspace3 ─┘      "external"           │
└──────────────────────────────────────────┘
```

**설계 이유:**
- 단일 Docker 호스트 장애 시 전체 작업 차단
- 개별 workspace 재시도로 인한 과부하 방지
- 분산 인프라로 확장 시 노드별 CB 분리 가능

### 미적용 Coordinator

| Coordinator | 이유 |
|-------------|------|
| Observer | 실패 시 tick skip (다음 주기에 자연 재시도) |
| GC | 비긴급 작업, 다음 tick에 재시도 |
| TTL Manager | DB 작업만 수행 (외부 호출 없음) |

---

## 장애 시나리오

### 시나리오 1: Docker 일시 장애

```
1. WC: container 생성 시도 → ConnectError
2. Retry: 1s, 2s, 4s 간격으로 재시도
3. 3회 내 복구 → 성공
```

### 시나리오 2: Docker 지속 장애

```
1. WC: 5개 workspace 연속 실패
2. Circuit → OPEN
3. 이후 workspace: 즉시 CircuitOpenError (Docker 호출 안함)
4. 30초 후 HALF_OPEN → 복구 테스트
5. 성공 시 CLOSED 복귀
```

### 시나리오 3: S3 Throttling

```
1. WC: archive 시도 → Throttling (429)
2. Retry: jitter 포함 지수 백오프
3. Thundering Herd 방지로 분산 재시도
```

---

## 모니터링

로그 패턴:

| 이벤트 | 로그 |
|--------|------|
| 재시도 | `Retryable error (attempt N/M, retry in Xs)` |
| CB OPEN | `[CircuitBreaker:external] CLOSED → OPEN` |
| CB 거부 | `[CircuitBreaker:external] Circuit OPEN, rejecting request` |
| CB 복구 | `[CircuitBreaker:external] HALF_OPEN → CLOSED` |
