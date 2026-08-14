"""ESG CSV/API ingestion service (B3).

Capabilities:
- CSV parse with header detection
- Named column-to-KPI mappings stored per org
- Reconciliation: preview rows, flag anomalies, detect duplicates
- Commit valid rows to esg_kpi_entries with source lineage
- Simple API-key ingest for ops system pushes
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
from datetime import datetime, timezone

from sheplatform.database import resolve_org
from sheplatform.modules.esg_kpi.data_service import _rag, get_kpi, record_kpi_entry


COMMON_DELIMITERS = [",", ";", "\t", "|"]


def _detect_dialect(sample: str) -> tuple[str, list[str]]:
    """Return (delimiter, headers) for the CSV sample."""
    for delim in COMMON_DELIMITERS:
        try:
            sniffed = csv.Sniffer().sniff(sample, delimiters=delim)
            reader = csv.DictReader(io.StringIO(sample), dialect=sniffed)
            headers = reader.fieldnames or []
            if headers and len(headers) >= 2:
                return sniffed.delimiter, headers
        except Exception:
            continue
    # fallback: split first line by comma
    first = sample.splitlines()[0] if sample else ""
    return ",", [h.strip() for h in first.split(",")]


def _coerce_number(value: str) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if cleaned == "":
        return None
    # Strip percentage
    is_pct = cleaned.endswith("%")
    if is_pct:
        cleaned = cleaned[:-1]
    try:
        num = float(cleaned)
        return num / 100.0 if is_pct else num
    except ValueError:
        return None


def _normalize_period(raw: str, frequency: str) -> str | None:
    """Normalize YYYY-MM, YYYY-QN, YYYY inputs."""
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 4 and raw.isdigit():
        return raw  # annual
    if len(raw) == 7 and raw[4] == "-":
        return raw  # YYYY-MM
    if len(raw) == 6 and raw[4] == "-" and raw[5:].upper().startswith("Q"):
        return f"{raw[:4]}-{raw[5:].upper()}"
    # try common date formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m")
        except ValueError:
            continue
    return raw


def _derive_period_from_filename(file_name: str) -> str | None:
    """Try to pull YYYY-MM or YYYY-QN from a filename like env_2026-08.csv."""
    import re
    m = re.search(r"(20\d{2})[-_ ]?(Q[1-4]|0[1-9]|1[0-2])", file_name, re.I)
    if m:
        year, part = m.group(1), m.group(2).upper()
        if part.startswith("Q"):
            return f"{year}-{part}"
        return f"{year}-{part}"
    m = re.search(r"(20\d{2})", file_name)
    if m:
        return m.group(1)
    return None


def _find_column(headers: list[str], candidates: list[str]) -> str | None:
    lowered = {h.strip().lower(): h for h in headers}
    for c in candidates:
        if c.lower() in lowered:
            return lowered[c.lower()]
    return None


def create_mapping(db, *, mapping_name: str, mappings: list[dict], org_id: int,
                   created_by: int | None = None) -> dict:
    """Store a column-to-KPI mapping.

    mappings: [{"source_column": "CO2 (t)", "kpi_id": 1, "transform": "value"}, ...]
    """
    created = []
    for m in mappings:
        db.execute(
            "INSERT INTO esg_csv_mappings (mapping_name, kpi_id, source_column, transform, "
            "org_id, created_by) VALUES (%s,%s,%s,%s,%s,%s)",
            (mapping_name, m["kpi_id"], m["source_column"], m.get("transform", "value"),
             org_id, created_by))
        created.append(m)
    db.commit()
    return {"ok": True, "mapping_name": mapping_name, "mappings": created}


def list_mappings(db, mapping_name: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM esg_csv_mappings WHERE is_active = 1"
    params: list = []
    if mapping_name:
        sql += " AND mapping_name = %s"
        params.append(mapping_name)
    if org_id:
        sql += " AND org_id = %s"
        params.append(org_id)
    sql += " ORDER BY mapping_name, source_column"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def delete_mapping(db, mapping_name: str, org_id: int) -> dict:
    db.execute("DELETE FROM esg_csv_mappings WHERE mapping_name = %s AND org_id = %s",
               (mapping_name, org_id))
    db.commit()
    return {"ok": True, "deleted": db.execute("SELECT changes()").fetchone()[0]
            if hasattr(db, "_conn") else 0}


def parse_csv_upload(db, file_bytes: bytes, file_name: str, mapping_name: str | None = None,
                     *, org_id: int, created_by: int | None = None,
                     default_period: str | None = None) -> dict:
    """Parse a CSV, apply the named mapping, and return a preview with anomaly flags."""
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")

    delim, headers = _detect_dialect(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = list(reader)

    mappings = list_mappings(db, mapping_name=mapping_name, org_id=org_id) if mapping_name else []
    mappings_by_col = {m["source_column"].strip().lower(): m for m in mappings}

    period_col = _find_column(headers, ["period", "month", "date", "year_month", "reporting_period"])

    db.execute(
        "INSERT INTO esg_csv_uploads (file_name, mapping_name, rows_total, org_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s)",
        (file_name, mapping_name or "", len(rows), org_id, created_by))
    db.commit()
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    preview = []
    for idx, row in enumerate(rows, start=1):
        raw_period = row.get(period_col, "").strip() if period_col else ""
        period = _normalize_period(raw_period or default_period or _derive_period_from_filename(file_name) or "", "")

        row_records = []
        any_valid = False
        for header, raw_value in row.items():
            if not header or header.strip().lower() == period_col:
                continue
            key = header.strip().lower()
            mapping = mappings_by_col.get(key)
            if not mapping:
                continue
            kpi = get_kpi(db, mapping["kpi_id"])
            if not kpi:
                continue

            actual = _coerce_number(raw_value)
            status = "pending"
            anomaly_reason: str | None = None

            if actual is None:
                status = "anomalous"
                anomaly_reason = "non-numeric value"
            elif actual < 0 and kpi["unit"] not in ("pct",):
                status = "anomalous"
                anomaly_reason = "negative value"
            else:
                status = "valid"
                any_valid = True

            row_records.append({
                "upload_id": upload_id,
                "row_number": idx,
                "period": period,
                "source_column": header,
                "raw_value": raw_value,
                "mapped_kpi_id": mapping["kpi_id"],
                "actual_value": actual,
                "status": status,
                "anomaly_reason": anomaly_reason,
            })
            preview.append({
                "upload_id": upload_id,
                "row_number": idx,
                "period": period,
                "source_column": header,
                "raw_value": raw_value,
                "mapped_kpi_id": mapping["kpi_id"],
                "kpi_code": kpi["kpi_code"],
                "kpi_name": kpi["name"],
                "actual_value": actual,
                "status": status,
                "anomaly_reason": anomaly_reason,
            })

        if not any_valid and not row_records:
            # no mapped columns found - still record a sentinel
            preview.append({
                "upload_id": upload_id,
                "row_number": idx,
                "period": period,
                "source_column": "",
                "raw_value": "",
                "kpi_code": "",
                "kpi_name": "",
                "actual_value": None,
                "status": "anomalous",
                "anomaly_reason": "no mapped columns",
            })

    # persist preview rows
    for rec in preview:
        db.execute(
            "INSERT INTO esg_csv_rows (upload_id, row_number, period, source_column, raw_value, "
            "mapped_kpi_id, actual_value, status, anomaly_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (rec["upload_id"], rec["row_number"], rec["period"], rec["source_column"],
             rec["raw_value"], rec["mapped_kpi_id"], rec["actual_value"], rec["status"], rec["anomaly_reason"]))
    db.commit()

    _update_upload_counts(db, upload_id)
    return {"ok": True, "upload_id": upload_id, "rows_total": len(rows),
            "preview": preview[:200], "headers": headers, "delimiter": delim}


def _update_upload_counts(db, upload_id: int) -> None:
    counts = db.execute(
        "SELECT status, COUNT(*) AS c FROM esg_csv_rows WHERE upload_id = %s GROUP BY status",
        (upload_id,)).fetchall()
    total = sum(r["c"] for r in counts)
    valid = sum(r["c"] for r in counts if r["status"] == "valid")
    anomalous = sum(r["c"] for r in counts if r["status"] == "anomalous")
    duplicate = sum(r["c"] for r in counts if r["status"] == "duplicate")
    db.execute(
        "UPDATE esg_csv_uploads SET rows_total = %s, rows_valid = %s, rows_anomalous = %s, "
        "rows_duplicate = %s WHERE id = %s",
        (total, valid, anomalous, duplicate, upload_id))
    db.commit()


def detect_duplicates(db, upload_id: int) -> dict:
    """Flag rows that duplicate an existing entry (same KPI + period) in this upload."""
    rows = db.execute(
        "SELECT id, mapped_kpi_id, period, status FROM esg_csv_rows "
        "WHERE upload_id = %s AND status IN ('valid','pending')",
        (upload_id,)).fetchall()
    duplicates = 0
    for row in rows:
        existing = db.execute(
            "SELECT id FROM esg_kpi_entries WHERE kpi_id = %s AND period = %s LIMIT 1",
            (row["mapped_kpi_id"], row["period"])).fetchone()
        existing_csv = db.execute(
            "SELECT id FROM esg_csv_rows WHERE mapped_kpi_id = %s AND period = %s "
            "AND upload_id = %s AND id < %s LIMIT 1",
            (row["mapped_kpi_id"], row["period"], upload_id, row["id"])).fetchone()
        if existing or existing_csv:
            db.execute(
                "UPDATE esg_csv_rows SET status = 'duplicate', anomaly_reason = 'duplicate period/kpi' "
                "WHERE id = %s", (row["id"],))
            duplicates += 1
    db.commit()
    _update_upload_counts(db, upload_id)
    return {"ok": True, "duplicates_flagged": duplicates}


def get_upload(db, upload_id: int) -> dict | None:
    row = db.execute("SELECT * FROM esg_csv_uploads WHERE id = %s", (upload_id,)).fetchone()
    return dict(row) if row else None


def get_upload_rows(db, upload_id: int, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM esg_csv_rows WHERE upload_id = %s"
    params: list = [upload_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY row_number, source_column"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def commit_upload(db, upload_id: int, *, created_by: int | None = None,
                  skip_anomalies: bool = True) -> dict:
    """Commit valid rows to esg_kpi_entries. Returns summary."""
    upload = get_upload(db, upload_id)
    if not upload:
        return {"ok": False, "message": "upload not found"}
    if upload["status"] == "committed":
        return {"ok": False, "message": "already committed"}

    status_filter = "status = 'valid'" if skip_anomalies else "status IN ('valid','anomalous','duplicate')"
    rows = db.execute(
        f"SELECT * FROM esg_csv_rows WHERE upload_id = %s AND {status_filter}",
        (upload_id,)).fetchall()

    committed = 0
    skipped = 0
    linked_incidents = []
    for row in rows:
        kpi = get_kpi(db, row["mapped_kpi_id"])
        if not kpi or row["actual_value"] is None:
            skipped += 1
            continue
        target = kpi.get("target_fy26")
        result = record_kpi_entry(
            db, kpi_id=kpi["id"], period=row["period"] or upload["file_name"],
            actual_value=row["actual_value"], target_value=float(target) if target is not None else None,
            notes=f"Imported from {upload['file_name']} row {row['row_number']} via {row['source_column']}",
            created_by=created_by, org_id=upload["org_id"])
        if result["ok"]:
            entry = result["entry"]
            db.execute(
                "UPDATE esg_kpi_entries SET source_upload_id = %s, source_row_id = %s WHERE id = %s",
                (upload_id, row["id"], entry["id"]))
            db.execute(
                "UPDATE esg_csv_rows SET status = 'committed', committed_entry_id = %s WHERE id = %s",
                (entry["id"], row["id"]))
            committed += 1
            if entry.get("linked_incident_id"):
                linked_incidents.append(entry["linked_incident_id"])
        else:
            skipped += 1

    db.execute(
        "UPDATE esg_csv_uploads SET status = 'committed', committed_at = %s, committed_by = %s "
        "WHERE id = %s",
        (datetime.now(timezone.utc).isoformat(), created_by, upload_id))
    db.commit()
    return {"ok": True, "committed": committed, "skipped": skipped,
            "linked_incident_ids": linked_incidents}


# ---------------------------------------------------------------------------
# API-key ingest for ops system pushes
# ---------------------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(db, *, name: str, org_id: int, created_by: int | None = None,
                   scopes: list[str] | None = None) -> dict:
    raw = "esk_" + secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO esg_api_keys (name, key_hash, scopes, org_id, created_by) VALUES (%s,%s,%s,%s,%s)",
        (name, _hash_key(raw), json.dumps(scopes or ["esg.ingest"]), org_id, created_by))
    db.commit()
    row = db.execute("SELECT * FROM esg_api_keys WHERE key_hash = %s", (_hash_key(raw),)).fetchone()
    return {"ok": True, "api_key": raw, "record": dict(row)}


def verify_api_key(db, key: str) -> dict | None:
    row = db.execute("SELECT * FROM esg_api_keys WHERE key_hash = %s AND is_active = 1",
                       (_hash_key(key),)).fetchone()
    return dict(row) if row else None


def record_api_kpi_payload(db, *, key_record: dict, payload: dict, created_by: int | None = None) -> dict:
    """Ingest a single JSON payload from an external ops system.

    Expected payload: {"period": "2026-08", "entries": [{"kpi_code": "ESG-ENV-01", "actual_value": 12.3}, ...]}
    """
    org_id = key_record["org_id"]
    period = _normalize_period(payload.get("period", ""), "")
    entries = payload.get("entries", [])
    if not period:
        return {"ok": False, "message": "period is required"}
    if not entries:
        return {"ok": False, "message": "entries are required"}

    db.execute(
        "INSERT INTO esg_csv_uploads (file_name, mapping_name, status, rows_total, org_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        ("api_payload", "api", "pending", len(entries), org_id, created_by))
    db.commit()
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    committed = 0
    skipped = []
    linked_incidents = []
    for idx, item in enumerate(entries, start=1):
        kpi_code = item.get("kpi_code")
        actual_value = _coerce_number(str(item.get("actual_value", "")))
        row = db.execute("SELECT id FROM esg_kpis WHERE kpi_code = %s", (kpi_code,)).fetchone()
        if not row or actual_value is None:
            skipped.append({"index": idx, "reason": "unknown kpi_code or missing value"})
            db.execute(
                "INSERT INTO esg_csv_rows (upload_id, row_number, period, source_column, raw_value, "
                "status, anomaly_reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (upload_id, idx, period, kpi_code, str(item.get("actual_value", "")),
                 "anomalous", "unknown kpi_code or missing value"))
            continue
        kpi_id = row["id"]
        kpi = get_kpi(db, kpi_id)
        target = kpi.get("target_fy26")
        result = record_kpi_entry(
            db, kpi_id=kpi_id, period=period, actual_value=actual_value,
            target_value=float(target) if target is not None else None,
            notes=f"Pushed via API key {key_record['name']}",
            created_by=created_by, org_id=org_id)
        if result["ok"]:
            entry = result["entry"]
            db.execute(
                "UPDATE esg_kpi_entries SET source_upload_id = %s, source_row_id = %s WHERE id = %s",
                (upload_id, None, entry["id"]))
            db.execute(
                "INSERT INTO esg_csv_rows (upload_id, row_number, period, source_column, raw_value, "
                "mapped_kpi_id, actual_value, status, committed_entry_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (upload_id, idx, period, kpi_code, str(item.get("actual_value", "")),
                 kpi_id, actual_value, "committed", entry["id"]))
            committed += 1
            if entry.get("linked_incident_id"):
                linked_incidents.append(entry["linked_incident_id"])
        else:
            skipped.append({"index": idx, "reason": result.get("message", "record failed")})

    status = "committed" if committed else "failed"
    db.execute(
        "UPDATE esg_csv_uploads SET status = %s, rows_total = %s, rows_valid = %s, committed_at = %s, "
        "committed_by = %s WHERE id = %s",
        (status, len(entries), committed, datetime.now(timezone.utc).isoformat() if committed else None,
         created_by, upload_id))
    db.execute("UPDATE esg_api_keys SET last_used_at = %s WHERE id = %s",
               (datetime.now(timezone.utc).isoformat(), key_record["id"]))
    db.commit()
    return {"ok": True, "upload_id": upload_id, "committed": committed,
            "skipped": skipped, "linked_incident_ids": linked_incidents}
