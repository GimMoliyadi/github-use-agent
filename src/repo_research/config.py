from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_mode: Literal["mock", "live"] = "mock"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout: float = Field(default=45, ge=1, le=180)
    llm_max_retries: int = Field(default=1, ge=0, le=2)
    llm_output_mode: Literal["auto", "tools", "json_schema", "json_text"] = "auto"
    llm_input_price_per_million: float = Field(default=0, ge=0)
    llm_output_price_per_million: float = Field(default=0, ge=0)
    github_token: SecretStr = SecretStr("")
    github_timeout: float = Field(default=20, ge=1, le=90)
    data_dir: Path = Path("data")
    app_access_token: SecretStr = SecretStr("")
    load_saved_model: bool = True

    @model_validator(mode="after")
    def live_configuration(self):
        if self.model_mode == "live" and not (
            self.llm_base_url and self.llm_api_key.get_secret_value() and self.llm_model
        ):
            raise ValueError("Live mode requires LLM_BASE_URL, LLM_API_KEY and LLM_MODEL")
        return self

