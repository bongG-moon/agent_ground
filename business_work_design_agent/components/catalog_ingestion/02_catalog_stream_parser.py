from __future__ import annotations

import codecs
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterator, TextIO

import gridfs
from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.errors import BulkWriteError, PyMongoError

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")
_WRAPPER_PREFIX = re.compile(r'^\s*\{\s*"items"\s*:', re.DOTALL)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter()).strip()
    return str(value or "").strip()


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _validate_job_ref(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    result = {key: payload.get(key) for key in _JOB_REF_KEYS}
    for key in ("tenant_id", "job_id", "snapshot_id", "stage", "trace_id"):
        if not isinstance(result[key], str) or not result[key].strip():
            raise ValueError(f"job_ref.{key} is required.")
        result[key] = result[key].strip()
    try:
        result["expected_cursor"] = max(0, int(result["expected_cursor"] or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("job_ref.expected_cursor must be an integer.") from exc
    return result


def _job_ref(source: dict[str, Any], stage: str, cursor: int) -> dict[str, Any]:
    return {
        "tenant_id": source["tenant_id"],
        "job_id": source["job_id"],
        "snapshot_id": source["snapshot_id"],
        "stage": stage,
        "expected_cursor": max(0, int(cursor)),
        "trace_id": source["trace_id"],
    }


def _failure(code: str, message: str, trace_id: str, retryable: bool = False) -> Data:
    return Data(
        data={
            "ok": False,
            "run_id": trace_id,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": code, "message": message, "retryable": retryable, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    )


def _iter_json_sequence(
    handle: TextIO,
    *,
    wrapper: bool,
    read_chars: int,
    max_record_chars: int,
) -> Iterator[tuple[int, Any]]:
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False

    def read_more() -> bool:
        nonlocal buffer, position, eof
        if eof:
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        chunk = handle.read(read_chars)
        if chunk == "":
            eof = True
            return False
        buffer += chunk
        return True

    def skip_whitespace() -> None:
        nonlocal position
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer) or not read_more():
                return

    def expect(character: str) -> None:
        nonlocal position
        skip_whitespace()
        if position >= len(buffer) or buffer[position] != character:
            raise ValueError(f"Expected '{character}' in the catalog JSON structure.")
        position += 1

    def decode_value() -> Any:
        nonlocal position
        skip_whitespace()
        start = position
        while True:
            try:
                value, end = decoder.raw_decode(buffer, position)
                if end - start > max_record_chars:
                    raise ValueError("A catalog record exceeds the configured character limit.")
                position = end
                return value
            except json.JSONDecodeError as exc:
                if len(buffer) - start > max_record_chars:
                    raise ValueError("A catalog record exceeds the configured character limit.") from exc
                if not read_more():
                    raise ValueError("The catalog JSON ended inside a record.") from exc
                start = 0

    if wrapper:
        expect("{")
        key = decode_value()
        if key != "items":
            raise ValueError('The supported wrapper must have "items" as its first and only field.')
        expect(":")
    expect("[")

    record_index = 0
    skip_whitespace()
    if position < len(buffer) and buffer[position] == "]":
        position += 1
    else:
        while True:
            value = decode_value()
            yield record_index, value
            record_index += 1
            skip_whitespace()
            if position >= len(buffer) and not read_more():
                raise ValueError("The catalog JSON array is not closed.")
            skip_whitespace()
            if position < len(buffer) and buffer[position] == ",":
                position += 1
                continue
            if position < len(buffer) and buffer[position] == "]":
                position += 1
                break
            raise ValueError("Catalog array entries must be separated by a comma.")

    if wrapper:
        expect("}")
    skip_whitespace()
    if position < len(buffer) or read_more():
        skip_whitespace()
        if position < len(buffer):
            raise ValueError("Unexpected content follows the catalog JSON payload.")


def _iter_jsonl(handle: TextIO, max_record_chars: int) -> Iterator[tuple[int, Any]]:
    record_index = 0
    for line_number, line in enumerate(handle, start=1):
        if len(line) > max_record_chars:
            yield record_index, {"__parse_error__": "RECORD_TOO_LARGE", "line_number": line_number, "sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()}
            record_index += 1
            continue
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = {
                "__parse_error__": "MALFORMED_JSONL_RECORD",
                "line_number": line_number,
                "sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
            }
        yield record_index, value
        record_index += 1


def _detect_format(prefix: str, format_hint: str) -> str:
    hint = str(format_hint or "").strip().lower()
    if hint == "jsonl":
        return "jsonl"
    stripped = prefix.lstrip("\ufeff \t\r\n")
    if stripped.startswith("["):
        return "array"
    if _WRAPPER_PREFIX.match(stripped):
        return "items_wrapper"
    raise ValueError('A .json catalog must be a JSON array or an {"items": [...]} wrapper.')


class CatalogStreamParserComponent(Component):
    display_name = "Catalog Stream Parser"
    description = "Parse JSON arrays, items wrappers, or JSONL into bounded MongoDB staging records with a durable cursor."
    icon = "Braces"
    name = "CatalogStreamParser"

    inputs = [
        DataInput(name="job_ref", display_name="Scanned Job Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        IntInput(name="max_records_per_run", display_name="Maximum Records Per Run", value=2000, advanced=True),
        IntInput(name="max_record_chars", display_name="Maximum Characters Per Record", value=1000000, advanced=True),
        IntInput(name="read_chunk_kb", display_name="Read Chunk Size (KiB)", value=256, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="parsed_job_ref", display_name="Parsed Job Reference", method="parse_catalog", types=["Data"])]

    def parse_catalog(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            incoming = _validate_job_ref(getattr(self, "job_ref", None))
            trace_id = incoming["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)

            max_records = _bounded_int(getattr(self, "max_records_per_run", 2000), 2000, 1, 5000)
            max_record_chars = _bounded_int(getattr(self, "max_record_chars", 1000000), 1000000, 1024, 4000000)
            read_chars = _bounded_int(getattr(self, "read_chunk_kb", 256), 256, 16, 1024) * 1024
            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 10000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                staging = database["catalog_ingest_chunks"]
                query = {
                    "_id": incoming["job_id"],
                    "tenant_id": incoming["tenant_id"],
                    "snapshot_id": incoming["snapshot_id"],
                }
                job = jobs.find_one(query)
                if not job:
                    return _failure("CATALOG_JOB_NOT_FOUND", "The catalog ingest job was not found for this tenant.", trace_id)
                current_stage = str(job.get("stage") or "")
                if current_stage == "QUARANTINED_SECRET":
                    return _failure("CATALOG_SECRET_QUARANTINED", "The catalog source is quarantined and cannot be parsed.", trace_id)
                if current_stage == "PARSE_COMPLETED":
                    cursor = int((job.get("stage_cursors") or {}).get("parse") or job.get("expected_cursor") or 0)
                    return Data(data=_job_ref(incoming, current_stage, cursor))
                if current_stage not in {"SECRET_SCAN_PASSED", "PARSE_PARTIAL"}:
                    return _failure("CATALOG_STAGE_CONFLICT", "Parsing requires a secret-scan-passed job.", trace_id)

                durable_cursor = int((job.get("stage_cursors") or {}).get("parse") or 0)
                valid_transition = (
                    current_stage == "SECRET_SCAN_PASSED"
                    and incoming["stage"] == "SECRET_SCAN_PASSED"
                    and incoming["expected_cursor"] == 0
                ) or (
                    current_stage == "PARSE_PARTIAL"
                    and incoming["stage"] == "PARSE_PARTIAL"
                    and incoming["expected_cursor"] == durable_cursor
                )
                if not valid_transition:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The parse cursor is stale. Reload the current job reference.", trace_id)

                blob_id = job.get("source_blob_id")
                if blob_id is None:
                    return _failure("CATALOG_SOURCE_MISSING", "The restricted catalog source is unavailable.", trace_id)
                source = gridfs.GridFS(database, collection="catalog_source_files_blob").get(blob_id)
                prefix_bytes = source.read(4096)
                source.seek(0)
                prefix = prefix_bytes.decode("utf-8-sig", errors="strict")
                source_format = _detect_format(prefix, str(job.get("source_format_hint") or ""))
                text_handle = codecs.getreader("utf-8-sig")(source, errors="strict")
                if source_format == "jsonl":
                    iterator = _iter_jsonl(text_handle, max_record_chars)
                else:
                    iterator = _iter_json_sequence(
                        text_handle,
                        wrapper=source_format == "items_wrapper",
                        read_chars=read_chars,
                        max_record_chars=max_record_chars,
                    )

                operations: list[ReplaceOne] = []
                accepted = 0
                quarantined = 0
                next_cursor = durable_cursor
                has_more = False
                now = _utc_now()
                for record_index, record in iterator:
                    if record_index < durable_cursor:
                        continue
                    if accepted + quarantined >= max_records:
                        has_more = True
                        break
                    is_parse_error = isinstance(record, dict) and "__parse_error__" in record
                    is_object = isinstance(record, dict) and not is_parse_error
                    status = "PARSED" if is_object else "QUARANTINED_PARSE"
                    if is_object:
                        accepted += 1
                        raw_record: dict[str, Any] | None = record
                        error_code = None
                    else:
                        quarantined += 1
                        raw_record = None
                        error_code = str(record.get("__parse_error__")) if is_parse_error else "RECORD_NOT_OBJECT"
                    document = {
                        "_id": f"{incoming['job_id']}:{record_index}",
                        "tenant_id": incoming["tenant_id"],
                        "job_id": incoming["job_id"],
                        "snapshot_id": incoming["snapshot_id"],
                        "record_index": record_index,
                        "parse_status": status,
                        "parse_error_code": error_code,
                        "raw_record": raw_record,
                        "source": {
                            "file_id": str(job.get("source_file_id") or incoming["job_id"]),
                            "record_index": record_index,
                            "file_sha256": str(job.get("source_sha256") or ""),
                        },
                        "created_at": now,
                        "updated_at": now,
                    }
                    operations.append(ReplaceOne({"_id": document["_id"], "tenant_id": incoming["tenant_id"]}, document, upsert=True))
                    next_cursor = record_index + 1

                if operations:
                    staging.create_index(
                        [("tenant_id", ASCENDING), ("job_id", ASCENDING), ("record_index", ASCENDING)],
                        unique=True,
                        name="uq_catalog_staging_record",
                    )
                    try:
                        staging.bulk_write(operations, ordered=False)
                    except BulkWriteError as exc:
                        write_errors = (exc.details or {}).get("writeErrors") or []
                        if write_errors:
                            raise

                next_stage = "PARSE_PARTIAL" if has_more else "PARSE_COMPLETED"
                update = jobs.update_one(
                    {**query, "stage": current_stage, "stage_cursors.parse": durable_cursor},
                    {
                        "$set": {
                            "stage": next_stage,
                            "expected_cursor": next_cursor,
                            "stage_cursors.parse": next_cursor,
                            "parse_completed": not has_more,
                            "source_format": source_format,
                            "updated_at": now,
                        },
                        "$inc": {"counts.parsed": accepted, "counts.parse_quarantined": quarantined},
                    },
                )
                if update.modified_count != 1:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The parse cursor changed while this batch was running.", trace_id, True)
                self.status = (
                    f"Catalog parse batch stored: job_id={incoming['job_id']}, accepted={accepted}, "
                    f"quarantined={quarantined}, cursor={next_cursor}, complete={not has_more}"
                )
                return Data(data=_job_ref(incoming, next_stage, next_cursor))
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Catalog parsing stopped on an invalid source contract."
            return _failure("CATALOG_PARSE_INVALID", str(exc), trace_id)
        except (UnicodeError, PyMongoError, gridfs.errors.GridFSError, OSError):
            self.status = "Catalog parsing failed before the durable cursor advanced."
            return _failure(
                "CATALOG_PARSE_FAILED",
                "The catalog parse batch could not be completed. Resume from the last durable cursor.",
                trace_id,
                retryable=True,
            )
