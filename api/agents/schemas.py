"""Shared request/response schemas for agents routes."""

from typing import Literal

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


class BatchExtractSubmitRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1)
    import_mode: str = "auto"
    extraction_mode: str = "per_sheet"
    auto_submit: bool = False
    offload: bool = True


class BatchExtractSubmitAcceptedItem(BaseModel):
    index: int
    file_id: str
    draft_id: str
    extraction_run_id: str
    submit_run_id: str
    status: Literal["queued"] = "queued"


class BatchExtractSubmitError(BaseModel):
    index: int
    file_id: str
    error: str
    code: str | None = None


class BatchExtractSubmitResult(BaseModel):
    total: int
    queued: int
    failed: int
    accepted: list[BatchExtractSubmitAcceptedItem] = Field(default_factory=list)
    errors: list[BatchExtractSubmitError] = Field(default_factory=list)
