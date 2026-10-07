from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 공통 계약(docs/specs/CONNECTION_CONTRACT.md) 확정 포트. mcp_client_url과 같은 호스트에 떠 있다고 가정한다.
_MCP_SERVER_PORTS = {
    "price_mcp": 8020,
    "news_mcp": 8021,
    "disclosure_mcp": 8022,
    "community_mcp": 8023,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/stock_insight"
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 1800

    mcp_client_url: str = "http://localhost:8010"
    mcp_client_timeout_seconds: float = 75.0  # 공통 계약: Backend → MCP Client 75초
    mcp_client_mode: str = "mock"  # "mock" | "live"
    # 부하테스트로 live 모드(느린 외부 API)를 흉내낼 때만 쓴다. 평소엔 0.
    mcp_mock_delay_seconds: float = 0.0

    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: str = "low"

    auth_mode: str = "demo"
    demo_user_id: str = "demo-user-a"
    jwt_secret_key: str = "dev-secret-change-me-please-32-bytes-min"
    jwt_expires_minutes: int = 1440  # 24시간

    cors_allowed_origins: str = "http://localhost:5173"

    admin_username: str = "admin"
    admin_password: str = "change-me"

    # 실황 페이지의 MCP Inspector 바로가기 링크에만 쓴다. mcp_client_url의 호스트는
    # "백엔드가 내부적으로 mcp_client에 접속하는 주소"라서(같은 서버면 localhost) 브라우저용
    # 링크에는 못 쓴다 — 브라우저가 실제로 접속할 수 있는 주소를 여기 명시로 넣는다.
    mcp_public_host: str = "localhost"

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def _jwt_secret_not_empty(cls, value: str) -> str:
        # .env에 JWT_SECRET_KEY= 처럼 비워 두면 HMAC 키 오류로 로그인이 500이 되므로 개발 기본값으로 대체한다.
        return value if isinstance(value, str) and value.strip() else "dev-secret-change-me-please-32-bytes-min"

    @property
    def cors_allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def mcp_server_urls(self) -> dict[str, str]:
        """MCP_PUBLIC_HOST에서 확정 포트로 뜨는 4개 MCP 서버 주소(브라우저 디버그용)."""
        return {name: f"http://{self.mcp_public_host}:{port}/mcp" for name, port in _MCP_SERVER_PORTS.items()}


settings = Settings()
