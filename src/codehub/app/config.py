"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """PostgreSQL connection pool configuration.

    Scale guide (N = concurrent workspaces):
      100 → pool=20, overflow=50 (total=70)
      300 → pool=60, overflow=150 (total=210)
      500 → pool=100, overflow=250 (total=350)
    """

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    url: str = Field(default="postgresql+asyncpg://codehub:codehub@postgres:5432/codehub")
    echo: bool = False
    # 100 concurrent workspaces baseline
    # Formula: pool_size = N // 5, max_overflow = N // 2
    pool_size: int = 20
    max_overflow: int = 50


class RedisConfig(BaseSettings):
    """Redis connection pool configuration.

    Scale guide (N = concurrent workspaces):
      100 → max_connections=150
      300 → max_connections=350
      500 → max_connections=550
    """

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(default="redis://redis:6379")
    # 100 concurrent workspaces baseline
    # Formula: max_connections = N + 50
    max_connections: int = Field(default=150)


class RedisChannelConfig(BaseSettings):
    """Redis PUB/SUB channel naming configuration.

    Channel naming pattern: {prefix}:{identifier}
    - SSE events: codehub:sse:{user_id}
    - Wake notifications: codehub:wake:{target}
    """

    model_config = SettingsConfigDict(env_prefix="REDIS_CHANNEL_")

    sse_prefix: str = Field(default="codehub:sse")
    wake_prefix: str = Field(default="codehub:wake")


class StorageConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S3_")

    endpoint_url: str = Field(default="http://minio:9000", validation_alias="S3_ENDPOINT")
    # Internal endpoint for containers (e.g., storage-job) running on Docker network.
    # Defaults to endpoint_url. Set S3_ENDPOINT_INTERNAL for local tests.
    internal_endpoint_url: str = Field(
        default="http://minio:9000", validation_alias="S3_ENDPOINT_INTERNAL"
    )
    access_key: str = Field(default="codehub", validation_alias="S3_ACCESS_KEY")
    secret_key: str = Field(default="codehub123", validation_alias="S3_SECRET_KEY")
    bucket_name: str = Field(default="codehub-archives", validation_alias="S3_BUCKET")


class RuntimeConfig(BaseSettings):
    """Runtime-agnostic common configuration.

    These settings are shared between Docker and K8s runtimes.
    """

    model_config = SettingsConfigDict(env_prefix="RUNTIME_")

    resource_prefix: str = Field(default="codehub-ws-")
    container_port: int = Field(default=8080)
    default_image: str = Field(default="cagojeiger/code-server:4.107.1")
    storage_job_image: str = Field(default="codehub/storage-job:latest")


class DockerConfig(BaseSettings):
    """Docker-specific configuration for workspace containers."""

    model_config = SettingsConfigDict(env_prefix="DOCKER_")

    network_name: str = Field(default="codehub-net")
    coder_uid: int = Field(default=1000)
    coder_gid: int = Field(default=1000)

    # DNS settings for VPN/network stability
    # Docker Desktop 4.48.0+ has DNS routing issues with VPN
    # See: https://github.com/docker/for-mac/issues/4751
    dns_servers: list[str] = Field(default=["8.8.8.8", "1.1.1.1"])
    dns_options: list[str] = Field(default=["use-vc"])  # Force TCP for DNS

    # Timeout settings
    api_timeout: float = Field(default=30.0)  # seconds (Docker API calls)
    image_pull_timeout: float = Field(default=600.0)  # seconds (10 minutes)
    container_wait_timeout: int = Field(default=300)  # seconds (5 minutes)
    job_timeout: int = Field(default=300)  # seconds (storage job)


class TtlConfig(BaseSettings):
    """TTL configuration for workspace lifecycle."""

    model_config = SettingsConfigDict(env_prefix="TTL_")

    standby_seconds: int = Field(default=600)  # 10분 (테스트용), 프로덕션: 10800 (3시간)
    archive_seconds: int = Field(default=1800)  # 30분 (테스트용), 프로덕션: 86400 (24시간)


class LimitsConfig(BaseSettings):
    """Resource limits configuration."""

    model_config = SettingsConfigDict(env_prefix="LIMITS_")

    max_running_per_user: int = Field(default=3)


class CookieConfig(BaseSettings):
    """Cookie configuration for session management."""

    model_config = SettingsConfigDict(env_prefix="COOKIE_")

    secure: bool = Field(default=False)  # Set True in production (HTTPS)


class ObserverConfig(BaseSettings):
    """Observer coordinator configuration."""

    model_config = SettingsConfigDict(env_prefix="OBSERVER_")

    timeout_s: float = Field(default=5.0)  # API call timeout per resource type


class CacheConfig(BaseSettings):
    """Local TTL cache configuration.

    Scale guide (N = concurrent workspaces):
      100 → maxsize=1000 (10x headroom for bursts)
      300 → maxsize=1000 (sufficient for most cases)
      500 → maxsize=1000 (LRU eviction handles overflow)
    """

    model_config = SettingsConfigDict(env_prefix="CACHE_")

    # 100 concurrent workspaces baseline
    # maxsize=1000 provides 10x headroom for burst traffic
    maxsize: int = Field(default=1000)
    ttl: float = Field(default=3.0)  # seconds (page load duration)


class ProxyConfig(BaseSettings):
    """HTTP/WebSocket proxy configuration.

    Scale guide (N = concurrent workspaces):
      100 → max_connections=100
      300 → max_connections=300
      500 → max_connections=500
    """

    model_config = SettingsConfigDict(env_prefix="PROXY_")

    # HTTP timeouts (fixed, not scale-dependent)
    timeout_total: float = Field(default=30.0)  # seconds
    timeout_connect: float = Field(default=10.0)  # seconds
    timeout_pool: float = Field(default=5.0)  # seconds (wait for connection from pool)

    # 100 concurrent workspaces baseline
    # Formula: max_connections = N (1 connection per active workspace)
    max_connections: int = Field(default=100)
    max_keepalive: int = Field(default=20)
    keepalive_expiry: float = Field(default=30.0)  # seconds

    # WebSocket settings
    ws_ping_interval: float = Field(default=20.0)  # seconds
    ws_ping_timeout: float = Field(default=20.0)  # seconds
    ws_max_size: int = Field(default=16 * 1024 * 1024)  # 16MB
    ws_max_queue: int = Field(default=64)


class CoordinatorConfig(BaseSettings):
    """Coordinator timing configuration."""

    model_config = SettingsConfigDict(env_prefix="COORDINATOR_")

    # Base coordinator intervals
    idle_interval: float = Field(default=15.0)  # seconds (idle polling)
    active_interval: float = Field(default=1.0)  # seconds (active polling)
    min_interval: float = Field(default=1.0)  # seconds (minimum interval)
    active_duration: float = Field(default=30.0)  # seconds (stay active after wake)

    # Leader election
    leader_retry_interval: float = Field(default=5.0)  # seconds
    verify_interval: float = Field(default=10.0)  # seconds
    verify_jitter: float = Field(default=0.3)  # 30% jitter
    leader_timeout: float = Field(default=5.0)  # seconds (advisory lock timeout)

    # WC specific
    operation_timeout: int = Field(default=300)  # seconds (5 minutes)

    # TTL specific
    ttl_interval: float = Field(default=60.0)  # seconds (1 minute)

    # GC specific
    gc_interval: float = Field(default=14400.0)  # seconds (4 hours)


class SSEConfig(BaseSettings):
    """Server-Sent Events configuration."""

    model_config = SettingsConfigDict(env_prefix="SSE_")

    heartbeat_interval: float = Field(default=30.0)  # seconds
    stream_maxlen: int = Field(default=1000)  # max messages per stream
    xread_block_ms: int = Field(default=1000)  # milliseconds
    xread_count: int = Field(default=10)  # messages per read
    xread_timeout: float = Field(default=2.0)  # seconds


class SecurityConfig(BaseSettings):
    """Security and session configuration."""

    model_config = SettingsConfigDict(env_prefix="SECURITY_")

    session_ttl: int = Field(default=86400)  # seconds (24 hours)
    lockout_threshold: int = Field(default=5)  # attempts before lockout
    lockout_base: int = Field(default=30)  # seconds (base lockout duration)
    lockout_max: int = Field(default=1800)  # seconds (30 minutes max)


class ActivityConfig(BaseSettings):
    """Activity tracking configuration."""

    model_config = SettingsConfigDict(env_prefix="ACTIVITY_")

    flush_interval: int = Field(default=30)  # seconds
    throttle_sec: float = Field(default=1.0)  # seconds (per-workspace throttle)


class MetricsConfig(BaseSettings):
    """Prometheus metrics configuration."""

    model_config = SettingsConfigDict(env_prefix="METRICS_")

    enabled: bool = Field(default=True)
    multiproc_dir: str = Field(default="/tmp/prometheus_metrics")
    update_interval: float = Field(default=10.0)  # seconds


class LoggingConfig(BaseSettings):
    """Logging configuration.

    Standard fields added to all logs:
    - schema_version: Log schema version for backwards compatibility
    - service: Service name (codehub-control-plane)

    Rate limiting:
    - Prevents log storms from repeated messages
    - ERROR logs bypass rate limiting (always logged)
    """

    model_config = SettingsConfigDict(env_prefix="LOGGING_")

    level: str = Field(default="INFO")
    schema_version: str = Field(default="1.0")
    slow_threshold_ms: float = Field(default=1000.0)  # 1초 이상이면 WARN
    rate_limit_per_minute: int = Field(default=100)  # 동일 메시지 분당 최대 횟수
    service_name: str = Field(default="codehub-control-plane")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_",
        env_nested_delimiter="__",
    )

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    redis_channel: RedisChannelConfig = Field(default_factory=RedisChannelConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    ttl: TtlConfig = Field(default_factory=TtlConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    cookie: CookieConfig = Field(default_factory=CookieConfig)
    observer: ObserverConfig = Field(default_factory=ObserverConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    coordinator: CoordinatorConfig = Field(default_factory=CoordinatorConfig)
    sse: SSEConfig = Field(default_factory=SSEConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


@lru_cache
def get_settings() -> Settings:
    return Settings()
