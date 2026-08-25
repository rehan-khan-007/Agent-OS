"""
S3-compatible object storage client for Cloudflare R2, used to store
uploaded documents durably and independently of any single running
process.

Why this matters: the previous approach (writing uploads to local
/tmp) only worked because the API server and the background worker
happen to run in the same process today. It would silently break the
moment a worker ran on a separate machine/container, since it would
have no access to the uploading server's local filesystem — the
worker would try to read a file path that simply doesn't exist there.

boto3 is a synchronous library with no native async support — every
call here is wrapped in asyncio.to_thread() so a slow upload/download
doesn't block the event loop other requests are running on.
"""

import asyncio

import boto3

from app.config import settings
from app.observability.logging import get_logger

logger = get_logger(__name__)


def is_configured() -> bool:
    """Whether R2 credentials are actually set — callers use this to
    fall back to local-path behavior in dev environments that don't
    have R2 credentials configured."""
    return bool(
        settings.r2_endpoint_url
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket_name
    )


def _get_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


async def upload_bytes(key: str, data: bytes) -> None:
    """Uploads raw bytes to R2 under the given object key."""
    def _upload():
        client = _get_client()
        client.put_object(Bucket=settings.r2_bucket_name, Key=key, Body=data)
    await asyncio.to_thread(_upload)
    logger.info("Uploaded object to R2", extra={"extra_fields": {"key": key, "bytes": len(data)}})


async def download_to_path(key: str, local_path: str) -> None:
    """Downloads an R2 object to a local file path — needed because
    the PDF/text parsing libraries this project uses read from a
    local file, not a byte stream directly."""
    def _download():
        client = _get_client()
        client.download_file(settings.r2_bucket_name, key, local_path)
    await asyncio.to_thread(_download)
    logger.info("Downloaded object from R2", extra={"extra_fields": {"key": key, "local_path": local_path}})


async def delete_object(key: str) -> None:
    """Deletes an R2 object — called only after a document has been
    fully, successfully ingested. The raw uploaded file is no longer
    needed once its chunks are embedded and stored, and cleaning it up
    keeps R2 usage well within the free tier as the corpus grows."""
    def _delete():
        client = _get_client()
        client.delete_object(Bucket=settings.r2_bucket_name, Key=key)
    await asyncio.to_thread(_delete)
    logger.info("Deleted object from R2", extra={"extra_fields": {"key": key}})
