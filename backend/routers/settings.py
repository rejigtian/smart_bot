"""Settings — LLM API keys and default model config, persisted to JSON."""
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"
SETTINGS_PATH.parent.mkdir(exist_ok=True)


class Settings(BaseModel):
    openai_api_key: Optional[str] = ""
    anthropic_api_key: Optional[str] = ""
    anthropic_base_url: Optional[str] = ""   # e.g. https://litellm.wepieoa.com for proxy
    gemini_api_key: Optional[str] = ""
    zhipu_api_key: Optional[str] = ""
    groq_api_key: Optional[str] = ""
    ollama_base_url: Optional[str] = "http://localhost:11434"
    default_provider: Optional[str] = "openai"
    default_model: Optional[str] = "gpt-4o"
    # Verifier can use a separate (stronger) model for pass/fail judgment.
    # Leave empty to use the same model as the agent.
    verifier_provider: Optional[str] = ""
    verifier_model: Optional[str] = ""
    # Webhook notification after run completes
    webhook_url: Optional[str] = ""
    webhook_type: Optional[str] = ""  # feishu / dingtalk / slack / custom


def _load() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict):
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))


@router.get("", response_model=Settings)
async def get_settings():
    data = _load()
    return Settings(**{k: data.get(k, v) for k, v in Settings().model_dump().items()})


@router.put("", response_model=Settings)
async def update_settings(new_settings: Settings):
    current = _load()
    current.update(new_settings.model_dump(exclude_none=True))
    _save(current)
    return Settings(**current)
