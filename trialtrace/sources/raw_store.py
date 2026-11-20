##############################################################################
# trialtrace/sources/raw_store.py
#
# PURPOSE: Save each downloaded response untouched, in a file named after its
#          content hash. Same content = same name, so nothing is saved twice
#          and every claim can point back to the exact file it came from.
#
# USE:     from trialtrace.sources.raw_store import save_raw
#          stored = save_raw(nct_id, url, retrieved_at, body, data_dir)
##############################################################################

import hashlib                              # sha256 fingerprints
import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

# Fields the registry changes on every response, even when the trial did not
# change. Left out of the hash, or the same trial would look "new" every day.
VOLATILE_FIELDS = {
    "clinicaltrials": [("derivedSection", "miscInfoModule", "versionHolder")],
}


class StoredRaw(BaseModel):
    """Where a raw response was saved, and its fingerprint."""

    nct_id: str
    url: str
    retrieved_at: datetime
    content_hash: str
    raw_file_path: str


def compute_content_hash(body: bytes, source: str) -> str:
    """sha256 of the record's content, ignoring volatile fields.

    Same record on different days -> same hash. Any real change -> new hash.
    Bytes that are not valid JSON are hashed as-is (the parser rejects them later).
    """
    paths = VOLATILE_FIELDS.get(source)
    if not paths:
        return hashlib.sha256(body).hexdigest()
    try:
        data = json.loads(body)
    except ValueError:                      # not JSON: hash the raw bytes
        return hashlib.sha256(body).hexdigest()

    for path in paths:
        parent = data
        for key in path[:-1]:               # walk down to the folder holding the field
            parent = parent.get(key) if isinstance(parent, dict) else None
        if isinstance(parent, dict):
            parent.pop(path[-1], None)      # remove the volatile field if present

    # Canonical form: the same content always becomes the same bytes
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upload_to_bucket(bucket_name: str, key: str, path: Path) -> None:
    """Copy one local file to Google Cloud Storage as <key>. Never overwrites an existing object."""
    from google.api_core.exceptions import PreconditionFailed      # imported here: only needed when a bucket is set
    from google.cloud import storage

    blob = storage.Client().bucket(bucket_name).blob(key)
    try:
        blob.upload_from_filename(str(path), if_generation_match=0)    # 0 = only if the object does not exist yet
    except PreconditionFailed:
        pass                                                           # already there: the first copy stays the evidence


def save_raw(
    nct_id: str,
    url: str,
    retrieved_at: datetime,
    body: bytes,
    data_dir: Path,
    source: str = "clinicaltrials",
    extension: str = "json",
    bucket: str | None = None,
) -> StoredRaw:
    """Save untouched bytes under raw/<source>/<NCT>/<hash>.<extension>, once.

    If a bucket name is given, the same files are also copied to gs://<bucket>/raw/<source>/<NCT>/ (a backup that
    survives the laptop). A failed upload is logged and does not stop the ingest; `gcloud storage rsync` catches up later.
    """
    content_hash = compute_content_hash(body, source)

    folder = data_dir / "raw" / source / nct_id
    folder.mkdir(parents=True, exist_ok=True)             # create missing parents; fine if it exists

    raw_path = folder / f"{content_hash}.{extension}"

    stored = StoredRaw(
        nct_id=nct_id,
        url=url,
        retrieved_at=retrieved_at,
        content_hash=content_hash,
        raw_file_path=raw_path.as_posix(),                # forward slashes on every OS
    )

    # Write only if this content is new. Never overwrite: the first file stays the evidence.
    if not raw_path.exists():
        meta_path = folder / f"{content_hash}.meta.json"
        meta_path.write_text(stored.model_dump_json(indent=2), encoding="utf-8")
        raw_path.write_bytes(body)                        # last: the raw file's existence means "fully saved"

    if bucket:
        for file in (raw_path, folder / f"{content_hash}.meta.json"):
            key = f"raw/{source}/{nct_id}/{file.name}"
            try:
                upload_to_bucket(bucket, key, file)
            except Exception as error:                    # a backup problem must not lose the download
                logging.warning("raw backup to gs://%s/%s failed: %s", bucket, key, error)

    return stored
