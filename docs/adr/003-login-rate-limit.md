# ADR-003: Login Rate Limiting

## 상태
Accepted

## 컨텍스트
브루트포스 공격으로부터 사용자 계정을 보호해야 합니다.
- 공격자가 비밀번호를 무차별 대입할 수 있음
- 단순 IP 기반 차단은 프록시/VPN으로 우회 가능
- 사용자 경험을 해치지 않으면서 보안 강화 필요

## 결정

### 방식: DB 기반 Exponential Backoff

| 항목 | 선택 | 이유 |
|------|------|------|
| 저장소 | DB (User 테이블) | 서버 재시작 시에도 유지, 분산 환경 대비 |
| 추적 단위 | 사용자별 | IP 기반보다 정확, 공유 IP 문제 없음 |
| 지연 방식 | Exponential Backoff | 정상 사용자 영향 최소화, 공격 비용 급증 |

### User 모델 추가 컬럼

```python
failed_login_attempts: int = Field(default=0)
locked_until: datetime | None = Field(default=None)
last_failed_at: datetime | None = Field(default=None)
```

### 지연 시간 공식

```
실패 횟수 < 5: 지연 없음
실패 횟수 >= 5: delay = min(30 * 2^(attempts-5), 1800) 초
```

| 실패 횟수 | 지연 시간 |
|-----------|-----------|
| 1-4 | 0초 |
| 5 | 30초 |
| 6 | 60초 (1분) |
| 7 | 120초 (2분) |
| 8 | 300초 (5분) |
| 9 | 600초 (10분) |
| 10+ | 1800초 (30분, 최대) |

### 리셋 조건

| 조건 | 동작 |
|------|------|
| 로그인 성공 | 즉시 리셋 (attempts=0, locked_until=NULL) |
| 잠금 시간 경과 | 로그인 시도 허용 (attempts 유지) |

### API 응답

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 120

{
  "error": {
    "code": "TOO_MANY_REQUESTS",
    "message": "Too many failed attempts. Try again in 2 minutes."
  }
}
```

## 결과

### 장점
- 브루트포스 공격 비용 급증 (10회 시도에 30분 대기)
- 정상 사용자는 4회까지 즉시 재시도 가능
- 서버 재시작 시에도 잠금 상태 유지

### 단점
- DB 쓰기 증가 (실패마다 UPDATE)
- 분산 환경에서 DB 동기화 필요 (MVP에서는 단일 인스턴스)

### 대안 (고려했으나 선택 안 함)

| 대안 | 미선택 이유 |
|------|------------|
| IP 기반 Rate Limit | 공유 IP 문제, 프록시 우회 가능 |
| In-Memory 저장 | 서버 재시작 시 리셋, 분산 환경 불가 |
| Account Lockout | 정상 사용자 불편, DoS 공격 벡터 |
| CAPTCHA | 구현 복잡도, UX 저하 |

## 참고 자료
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [Rate Limiting Best Practices](https://cloud.google.com/architecture/rate-limiting-strategies-techniques)
