# Roadmap 002: M2 (Draft)

## Status: Draft

> M2는 완성형 아키텍처를 구축합니다. M3에서는 Instance Controller와 Storage Provider 구현체만 교체하여 K8s에서 동작합니다.

---

## 마일스톤 개요

| Milestone | 목표 | 핵심 변경 |
|-----------|------|----------|
| M1 (MVP) | 기본 동작 | local-docker + bind mount |
| **M2** | 완성형 아키텍처 | Reconciler + Volume + Object Storage + TTL |
| M3 | K8s 배포 | Instance Controller, Storage Provider 구현체만 교체 |

---

## 핵심 변경 사항

### 1. 상태 모델 확장

```
        컴퓨트    Volume    Object Storage
RUNNING   ✓         ✓           -
WARM      -         ✓           -
COLD      -         -           ✓
```

**전환 흐름:**
```
RUNNING ──(TTL1 만료)──> WARM ──(TTL2 만료)──> COLD
    ↑                      ↑                     │
    └──(Auto-wake)─────────┴──────(수동 복원)────┘
```

- **RUNNING**: 컨테이너 실행 중, Volume 마운트됨
- **WARM**: 컨테이너 없음, Volume 유지 (빠른 재시작 가능)
- **COLD**: Volume 없음, Object Storage에 아카이브 (복원 필요)

### 2. Storage 아키텍처 변경

**현재 (M1):**
```
Bind Mount: host dir → container /home/coder
```

**M2:**
```
Docker Volume ──(WARM→COLD)──> Object Storage (MinIO/S3)
     ↑                              │
     └────────(COLD→WARM)───────────┘
```

- Bind Mount → Docker Volume 전환 (K8s PVC와 유사)
- Object Storage 연동 (MinIO 자체 호스팅 또는 S3 호환)

### 3. Reconciler 구현 (ADR-006, ADR-007)

- **선언적 패턴**: API는 desired_state만 설정, Reconciler가 수렴
- **Leader Election**: PostgreSQL Advisory Lock
- **WorkQueue**: workspace 단위 직렬화
- **Hints**: Redis Pub/Sub (즉시 반응)
- **TTL 기반 자동 전환**: RUNNING → WARM → COLD

### 4. Auto-wake (WARM 상태만)

```
/w/{workspace_id}/ 접속
    ↓
상태 확인
    ↓
├── RUNNING: 바로 프록시
├── WARM: 로딩 페이지 → 컨테이너 시작 → 프록시 (빠름)
├── COLD: 502 + "복원 필요" 안내 (수동 복원 후 접속)
└── 그 외: 502
```

**로딩 페이지:**
- WARM → RUNNING 전환 중 사용자에게 대기 UI 제공
- SSE로 상태 변경 알림 → 자동 리다이렉트

---

## Task 분해 (초안)

### Phase 1: 인프라 기반
- [ ] Docker Volume 기반 Storage Provider 구현
- [ ] Object Storage (MinIO) 연동
- [ ] Volume ↔ Object Storage 아카이브/복원

### Phase 2: 상태 모델
- [ ] DB 스키마: WARM, COLD 상태 추가
- [ ] desired_state 컬럼 추가
- [ ] TTL 관련 컬럼: last_access_at, warm_ttl, cold_ttl (워크스페이스별 설정 가능)

### Phase 3: Reconciler
- [ ] Leader Election 구현
- [ ] WorkQueue 구현 (직렬화 + dirty flag)
- [ ] Hints 연동 (Redis Pub/Sub)
- [ ] TTL 기반 상태 전환 로직

### Phase 4: API/프록시
- [ ] API 선언형 전환 (desired_state 설정)
- [ ] Auto-wake 구현 (프록시에서 WARM 감지 시 시작)
- [ ] 로딩 페이지 UI
- [ ] 상태별 프록시 동작

### Phase 5: 테스트
- [ ] E2E: 상태 전환 시나리오
- [ ] E2E: Auto-wake 시나리오
- [ ] Chaos: 프로세스 kill 후 복구

---

## 인터페이스 확정 (M3 대비)

### Instance Controller
```python
class InstanceController(ABC):
    async def start(workspace_id, image_ref, volume_name) -> None
    async def stop(workspace_id) -> None
    async def delete(workspace_id) -> None
    async def get_status(workspace_id) -> InstanceStatus
    async def resolve_upstream(workspace_id) -> UpstreamInfo
```

### Storage Provider
```python
class StorageProvider(ABC):
    # Volume 관리
    async def create_volume(workspace_id) -> VolumeName
    async def delete_volume(workspace_id) -> None
    async def get_volume_status(workspace_id) -> VolumeStatus

    # Archive (WARM ↔ COLD)
    async def archive_to_object_storage(workspace_id) -> ArchiveKey
    async def restore_from_object_storage(workspace_id, archive_key) -> VolumeName
```

---

## 미결정 사항

1. **TTL 기본값**: RUNNING→WARM (30분?), WARM→COLD (7일?)
2. **Object Storage**: MinIO 자체 호스팅 vs S3 호환 API 선택
3. **아카이브 포맷**: tar.gz? 다른 포맷?

---

## 다음 단계

1. 상세 분석 및 피드백 반영
2. spec/ 업데이트 (WARM/COLD 상태, Auto-wake 등)
3. 정식 버전 002-m2.md 생성

---

## References

- [spec/](../spec/) - 상세 스펙
- [ADR-006: Reconciler 패턴 채택](../adr/006-reconciler-pattern.md)
- [ADR-007: Reconciler 구현 전략](../adr/007-reconciler-implementation.md)
- [ADR-005: Redis Pub/Sub](../adr/005-redis-pubsub.md)
