"""Shared request/response schemas for agents routes."""

from pydantic import BaseModel, Field


class BulkDeletePayload(BaseModel):
    ids: list[str] = Field(default_factory=list)


class BulkDeleteResult(BaseModel):
    deleted_ids: list[str] = Field(default_factory=list)
    failed_ids: list[str] = Field(default_factory=list)
