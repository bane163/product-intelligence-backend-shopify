"""Shared request/response schemas for agents routes."""

from pydantic import BaseModel, Field


class BulkDeletePayload(BaseModel):
    ids: list[str] = Field(default_factory=list)


class BulkDeleteResult(BaseModel):
    deleted_ids: list[str] = Field(default_factory=list)
    failed_ids: list[str] = Field(default_factory=list)


class BulkUploadItem(BaseModel):
    index: int
    file_id: str
    filename: str
    size: int


class BulkUploadError(BaseModel):
    index: int
    filename: str
    error: str
    code: str | None = None


class BulkUploadResult(BaseModel):
    total: int
    succeeded: int
    failed: int
    uploaded: list[BulkUploadItem] = Field(default_factory=list)
    errors: list[BulkUploadError] = Field(default_factory=list)
