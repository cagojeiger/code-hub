# Metrics

Code-Hub의 Prometheus 메트릭 문서입니다.

## 메트릭 카테고리

- [Workspace Lifecycle](./01-workspace-lifecycle.md) - 워크스페이스 상태 전환, 작업 성공률, TTL 만료
- [Coordinator](./02-coordinator.md) - 제어 루프 성능, 리더 선출, reconcile 큐
- [WebSocket](./03-websocket.md) - 프록시 성능, 연결 상태, 메시지 latency
- [Database](./04-database.md) - 연결 풀 상태, DB 가용성

## 빠른 시작

### 메트릭 확인

```bash
curl http://localhost:18000/metrics
```

### Prometheus 접속

```
http://localhost:19090
```

Prometheus UI에서 메트릭 쿼리 및 그래프를 확인할 수 있습니다.

### Grafana 접속

```
http://localhost:13000
```

- **Username**: `admin`
- **Password**: `qwer1234`

## 유용한 쿼리 및 알림

- [PromQL 쿼리 모음](./05-prometheus-queries.md) - 자주 사용하는 PromQL 쿼리
- [알림 규칙](./06-alerting-rules.md) - Prometheus 알림 규칙 예시

## 메트릭 설계 원칙

### 카디널리티 관리

- **workspace_id를 레이블로 사용하지 않음**: 워크스페이스가 수백~수천 개로 늘어나면 메트릭이 폭발합니다.
- **집계된 메트릭 사용**: 상태별, 작업별로 집계하여 메트릭 수를 ~100개로 유지합니다.

### 멱등성

- 모든 메트릭 업데이트는 멱등적입니다 (동일한 이벤트를 여러 번 기록해도 안전).
- Gauge는 주기적으로 전체 재설정 후 업데이트합니다.

### Multiprocess 지원

- `multiprocess_mode="livesum"`: Gauge는 모든 워커의 값을 합산
- `multiprocess_mode="max"`: Leader status는 최대값 사용 (한 워커만 1, 나머지 0)

## 메트릭 업데이트 주기

- **Counter/Histogram**: 이벤트 발생 시 즉시 업데이트
- **Gauge (Workspace count)**: 10초마다 주기적 업데이트 (설정 가능)
- **Gauge (DB pool)**: 10초마다 주기적 업데이트

## 문제 해결

### 메트릭이 나타나지 않음

1. Prometheus가 control-plane을 scrape하는지 확인:
   ```
   http://localhost:19090/targets
   ```

2. 메트릭 엔드포인트가 응답하는지 확인:
   ```bash
   curl http://localhost:18000/metrics
   ```

3. 로그에서 메트릭 업데이트 에러 확인:
   ```bash
   docker-compose logs control-plane | grep "workspace count metrics"
   ```

### Prometheus가 scrape하지 못함

- `docker-compose.yml`에서 `prometheus` 서비스가 `control-plane:8000`에 접근할 수 있는지 확인
- 네트워크 설정 확인: 같은 Docker Compose 네트워크에 있어야 함

### 메트릭 값이 이상함

- **Gauge가 계속 증가만 함**: Reset 로직이 누락되었을 수 있습니다. `_update_workspace_count_metrics()`에서 0으로 초기화하는지 확인
- **Counter가 감소함**: Counter는 절대 감소하지 않습니다. Gauge를 사용해야 합니다.

## 다음 단계

1. [Workspace 메트릭 이해](./01-workspace-lifecycle.md)
2. [Coordinator 메트릭 이해](./02-coordinator.md)
3. [유용한 쿼리 학습](./05-prometheus-queries.md)
