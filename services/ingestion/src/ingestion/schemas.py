from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from ingestion.import_models import MAX_SOURCE_URL_LENGTH
from ingestion.models import ImportStatus

MAX_TEXT_BYTES = 256 * 1024


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UrlImportRequest(ApiModel):
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def _url_within_stored_length(cls, value: HttpUrl) -> HttpUrl:
        if len(str(value)) > MAX_SOURCE_URL_LENGTH:
            raise ValueError("url exceeds the maximum stored length")
        return value


class TextImportRequest(ApiModel):
    text: str

    @field_validator("text")
    @classmethod
    def _text_has_content_within_limit(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace content")
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("text must not exceed 256 KiB encoded as UTF-8")
        return value


class ImportAccepted(ApiModel):
    job_id: UUID
    status: ImportStatus


class ImportJobView(ApiModel):
    id: UUID
    status: ImportStatus
    attempt_count: Annotated[int, Field(ge=0)]
    created_recipe_id: UUID | None
    error_category: str | None
    cancellation_requested: bool = False
