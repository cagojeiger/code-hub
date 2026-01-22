# CodeHub 보안 감사 보고서

**감사일**: 2026-01-22
**대상 버전**: v0.2.0
**감사자**: AI Security Audit

---

## 요약

| 심각도 | 발견 항목 수 |
|--------|-------------|
| 🔴 높음 (High) | 2 |
| 🟠 중간 (Medium) | 6 |
| 🟡 낮음 (Low) | 2 |
| ✅ 양호 | 14 |

---

## 🔴 높음 (High) - 공개 전 반드시 수정 필요

### 1. 하드코딩된 기본 자격 증명 (docker-compose.yml)

**위치**: `docker-compose.yml`

```yaml
# Line 34-35
S3_ACCESS_KEY: codehub
S3_SECRET_KEY: codehub123

# Line 109
POSTGRES_PASSWORD: codehub

# Line 144
MINIO_ROOT_PASSWORD: codehub123

# Line 221
GF_SECURITY_ADMIN_PASSWORD: qwer1234
```

**위험**: 공개 저장소에 기본 자격 증명이 노출되면 공격자가 즉시 악용 가능

**권장 조치**:
1. `docker-compose.yml`에서 모든 하드코딩된 비밀번호 제거
2. `.env.example` 파일 생성하여 필요한 환경변수 목록만 제공
3. 실제 값은 `.env` 파일에서 로드하도록 변경
4. README에 환경변수 설정 가이드 추가

```yaml
# 수정 예시
x-common-env: &common-env
  S3_ACCESS_KEY: ${S3_ACCESS_KEY}
  S3_SECRET_KEY: ${S3_SECRET_KEY}
```

---

### 2. 기본 관리자 비밀번호 (main.py)

**위치**: `src/codehub/app/main.py:72`

```python
password = os.getenv("ADMIN_PASSWORD", "qwer1234")
```

**위험**: 환경변수가 설정되지 않으면 취약한 기본 비밀번호 사용

**권장 조치**:
1. 기본값 제거하고 환경변수 필수로 변경
2. 또는 최초 실행 시 랜덤 비밀번호 생성 후 콘솔에 출력

```python
password = os.getenv("ADMIN_PASSWORD")
if not password:
    raise ValueError("ADMIN_PASSWORD environment variable is required")
```

---

## 🟠 중간 (Medium)

### 3. 코드 내 기본 자격 증명 (config.py)

**위치**: `src/codehub/app/config.py:68-69`

```python
access_key: str = Field(default="codehub", validation_alias="S3_ACCESS_KEY")
secret_key: str = Field(default="codehub123", validation_alias="S3_SECRET_KEY")
```

**권장 조치**: 기본값 제거 또는 `None`으로 설정 후 검증 추가

---

### 4. 쿠키 Secure 플래그 기본값 False

**위치**: `src/codehub/app/config.py:131`

```python
secure: bool = Field(default=False)  # Set True in production (HTTPS)
```

**위험**: HTTPS 환경에서도 Secure 플래그가 False면 쿠키가 HTTP로 전송될 수 있음

**권장 조치**:
1. 프로덕션 배포 가이드에 `COOKIE_SECURE=true` 설정 명시
2. 또는 환경 감지하여 자동 설정

---

### 5. CORS 설정 미적용

**현황**: CORS 미들웨어가 명시적으로 설정되어 있지 않음

**권장 조치**: 프로덕션 환경에서 허용된 오리진만 접근 가능하도록 CORS 설정

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-domain.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

---

## 🟡 낮음 (Low)

### 6. 테스트 코드 내 하드코딩된 자격 증명

**위치**: `src/tests/`, `.archive/backend/tests/`

```python
# 다수의 테스트 파일에서
json={"username": "admin", "password": "admin"}
password_hash="hash"
```

**권장 조치**: 테스트용이므로 위험도 낮음, 단 실제 시스템에서 사용되는 값과 다르게 유지

---

### 7. innerHTML 사용 (XSS 위험 완화됨)

**위치**: `src/codehub/app/static/js/detail-panel.js`

```javascript
memoEl.innerHTML = DOMPurify.sanitize(marked.parse(workspace.memo));
```

**현황**: DOMPurify로 sanitize 처리됨 - **양호**

**권장 조치**: 현재 설정 유지, DOMPurify 버전 최신 유지

---

## ✅ 양호한 보안 사항

