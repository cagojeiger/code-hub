# CodeHub 보안 감사 보고서

**감사일**: 2026-01-22
**대상 버전**: v0.2.0
**감사자**: AI Security Audit

---

## 요약

| 심각도 | 발견 항목 수 |
|--------|-------------|
| 🔴 높음 (High) | 2 |
| 🟠 중간 (Medium) | 3 |
| 🟡 낮음 (Low) | 2 |
| ✅ 양호 | 8 |

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

## 결론

CodeHub는 전반적으로 양호한 보안 아키텍처를 가지고 있습니다. 주요 보안 메커니즘(Argon2id 해싱, 세션 보안, ORM 사용 등)이 적절히 구현되어 있습니다.

**공개 전 필수 조치**: docker-compose.yml 및 코드 내 하드코딩된 자격 증명을 환경변수로 대체해야 합니다. 이 작업만 완료되면 공개 배포에 적합합니다.
