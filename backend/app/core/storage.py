"""S3-compatible object storage abstraction.

Uses boto3 under the hood. Locally: MinIO. Production: AWS S3.
Never uses user-provided filenames for storage keys — always generates
a non-guessable key (per security plan §2).
"""

import uuid
from io import BytesIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _get_s3_client():
    """Create a boto3 S3 client pointing at MinIO (local) or real S3.

    connect_timeout/read_timeout bound this call from an API request
    handler's perspective — without them a stalled storage backend hangs
    the request indefinitely instead of surfacing a 5xx.
    """
    if settings.minio_endpoint and "minio" in settings.minio_endpoint:
        return boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_root_user,
            aws_secret_access_key=settings.minio_root_password,
            region_name=settings.aws_region,
            config=BotoConfig(signature_version="s3v4", connect_timeout=10, read_timeout=30),
        )
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        config=BotoConfig(connect_timeout=10, read_timeout=30),
    )


def generate_storage_key(filename: str, prefix: str = "cvs/") -> str:
    """Generate a non-guessable storage key. Never includes the client filename.

    Format: {prefix}{uuid}.{original_extension_lower}
    This prevents path traversal and key collisions across users.

    `prefix` defaults to the existing "cvs/" for backward compatibility;
    cvs.py's upload endpoint passes "quarantine/" so an unscanned upload
    lands somewhere nothing but the scanning step ever reads from (see
    jbs-solution-sheet.md S6).
    """
    ext = ""
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        # Sanitize: only alphanumeric, max 10 chars
        ext = "".join(c for c in ext if c.isalnum())[:10]
        if ext:
            ext = f".{ext}"
    return f"{prefix}{uuid.uuid4().hex}{ext}"


async def upload_file(file_content: bytes, storage_key: str, content_type: str) -> None:
    """Upload a file to object storage."""
    s3 = _get_s3_client()
    try:
        s3.put_object(
            Bucket=settings.s3_bucket_name,
            Key=storage_key,
            Body=file_content,
            ContentType=content_type,
        )
        logger.info("file_uploaded", storage_key=storage_key, size=len(file_content))
    except ClientError as e:
        logger.error("storage_upload_failed", key=storage_key, error=str(e))
        raise


async def download_file(storage_key: str) -> bytes:
    """Download a file from object storage by its key."""
    s3 = _get_s3_client()
    try:
        response = s3.get_object(Bucket=settings.s3_bucket_name, Key=storage_key)
        return response["Body"].read()
    except ClientError as e:
        logger.error("storage_download_failed", key=storage_key, error=str(e))
        raise


async def delete_file(storage_key: str) -> None:
    """Delete a file from object storage."""
    s3 = _get_s3_client()
    try:
        s3.delete_object(Bucket=settings.s3_bucket_name, Key=storage_key)
        logger.info("file_deleted", storage_key=storage_key)
    except ClientError as e:
        logger.error("storage_delete_failed", key=storage_key, error=str(e))
        raise


async def ensure_bucket_exists() -> None:
    """Create the storage bucket if it doesn't already exist (local dev only)."""
    s3 = _get_s3_client()
    try:
        s3.head_bucket(Bucket=settings.s3_bucket_name)
    except ClientError:
        s3.create_bucket(Bucket=settings.s3_bucket_name)
        logger.info("bucket_created", bucket=settings.s3_bucket_name)