### 1. 비밀번호 해싱 - Argon2id ✅
```python
# src/codehub/core/security.py
_hasher = PasswordHasher()  # Argon2id 사용
```
최신 권장 해싱 알고리즘 사용

### 2. 세션 쿠키 보안 설정 ✅
```python
# src/codehub/app/api/v1/auth.py:99-107
response.set_cookie(
    httponly=True,      # JavaScript 접근 차단
    samesite="lax",     # CSRF 보호
    path="/",
    max_age=...,
)
```

### 3. SQL Injection 방지 ✅
- SQLAlchemy ORM 사용
- 파라미터화된 쿼리 사용
- Raw SQL은 `text()` 함수와 바인드 변수 사용

### 4. 로그인 시도 제한 (Account Lockout) ✅
```python
# src/codehub/app/config.py:232-235
lockout_threshold: int = Field(default=5)
lockout_base: int = Field(default=30)
lockout_max: int = Field(default=1800)  # 30분
```

### 5. 입력 검증 - Pydantic 모델 ✅
```python
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
```

### 6. Docker Socket Proxy 사용 ✅
- Docker 소켓 직접 노출 대신 `tecnativa/docker-socket-proxy` 사용
- 필요한 API만 허용 (CONTAINERS, NETWORKS, IMAGES 등)

### 7. .gitignore에 민감 파일 포함 ✅
```gitignore
.env
.envrc
.venv
```

### 8. 명령어 인젝션 취약점 없음 ✅
- `subprocess`, `os.system`, `eval()`, `exec()` 사용 없음 (테스트 제외)

---

## 공개 전 체크리스트

- [ ] `docker-compose.yml`에서 하드코딩된 비밀번호 제거
- [ ] `config.py`에서 기본 자격 증명 제거
- [ ] `main.py`에서 기본 admin 비밀번호 제거
- [ ] `.env.example` 파일 생성
- [ ] README에 보안 설정 가이드 추가
- [ ] CORS 정책 설정 확인
- [ ] HTTPS 환경에서 `COOKIE_SECURE=true` 설정 문서화
- [ ] 의존성 취약점 스캔 (`pip-audit` 또는 `safety`)

---

## 환경변수 목록 (.env.example 용)

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname

# Redis
REDIS_URL=redis://host:6379

# S3/MinIO
S3_ENDPOINT=http://host:9000
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key

# Admin
ADMIN_PASSWORD=your-secure-password

# Cookie (production)
COOKIE_SECURE=true

# Docker
DOCKER_HOST=tcp://docker-proxy:2375
```

---

---

## 🌐 웹 브라우저 보안 분석

외부 공개 후 브라우저에서 사용할 때의 보안 취약점 분석입니다.

### 🟠 중간 (Medium) - 웹 취약점

#### 8. CSP (Content-Security-Policy) 헤더 미설정

**위치**: `src/codehub/app/static/index.html`

**현황**:
- Content-Security-Policy 헤더가 설정되어 있지 않음
- 외부 CDN에서 스크립트 로드 중

```html
<!-- 현재 외부 CDN 의존 -->
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.6/purify.min.js"></script>
```

**위험**:
- XSS 공격 시 인라인 스크립트 실행 가능
- CDN 해킹 시 공급망 공격(Supply Chain Attack) 가능

**권장 조치**:
```python
# FastAPI 미들웨어로 CSP 헤더 추가
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response
```

---

#### 9. 외부 CDN 의존성 (공급망 공격 위험)

**현황**: 3개의 외부 CDN에서 JavaScript 라이브러리 로드

| 라이브러리 | CDN | 용도 |
|-----------|-----|------|
| Tailwind CSS | cdn.tailwindcss.com | 스타일링 |
| Marked | cdn.jsdelivr.net | Markdown 파싱 |
| DOMPurify | cdnjs.cloudflare.com | XSS 방지 |

**위험**: CDN이 해킹되면 악성 스크립트가 주입될 수 있음

**권장 조치**:
1. **SRI (Subresource Integrity) 해시 추가**:
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.6/purify.min.js"
        integrity="sha384-XXXXX"
        crossorigin="anonymous"></script>
```

2. **또는 로컬 번들링**: npm으로 설치 후 빌드 시 번들링

---

#### 10. API Rate Limiting 미적용

**현황**:
- ✅ 로그인 API: Account lockout 적용됨
- ❌ 워크스페이스 API: Rate limiting 없음

