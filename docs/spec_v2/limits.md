# RUNNING 워크스페이스 제한 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

리소스 관리를 위해 동시에 실행 가능한 워크스페이스 수를 제한합니다.

---

## 제한 유형

| 제한 | 기본값 | 설명 |
|------|--------|------|
| max_running_per_user | 2 | 사용자당 동시 RUNNING 워크스페이스 수 |
| max_running_global | 100 | 시스템 전체 동시 RUNNING 워크스페이스 수 |

---

## 불변식

1. **카운트 기준**: `observed_status = RUNNING` 또는 `operation = STARTING` 모두 카운트
2. **Atomic 체크**: 제한 체크와 operation 설정은 **동일 트랜잭션** 내에서 수행
3. **우선순위**: per_user 제한을 먼저 체크, 통과 시 global 체크

> STARTING 포함 이유: 동시 요청 시 race condition 방지

---

## 체크 시점

| 시점 | 동작 |
|------|------|
| API: desired_state = RUNNING | 제한 체크 후 설정 |
| Proxy: Auto-wake 트리거 | 제한 체크 후 진행 |

---

## 제한 초과 시 동작

| 상황 | 응답 | 상세 |
|------|------|------|
| API 요청 | 429 Too Many Requests | error: workspace_limit_exceeded |
| Auto-wake | 502 + 안내 페이지 | 실행 중인 워크스페이스 목록 표시 |

### Auto-wake 흐름

```mermaid
sequenceDiagram
    participant B as Browser
    participant P as Proxy
    participant API

    B->>P: GET /w/{workspace_id}/
    P->>API: 제한 체크
    API-->>P: 제한 초과

    P-->>B: 502 + 안내 페이지
    Note over B: 실행 중인 워크스페이스 목록
```

---

## Race Condition 방지

### 문제: RUNNING만 체크 시

```mermaid
sequenceDiagram
    participant A as User A
    participant B as User B
    participant DB

    Note over DB: RUNNING = 1, max = 2

    par 동시 요청
        A->>DB: count 조회 → 1 (통과)
        B->>DB: count 조회 → 1 (통과)
    end

    A->>DB: operation = STARTING
    B->>DB: operation = STARTING

    Note over DB: 결과: 3개 RUNNING (초과!)
```

### 해결: Atomic Transaction

| 단계 | 동작 |
|------|------|
| 1 | 트랜잭션 시작 |
| 2 | (RUNNING + STARTING) 카운트 조회 |
| 3 | 제한 체크 |
| 4 | operation = STARTING 설정 |
| 5 | 트랜잭션 커밋 |

> 트랜잭션 격리 수준: Read Committed (PostgreSQL 기본값)

---

## 에러 응답

| 필드 | 값 |
|------|---|
| error | workspace_limit_exceeded |
| limit_type | per_user / global |
| current | 현재 카운트 |
| max | 제한 값 |
| running_workspaces | 실행 중인 워크스페이스 목록 (per_user만) |

---

## 참조

- [schema.md](./schema.md) - 설정 값
- [states.md](./states.md) - 상태 정의
