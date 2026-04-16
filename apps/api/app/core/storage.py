"""
MinIO / S3-compatible object storage client.

All public functions are async. The MinIO SDK is synchronous, so every
call is dispatched to a thread-pool executor to avoid blocking the asyncio
event loop.
"""
import asyncio
import io
from typing import Optional
from minio import Minio
from minio.error import S3Error
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("storage")

_client: Optional[Minio] = None


def get_storage_client() -> Minio:
    global _client
    if _client is None:
        endpoint = settings.MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
        secure = settings.MINIO_ENDPOINT.startswith("https://")
        _client = Minio(
            endpoint,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=secure,
        )
    return _client


def _init_storage_sync():
    """Synchronous bucket init — called from executor."""
    client = get_storage_client()
    bucket = settings.MINIO_BUCKET
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("storage.bucket_created", bucket=bucket)
    else:
        logger.info("storage.bucket_exists", bucket=bucket)

    # Automatically set bucket to public read-only to avoid SignatureDoesNotMatch errors 
    # across LAN/Docker IP mismatches when serving PDFs.
    import json
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"]
            }
        ]
    }
    client.set_bucket_policy(bucket, json.dumps(policy))


async def init_storage():
    """Create bucket if not exists (non-blocking)."""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _init_storage_sync)
    except S3Error as e:
        logger.error("storage.init_error", error=str(e))
    except Exception as e:
        logger.error("storage.init_unexpected_error", error=str(e), exc_info=True)


def _upload_sync(object_name: str, data: bytes, content_type: str) -> str:
    """Synchronous upload — called from executor."""
    client = get_storage_client()
    client.put_object(
        settings.MINIO_BUCKET,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return f"{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET}/{object_name}"


async def upload_file(
    object_name: str, data: bytes, content_type: str = "application/octet-stream"
) -> str:
    """Upload bytes to MinIO, return object URL (non-blocking)."""
    loop = asyncio.get_running_loop()
    try:
        url = await loop.run_in_executor(None, _upload_sync, object_name, data, content_type)
        logger.info("storage.upload_success", object_name=object_name, bytes=len(data))
        return url
    except S3Error as e:
        logger.error("storage.upload_error", object_name=object_name, error=str(e))
        raise


def _download_sync(object_name: str) -> bytes:
    """Synchronous download — called from executor."""
    client = get_storage_client()
    response = client.get_object(settings.MINIO_BUCKET, object_name)
    try:
        return response.read()
    finally:
        response.close()
        # Note: MinIO SDK response does not have release_conn(); close() is sufficient.


async def download_file(object_name: str) -> bytes:
    """Download file bytes from MinIO (non-blocking)."""
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _download_sync, object_name)
        logger.info("storage.download_success", object_name=object_name, bytes=len(data))
        return data
    except S3Error as e:
        logger.error("storage.download_error", object_name=object_name, error=str(e))
        raise


def _delete_sync(object_name: str):
    """Synchronous delete — called from executor."""
    client = get_storage_client()
    client.remove_object(settings.MINIO_BUCKET, object_name)


async def delete_file(object_name: str):
    """Delete an object from MinIO (non-blocking)."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _delete_sync, object_name)
        logger.info("storage.delete_success", object_name=object_name)
    except S3Error as e:
        logger.error("storage.delete_error", object_name=object_name, error=str(e))
        raise