**위험**:
- 워크스페이스 대량 생성 공격
- API 남용으로 인한 서비스 거부

**권장 조치**:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/v1/workspaces")
@limiter.limit("10/minute")
async def create_workspace(...):
    ...
```

---

### ✅ 양호한 웹 보안 사항

#### 1. IDOR (Insecure Direct Object Reference) 방지 ✅

```python
# src/codehub/services/workspace_service.py:100-101
if user_id is not None and workspace.owner_user_id != user_id:
    raise ForbiddenError()
```
- 모든 워크스페이스 접근 시 소유자 검증
- 다른 사용자의 워크스페이스 접근 불가

#### 2. XSS 방지 - DOMPurify ✅

```javascript
// src/codehub/app/static/js/detail-panel.js:45
memoEl.innerHTML = DOMPurify.sanitize(marked.parse(workspace.memo));
```
- 사용자 입력(memo)을 렌더링할 때 sanitize 적용
- Markdown 렌더링 시에도 보호됨

#### 3. CSRF 기본 보호 ✅

```python
# src/codehub/app/api/v1/auth.py:103
samesite="lax",  # Cross-site 요청 시 쿠키 전송 제한
```
- `SameSite=Lax` 쿠키 설정으로 기본적인 CSRF 보호

#### 4. 프록시 인증/인가 ✅

```python
# src/codehub/app/proxy/router.py:69-70
user_id = await get_user_id_from_session(db, session)
workspace = await get_workspace_for_user(db, workspace_id, user_id)
```
- 워크스페이스 프록시 접근 시 세션 + 소유자 이중 검증
- SSRF 방지: 고정된 내부 컨테이너로만 프록시

#### 5. SSE 채널 격리 ✅

```python
# src/codehub/app/api/v1/events.py:114
channel = f"{_channel_config.sse_prefix}:{user_id}"
```
- 사용자별 독립된 SSE 채널
- 다른 사용자의 이벤트 수신 불가

#### 6. 입력 길이 제한 ✅

```python
# src/codehub/app/api/v1/workspaces.py:27-29
name: str = Field(min_length=1, max_length=255)
description: str | None = Field(default=None, max_length=500)
image_ref: str = Field(default=..., max_length=512)
```
- 모든 입력에 길이 제한 적용
- 버퍼 오버플로우 및 DoS 방지

---

## OWASP Top 10 점검 결과

| # | 취약점 | 상태 | 비고 |
|---|--------|------|------|
| A01 | Broken Access Control | ✅ 양호 | 소유자 검증, 세션 관리 |
| A02 | Cryptographic Failures | ✅ 양호 | Argon2id, 환경변수 주입 필요 |
| A03 | Injection | ✅ 양호 | ORM 사용, Pydantic 검증 |
| A04 | Insecure Design | ✅ 양호 | 프록시 인증, 권한 분리 |
| A05 | Security Misconfiguration | 🟠 보통 | CSP 미설정, CORS 확인 필요 |
| A06 | Vulnerable Components | 🟠 보통 | 외부 CDN 의존성 |
| A07 | Auth Failures | ✅ 양호 | Lockout, 세션 만료 |
| A08 | Data Integrity Failures | 🟠 보통 | SRI 해시 미적용 |
| A09 | Logging Failures | ✅ 양호 | 구조화된 로깅, 추적 ID |
| A10 | SSRF | ✅ 양호 | 고정된 upstream만 프록시 |

---

## 추가 체크리스트 (웹 보안)

- [ ] CSP 헤더 추가
- [ ] 외부 CDN에 SRI 해시 적용
- [ ] API Rate Limiting 구현 (slowapi 등)
- [ ] X-Frame-Options 헤더 추가 (clickjacking 방지)
- [ ] X-Content-Type-Options: nosniff 헤더 추가

---

## 결론

CodeHub는 전반적으로 양호한 보안 아키텍처를 가지고 있습니다. 주요 보안 메커니즘(Argon2id 해싱, 세션 보안, ORM 사용 등)이 적절히 구현되어 있습니다.

**공개 전 필수 조치**:
1. docker-compose.yml 및 코드 내 하드코딩된 자격 증명을 환경변수로 대체
2. CSP 헤더 추가 권장
3. 외부 CDN에 SRI 해시 적용 권장

**웹 브라우저 사용 시 보안 수준**: 🟢 양호 (인증/인가/XSS 방지 잘 되어 있음)
