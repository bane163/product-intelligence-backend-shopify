"""Approved public failure copy. Diagnostic exceptions stay in logs and stored evidence."""

from __future__ import annotations
import re
from typing import Any

GENERIC = "Something went wrong. Please try again."
ERROR_KEYS = {
    "error",
    "failure_message",
    "error_message",
    "extraction_error",
    "submit_error",
    "last_error",
    "issue_message",
    "failure_reason",
}
VALIDATION_KEYS = {
    "field_errors",
    "validation_errors",
    "userErrors",
    "user_errors",
    "errors",
}


def error_code(value: Any, status: int | None = None) -> str:
    row = value if isinstance(value, dict) else {}
    if (
        row.get("partial_count")
        or (row.get("success_count") and row.get("failed_count"))
        or "partial" in str(row.get("status", ""))
    ):
        return "PARTIALLY_APPLIED"
    explicit = row.get("code") or row.get("failure_code") or row.get("error_code")
    if isinstance(explicit, str) and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{1,99}", explicit
    ):
        return explicit
    text = str(row.get("message", value)).lower()
    if status == 401 or any(
        x in text
        for x in (
            "shopify_reauth_required",
            "invalid session",
            "session expired",
            "401 unauthorized",
            "reopen stockpile from shopify admin",
            "missing shop_domain",
            "missing authenticated shop_domain",
            "shop_domain is required",
            "shop_domain header is required",
            "shop_domain must be provided",
        )
    ):
        return "SHOPIFY_REAUTH_REQUIRED"
    if status == 403 or any(
        x in text
        for x in (
            "access denied",
            "permission",
            "access scope",
            "forbidden",
            "not authorized",
        )
    ):
        return "ACCESS_DENIED"
    if status == 429 or any(
        x in text for x in ("timeout", "timed out", "connection", "network")
    ):
        return "TEMPORARY_FAILURE"
    return f"UPSTREAM_{status}" if status else "UNKNOWN_ERROR"


def public_message(value: Any, status: int | None = None) -> str:
    row = value if isinstance(value, dict) else {}
    code = (
        error_code(value, status) + " " + error_code(row.get("message", value), status)
    ).upper()
    if re.search(r"REAUTH|SESSION|UNAUTHENTICATED|UPSTREAM_401|MISSING_TENANT", code):
        return (
            "Your Shopify session has expired. Reopen the app from Shopify to continue."
        )
    if re.search(r"ACCESS|PERMISSION|FORBIDDEN|UPSTREAM_403|OWNERSHIP", code):
        return "We couldn’t access the information needed for this action. Contact support for help."
    if "PARTIAL" in code:
        return "Some changes were applied. Review the completed and failed changes before trying again."
    if re.search(r"CREDIT_LIMIT|INSUFFICIENT_CREDIT", code):
        return "You’ve reached your credit limit. Review your plan or credit limit in Billing to continue."
    if re.search(r"REVIEW_EDIT_VALIDATION|INVALID_REVIEW|VALIDATION_FAILED", code):
        return "Correct the highlighted fields before applying these changes."
    if re.search(r"REVIEW_REQUIRED|STALE|CONFLICT", code):
        return "Shopify data has changed. Refresh the review before applying these changes."
    if re.search(
        r"TIMEOUT|TEMPORARY|CONNECTION|UNAVAILABLE|UPSTREAM_429|UPSTREAM_5", code
    ):
        return "The service is temporarily unavailable. Please try again shortly."
    return GENERIC


def field_message(row: dict) -> str:
    field = str(row.get("value_path") or row.get("field") or "").lower()
    code = str(row.get("code") or "")
    if field == "title" or code == "TITLE_REQUIRED":
        return "Enter a product title."
    if field.endswith("price") or re.search(r"PRICE|DECIMAL", code):
        return "Enter a valid price using numbers and a decimal point."
    if re.search(r"URL|IMAGE|MEDIA", code):
        return "Use a publicly accessible image URL and try again."
    if re.search(r"OPTION|VARIANT", code):
        return "Check the option names and values. Each variant needs a unique combination."
    return "Check this value and enter a valid value before saving."


def safe_detail(value: Any, status: int | None = None) -> dict:
    root = value if isinstance(value, dict) else {}
    if isinstance(root.get("detail"), list):
        root = {
            "code": "VALIDATION_FAILED",
            "field_errors": [
                {
                    "code": "INVALID_REVIEW_VALUE",
                    "field": ".".join(
                        str(part) for part in item.get("loc", []) if part != "body"
                    ),
                    "loc": item.get("loc", []),
                    "message": "",
                }
                for item in root["detail"]
                if isinstance(item, dict)
            ],
        }
    nested = root.get("detail") or root.get("error")
    row = (
        nested
        if isinstance(nested, dict)
        else {**root, **({"message": nested} if isinstance(nested, str) else {})}
    )
    code = error_code(row or value, status)
    output = {"code": code, "message": public_message({**row, "code": code}, status)}
    for key in (
        "field_errors",
        "validation_errors",
        "item_id",
        "cell_id",
        "change_id",
        "value_path",
        "field",
        "request_id",
        "correlation_id",
        "retryable",
    ):
        if key in row:
            output[key] = sanitize_payload(row[key], validation=key in VALIDATION_KEYS)
    return output


def sanitize_payload(value: Any, *, validation: bool = False) -> Any:
    if isinstance(value, list):
        return [
            (
                field_message({})
                if validation and isinstance(x, str)
                else sanitize_payload(x, validation=validation)
            )
            for x in value
        ]
    if not isinstance(value, dict):
        return value
    failed = (
        bool(value.get("error"))
        or value.get("level") == "error"
        or value.get("phase") == "error"
        or value.get("ok") is False
        or bool(re.search(r"failed|error|partial", str(value.get("status", ""))))
    )
    output = {}
    for key, entry in value.items():
        if not validation and key in {
            "value",
            "current",
            "proposed",
            "original",
            "original_value",
            "proposed_value",
            "current_value",
            "execution_proposed",
            "metafields",
        }:
            output[key] = entry
            continue
        if validation and key not in {
            "code",
            "message",
            "field",
            "value_path",
            "item_id",
            "cell_id",
            "change_id",
            "product_index",
            "variant_index",
            "loc",
            "type",
            "filename",
            "file_id",
            "index",
            "error",
            "status",
            "suggestion_id",
            "run_id",
            "remedy",
        }:
            continue
        if key == "remedy" and str(entry) not in {
            "edit",
            "review",
            "retry",
            "view_source",
        }:
            continue
        if key in {
            "stack",
            "traceback",
            "exception",
            "raw_response",
            "upstream_body",
            "payload_preview",
            "response_body",
        }:
            continue
        if key in {"detail", "details"} and failed:
            output[key] = safe_detail(entry)
            continue
        if key in {"message", "reason"} and (validation or failed):
            output[key] = field_message(value) if validation else public_message(value)
        elif key in ERROR_KEYS and entry:
            output[key] = (
                safe_detail(entry)
                if isinstance(entry, dict)
                else public_message(
                    {
                        **value,
                        "code": value.get("failure_code")
                        or value.get("error_code")
                        or value.get("code"),
                        "status": value.get("status"),
                        "message": entry,
                    }
                )
            )
        else:
            output[key] = sanitize_payload(entry, validation=key in VALIDATION_KEYS)
    return output
