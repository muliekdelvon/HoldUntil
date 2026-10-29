import os
import uuid
import logging
from datetime import datetime
from backend.app.config import settings

logger = logging.getLogger("storage_service")

# Local upload storage directory (persistent volume path on Docker/Coolify)
UPLOAD_BASE_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads"))
os.makedirs(UPLOAD_BASE_DIR, exist_ok=True)

class StorageService:
    """
    Unified Cloud & Local Object Storage Service.
    Supports Cloudflare R2, AWS S3, and local filesystem fallback.
    Driven 100% by environment variables with zero hardcoded credentials.
    """
    _s3_client = None

    @classmethod
    def _get_s3_client(cls):
        if cls._s3_client is not None:
            return cls._s3_client

        if settings.STORAGE_BACKEND not in ["s3", "r2"]:
            return None

        if not settings.S3_BUCKET_NAME or not settings.S3_ACCESS_KEY_ID or not settings.S3_SECRET_ACCESS_KEY:
            logger.warning("Cloud storage credentials not fully configured; falling back to local storage.")
            return None

        try:
            import boto3
            from botocore.config import Config

            client_kwargs = {
                "service_name": "s3",
                "aws_access_key_id": settings.S3_ACCESS_KEY_ID,
                "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY,
                "region_name": settings.S3_REGION_NAME or "auto",
                "config": Config(signature_version="s3v4")
            }

            if settings.S3_ENDPOINT_URL:
                client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

            cls._s3_client = boto3.client(**client_kwargs)
            logger.info(f"Initialized S3 storage client for bucket '{settings.S3_BUCKET_NAME}'")
            return cls._s3_client
        except Exception as e:
            logger.error(f"Failed to initialize boto3 S3 client: {e}")
            return None

    @classmethod
    def upload_file(
        cls,
        file_data: bytes,
        original_filename: str = "upload.jpg",
        content_type: str = "image/jpeg",
        folder: str = "evidence"
    ) -> str:
        """
        Uploads a file to Cloudflare R2 / S3 or local persistent storage.
        Returns the accessible URL for database storage and client access.
        """
        ext = os.path.splitext(original_filename)[1] or ".jpg"
        unique_name = f"{uuid.uuid4().hex[:16]}{ext}"
        object_key = f"{folder}/{unique_name}"

        # 1. Attempt Cloud Storage (R2 / S3)
        s3 = cls._get_s3_client()
        if s3:
            try:
                s3.put_object(
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=object_key,
                    Body=file_data,
                    ContentType=content_type
                )
                logger.info(f"Uploaded {object_key} to bucket '{settings.S3_BUCKET_NAME}'")

                if settings.S3_PUBLIC_DOMAIN:
                    domain = settings.S3_PUBLIC_DOMAIN.rstrip("/")
                    return f"{domain}/{object_key}"

                # Generate presigned URL valid for 7 days
                return s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.S3_BUCKET_NAME, "Key": object_key},
                    ExpiresIn=604800
                )
            except Exception as s3_err:
                logger.error(f"S3 upload failed for {object_key}: {s3_err}. Falling back to local storage.")

        # 2. Local Filesystem Storage Fallback
        target_dir = os.path.join(UPLOAD_BASE_DIR, folder)
        os.makedirs(target_dir, exist_ok=True)
        local_path = os.path.join(target_dir, unique_name)

        with open(local_path, "wb") as f:
            f.write(file_data)

        logger.info(f"Saved file locally to {local_path}")
        return f"/api/uploads/{folder}/{unique_name}"

    @classmethod
    def get_local_path(cls, file_url: str) -> str | None:
        """
        Resolves a /api/uploads/ URL back to an absolute local filesystem path
        for perceptual hashing, EXIF extraction, and OCR.
        """
        if not file_url:
            return None

        if file_url.startswith("/api/uploads/"):
            rel_path = file_url.replace("/api/uploads/", "").lstrip("/")
            full_path = os.path.join(UPLOAD_BASE_DIR, rel_path)
            if os.path.exists(full_path):
                return full_path

        # If it's already an existing local path
        if os.path.exists(file_url):
            return file_url

        return None
