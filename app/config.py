"""
配置管理模块
使用 pydantic-settings 管理环境变量
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPORTED_TRAVEL_AGENT_MODES = {"supervisor", "supervisor_subagents"}
DEGRADED_PLANNING_REASON = "no_llm_or_provider"
DEGRADED_PLANNING_MARKER = "planning_degraded:no_llm_or_provider"

# ======== 环境兼容性修复（课件没有，但本地环境必需） ========
# 问题：本机开了代理（如 Clash 端口 7897），代理会把发往阿里云国内服务的请求
#       转发到海外节点，导致 dashscope.aliyuncs.com 的 SSL 握手失败。
# 修复：把阿里云域名加入 NO_PROXY，让相关请求绕过代理直连。
_no_proxy = os.environ.get("NO_PROXY", "")
_aliyun_domains = "aliyuncs.com,dashscope.aliyuncs.com,.aliyuncs.com,localhost,127.0.0.1"
if _aliyun_domains not in _no_proxy:
    new_value = f"{_no_proxy},{_aliyun_domains}" if _no_proxy else _aliyun_domains
    os.environ["NO_PROXY"] = new_value
    os.environ["no_proxy"] = new_value
# ======== 修复结束 ========


class Settings(BaseSettings):
    """应用配置"""

    # ============== 应用基础配置 ==============
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=False, alias="DEBUG")
    jwt_secret_key: str = Field(
        default="development-only-change-me",
        alias="JWT_SECRET_KEY",
    )
    travel_agent_mode: str = Field(default="supervisor_subagents", alias="TRAVEL_AGENT_MODE")
    allow_legacy_fallback: bool = Field(default=False, alias="ALLOW_LEGACY_FALLBACK")
    enable_external_tools: bool = Field(default=False, alias="ENABLE_EXTERNAL_TOOLS")

    # ============== LLM 配置 ==============
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")
    qwen_model_name: str = Field(default="qwen-max", alias="QWEN_MODEL_NAME")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL"
    )
    qwen_temperature: float = 0.7
    qwen_max_tokens: int = 8000

    # ============== LangSmith 配置 ==============
    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="travel-planner-dev", alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(default=True, alias="LANGSMITH_TRACING")
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        alias="LANGSMITH_ENDPOINT"
    )

    # ============== 数据库配置 ==============
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="ai_travel_db", alias="POSTGRES_DB")
    postgres_user: str = Field(default="travel_user", alias="POSTGRES_USER")
    postgres_password: str = Field(default="travel123456", alias="POSTGRES_PASSWORD")

    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")

    # ============== MCP 服务配置 ==============
    amap_api_key: str = Field(default="", alias="AMAP_API_KEY")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    mcp_weather_url: str = Field(default="", alias="MCP_WEATHER_URL")
    mcp_search_url: str = Field(default="", alias="MCP_SEARCH_URL")
    external_timeout_seconds: float = Field(default=10.0, alias="EXTERNAL_TIMEOUT_SECONDS")
    external_max_retries: int = Field(default=2, alias="EXTERNAL_MAX_RETRIES")

    # ============== RAG 检索配置 ==============
    enable_cross_encoder_rerank: bool = Field(default=False, alias="ENABLE_CROSS_ENCODER_RERANK")
    cross_encoder_model: str = Field(default="BAAI/bge-reranker-base", alias="CROSS_ENCODER_MODEL")

    def validate_security(self) -> None:
        """拒绝在非开发环境使用公开的默认 JWT 密钥。"""
        if (
            self.app_env.lower() not in {"development", "dev", "test"}
            and self.jwt_secret_key == "development-only-change-me"
        ):
            raise RuntimeError("生产环境必须配置独立的 JWT_SECRET_KEY")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def database_url(self) -> str:
        """生成 PostgreSQL 连接字符串"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """生成 Redis 连接字符串"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """获取配置单例（缓存）"""
    return Settings()


settings = get_settings()
