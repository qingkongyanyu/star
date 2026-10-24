"""应用配置：从环境变量 / backend/.env 读取。

字段与 backend/.env.example 一一对应，说明见 docs/02-后端/07-配置与安全.md。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env 固定位置（文件相对，而非 cwd 相对），使后端可从任意目录启动。
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # 应用
    app_name: str = "data-mid-platform"
    debug: bool = False
    env: str = "dev"

    # 数据库（双库隔离：task_repo 平台库 + artifact_repo 资产库）
    database_url: str = "mysql+pymysql://midplat:secret@127.0.0.1:3306/task_repo"
    artifact_database_url: str = "mysql+pymysql://midplat:secret@127.0.0.1:3306/artifact_repo"

    # Redis / Kafka
    redis_url: str = "redis://127.0.0.1:6379/0"
    kafka_bootstrap: str = "127.0.0.1:9092"
    kafka_topic: str = "task.events"
    # 事件总线：默认进程内广播（本地/单 worker）；多副本部署设 EVENT_BUS_REDIS=true 走 Redis Pub/Sub。
    event_bus_redis: bool = False
    event_bus_redis_channel: str = "midplat.events"

    # MinIO（对象存储，本地/云 S3 兼容）
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "artifacts"
    minio_region: str = "us-east-1"  # 显式 region 使预签名跳过分区查询（本地离线可用）

    # LLM（OpenAI 兼容）
    llm_base_url: str = "https://api.example.com/v1"
    llm_api_key: str = "sk-xxx"
    llm_model: str = "claude-sonnet-5"
    embedding_model: str = "text-embedding-3-small"
    # LLM provider 级韧性（v2.2b）：单次 HTTP 超时 + 内置重试（指数退避）。
    # 与 Agent 级超时/重试叠加但不冲突——wait_for 会在总量上兜底。
    llm_timeout_seconds: float = 90.0          # 单次补全/向量请求超时
    llm_max_retries: int = 2                    # openai 客户端连接/5xx/429 自动重试次数

    # 认证
    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 120
    password_hasher: str = "argon2"

    # 商业版初始化：仅当库中不存在管理员时使用；未配置则不会创建任何默认账号。
    # 生产环境必须显式配置，或使用 `python -m app.cli create-admin` 初始化。
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    password_min_length: int = 10

    # 登录防爆破：同一账号/来源 IP 连续失败达到阈值后锁定一段时间（Redis 多实例共享，内存兜底）
    login_max_attempts: int = 5
    login_ip_max_attempts: int = 30
    login_lockout_seconds: int = 900

    # 异步执行：Agent 上游资产并发拉取上限（每个输入独立会话）
    agent_input_fetch_concurrency: int = 4

    # CORS
    cors_origins: str = "http://localhost:5173"

    # 业务阈值（取数引擎真实生效，见 infra/datasource.py sql_execute）
    max_query_rows: int = 5_000  # 单次只读 SELECT 取数行数上限（超出截断并标注 truncated）

    # 调度租约/心跳（多 worker + 崩溃自愈；单位秒）
    scheduler_lease_ttl: int = 120  # 任务级驱动态 RUNNING/PLANNING 租约过期阈值
    scheduler_scan_interval: float = 1.0  # 调度器空闲扫描周期（秒）
    scheduler_max_concurrent: int = 4  # 单进程同时驱动任务数上限
    llm_input_max_chars: int = 12_000  # 拼入 LLM 提示词的单个上游资产截断上限（原 _MAX_INPUT_CHARS=3000）

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
