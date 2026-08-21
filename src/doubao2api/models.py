from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: Any
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    account_id: str | None = None
    model: str = "doubao"
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    conversation_id: str | None = None
    bot_id: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    enable_thinking: bool | None = None
    reasoning_effort: str | None = None


class ImageGenerationRequest(BaseModel):
    account_id: str | None = None
    prompt: str
    model: str = "doubao-image"
    n: int = 1
    size: str | None = "1024x1024"
    ratio: str | None = None
    ref_image_key: str | None = None
    ref_image_keys: list[str] | None = None
    ref_image_url: str | None = None
    ref_image_urls: list[str] | None = None
    response_format: str | None = "url"
    wait_timeout: float | None = 15


class VideoGenerationRequest(BaseModel):
    account_id: str | None = None
    prompt: str
    model: str = "doubao-video"
    size: str | None = None
    ratio: str | None = None
    duration: Any | None = None
    ref_image_key: str | None = None
    ref_image_keys: list[str] | None = None
    ref_image_url: str | None = None
    ref_image_urls: list[str] | None = None
    wait_timeout: float | None = 15
    return_no_watermark_video: bool | None = None


class AccountProvisionRequest(BaseModel):
    account_id: str | None = None
    ai_platform: str | None = None
    start_browser: bool = True
    background: bool = False


class AccountRenameRequest(BaseModel):
    new_account_id: str


class AccountCategoryRequest(BaseModel):
    category: str | None = None


class AccountTabHiddenRequest(BaseModel):
    hidden: bool


class AccountBatchCategoryRequest(BaseModel):
    account_ids: list[str]
    category: str | None = None


class AccountCacheClearRequest(BaseModel):
    account_ids: list[str] | None = None


class AccountBackgroundActionRequest(BaseModel):
    background: bool = False


class AdminSettingsUpdateRequest(BaseModel):
    return_no_watermark_video: bool | None = None
    image_upload_force_upload: bool | None = None
    auto_start_all_accounts: bool | None = None
    auto_start_account_categories: list[str] | None = None
    auto_replenish_accounts: bool | None = None
    auto_replenish_account_categories: list[str] | None = None
    video_daily_credits: int | None = Field(default=None, ge=0)
    video_15s_credit_cost: int | None = Field(default=None, ge=0)
    video_10s_credit_cost: int | None = Field(default=None, ge=0)
    video_5s_credit_cost: int | None = Field(default=None, ge=0)


class ManualCookieImportRequest(BaseModel):
    account_id: str | None = None
    cookie_text: str
    reset_environment: bool = True


class ResearchJobCreateRequest(BaseModel):
    name: str = ""
    keywords: list[str]
    account_ids: list[str] = Field(default_factory=list)
    prompt_template: str = "{keyword}"
    scheduled_at: str | None = None
    interval_seconds: int = Field(default=10, ge=1, le=86400)
    account_cooldown_seconds: int = Field(default=0, ge=0, le=86400)
    max_attempts: int = Field(default=2, ge=1, le=3)
    ai_platform: str = "doubao"


class ResearchJobTemplateCreateRequest(BaseModel):
    name: str = ""
    keywords: list[str]
    prompt_template: str = "{keyword}"
    interval_seconds: int = Field(default=10, ge=1, le=86400)
    account_cooldown_seconds: int = Field(default=0, ge=0, le=86400)
    max_attempts: int = Field(default=2, ge=1, le=3)


class ResearchJobTemplateUpdateRequest(BaseModel):
    name: str = ""
    keywords: list[str]
    prompt_template: str = "{keyword}"
    interval_seconds: int = Field(default=10, ge=1, le=86400)
    account_cooldown_seconds: int = Field(default=0, ge=0, le=86400)
    max_attempts: int = Field(default=2, ge=1, le=3)


class ResearchScheduleCreateRequest(BaseModel):
    name: str = ""
    template_id: str
    schedule_type: str = "interval"
    schedule_value: str


class ResearchScheduleUpdateRequest(BaseModel):
    name: str = ""
    template_id: str
    schedule_type: str = "interval"
    schedule_value: str


class ResearchScheduleToggleRequest(BaseModel):
    enabled: bool


class ResearchJobRenameRequest(BaseModel):
    name: str


class ResearchResultsSyncRequest(BaseModel):
    batch_size: int | None = Field(default=10000, ge=1, le=100000)


class ResearchResultsSourceComparisonRequest(BaseModel):
    job_ids_a: list[str]
    job_ids_b: list[str]
    keywords: list[str] = Field(default_factory=list)
    platform: str = ""
    account_id: str = ""


class ResearchResultsLongTailRequest(BaseModel):
    job_id: str = ""
    keywords: list[str] = Field(default_factory=list)
    platform: str = ""
    account_id: str = ""
    date_from: str = ""
    date_to: str = ""
    split_mode: str = "threshold"
    breadth_threshold: int = 3
    freq_threshold: int = 20
    density_threshold: float = 5.0
    noise_density_threshold: float = 20.0
