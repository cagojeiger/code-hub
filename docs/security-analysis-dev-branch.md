# Dev 브랜치 보안 분석 보고서

> 분석 일자: 2026-01-09

## 서비스 구조 개요

Dev 브랜치의 주요 서비스:

| 서비스 | 위치 | 역할 |
|--------|------|------|
| **SessionService** | `src/codehub/services/session_service.py` | 사용자 세션 생성/검증/폐기 |
| **WorkspaceService** | `src/codehub/services/workspace_service.py` | 워크스페이스 CRUD 및 상태 관리 |

---

## 보안 구현 분석

### 1. 비밀번호 보안 (security.py)

- **상태**: 양호
- **구현**: Argon2id 해시 알고리즘 사용 (OWASP 권장)
- `PasswordHasher()` 기본 설정 사용 (적절함)

### 2. 로그인 Rate Limiting (auth.py)

- **상태**: 양호
- **구현**: 지수 백오프 잠금

| 실패 횟수 | 잠금 시간 |
|-----------|----------|
| 5회 | 30초 |
| 6회 | 60초 |
| 7회 | 120초 |
| 10회+ | 30분 (최대) |

### 3. 세션 관리 (session_service.py)

- **상태**: 양호
- **구현**: Single-session 정책
  - 새 로그인 시 기존 세션 모두 삭제
  - TTL 기반 자동 만료 (기본 24시간)
  - 명시적 revoke 지원

### 4. 쿠키 설정 (auth.py)

```python
response.set_cookie(
    key="session",
    httponly=True,      # XSS 방어
    samesite="lax",     # CSRF 부분 방어
    secure=_settings.cookie.secure,  # 환경변수로 제어
)
```

### 5. 권한 검증 (proxy/auth.py)

- **상태**: 양호
- `get_workspace_for_user()`: owner_user_id != user_id → ForbiddenError
- Proxy 레벨에서 인증/인가 모두 검사

---

## 보안 취약점 및 개선 권고

### 높음 (High)

| 항목 | 위치 | 문제점 | 권고 |
|------|------|--------|------|
| 기본 자격증명 | `config.py:69` | S3 secret_key="codehub123" 하드코딩 | 환경변수 필수화, 기본값 제거 |
| Cookie Secure 기본값 | `config.py:125` | `secure: bool = False` | 프로덕션에서 True 강제 |

### 중간 (Medium)

| 항목 | 위치 | 문제점 | 권고 |
|------|------|--------|------|
| 캐시 TTL | `cache.py` | 3초 TTL 캐시 (세션 revoke 후 3초간 유효) | 민감 작업시 캐시 무효화 추가 |
| DB URL 기본값 | `config.py:20` | 비밀번호 "codehub" 하드코딩 | 환경변수 필수화 |

### 낮음 (Low)

| 항목 | 위치 | 문제점 | 권고 |
|------|------|--------|------|
| Request ID 신뢰 | `middleware.py:31` | 클라이언트 X-Request-ID 수용 | 내부 생성으로 변경 고려 |
| 에러 메시지 | `auth.py` | "Invalid username or password" 동일 | 현재 양호 (정보 노출 방지) |

---

## 아키텍처 보안 요약

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client    │────▶│   Proxy      │────▶│  Workspace  │
│   Browser   │     │  (FastAPI)   │     │  Container  │
└─────────────┘     └──────────────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
              ┌──────────┐  ┌──────────┐
              │ Session  │  │ Ownership│
              │ Check    │  │ Check    │
              └──────────┘  └──────────┘
```

**보안 레이어:**
1. 세션 쿠키 검증 → 인증
2. 워크스페이스 소유권 검증 → 인가
3. Rate limiting → 브루트포스 방지
4. Argon2id → 비밀번호 보호

---

## 즉시 조치 권장 항목

### 프로덕션 배포 전 필수

1. `COOKIE_SECURE=true` 설정
2. 모든 기본 비밀번호/키 환경변수로 교체
3. HTTPS 강제

### 세션 revoke 시 캐시 무효화

```python
# SessionService.revoke() 호출 후
clear_session_cache(session_id)
```
