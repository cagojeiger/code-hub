# Glossary

code-hub 프로젝트에서 사용하는 핵심 용어 정의입니다.

---

## C

### Control Plane
API(`/api/v1/*`) 및 워크스페이스 프록시(`/w/{workspace_id}/*`)를 제공하는 서버. Workspace 메타데이터 관리를 담당합니다.

---

## H

### Home Store
홈 데이터 원본 저장소. 로컬 환경에서는 host directory, 클라우드 환경에서는 object storage로 구현됩니다.

### Home Store Key
Home Store 내 논리 경로 키. DB에는 절대경로가 아닌 논리 키만 저장됩니다.
- 패턴: `users/{user_id}/workspaces/{workspace_id}/home`
- 로컬: `base_dir + home_store_key`를 bind mount
- 클라우드: `{home_store_key}/home.tar.zst` 같은 오브젝트 키로 사용

### HomeStoreBackend
홈 스토어 구현체 인터페이스. `local-dir`(로컬) 또는 `object-store`(클라우드) 백엔드를 제공합니다.

---

## R

### Runner
Workspace Instance 생명주기(lifecycle)를 관리하는 컴포넌트. 로컬에서는 Docker를 사용하고, HomeStoreBackend 결과를 `/home/coder`에 마운트합니다.

---

## W

### Workspace
메타데이터 단위(DB 레코드). 이름, 설명, 메모, Home Store Key 등의 정보를 포함합니다.

### Workspace Instance
실행 중인 code-server 인스턴스. 로컬 환경에서는 Docker 컨테이너, 클라우드 환경에서는 K8s Pod으로 구현됩니다. Workspace 1개당 1개의 Instance가 매핑됩니다.
