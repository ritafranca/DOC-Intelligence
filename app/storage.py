from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import aioboto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.config import settings


ALLOWED_MIME_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._() -]+")


@dataclass(slots=True)
class StoredUpload:
    temp_path: Path
    file_hash: str
    mime_type: str
    size_bytes: int
    original_name: str


class ObjectStorage(Protocol):
    async def start(self) -> None: ...
    async def healthcheck(self) -> None: ...
    async def put_file(self, object_key: str, path: Path, mime_type: str) -> None: ...
    async def get_bytes(self, object_key: str) -> bytes: ...
    async def download_file(self, object_key: str, destination: Path) -> None: ...
    async def delete(self, object_key: str) -> None: ...


def sanitize_filename(filename: str | None) -> str:
    raw = Path(filename or "documento").name
    cleaned = SAFE_NAME_PATTERN.sub("_", raw).strip(" .")
    return (cleaned or "documento")[:255]


def detect_mime(header: bytes) -> str | None:
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _validate_content(path: Path, mime_type: str) -> None:
    try:
        if mime_type.startswith("image/"):
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                if width < 64 or height < 64:
                    raise ValueError("A imagem é pequena demais para extração.")
                if width * height > 50_000_000:
                    raise ValueError("A imagem excede o limite de 50 megapixels.")
        else:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ValueError("PDF protegido por senha não é aceito.")
            if not reader.pages:
                raise ValueError("PDF sem páginas.")
            if len(reader.pages) > settings.max_pdf_pages:
                raise ValueError(f"PDF excede o limite de {settings.max_pdf_pages} páginas.")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        raise ValueError("Conteúdo corrompido ou inválido.") from exc


async def receive_upload(upload: UploadFile) -> StoredUpload:
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    original_name = sanitize_filename(upload.filename)
    temp_path = settings.temp_dir / f"{uuid.uuid4()}.upload"
    digest = hashlib.sha256()
    size = 0
    header = bytearray()

    try:
        with temp_path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Arquivo excede o limite de {settings.max_upload_bytes // (1024 * 1024)} MB.",
                    )
                if len(header) < 16:
                    header.extend(chunk[: 16 - len(header)])
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Arquivo vazio.")
        mime_type = detect_mime(bytes(header))
        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=415, detail="Somente JPEG, PNG e PDF são aceitos.")
        await asyncio.to_thread(_validate_content, temp_path, mime_type)
        return StoredUpload(temp_path, digest.hexdigest(), mime_type, size, original_name)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except ValueError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def make_object_key(document_id: str, file_hash: str, mime_type: str) -> str:
    return f"documents/{file_hash[:2]}/{document_id}{ALLOWED_MIME_TYPES[mime_type]}"


class S3ObjectStorage:
    def __init__(self) -> None:
        self._session = aioboto3.Session()

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def _encryption_args(self) -> dict:
        args = {"ServerSideEncryption": settings.s3_sse_algorithm}
        if settings.s3_sse_algorithm == "aws:kms":
            args["SSEKMSKeyId"] = settings.s3_sse_kms_key_id
            args["BucketKeyEnabled"] = True
        return args

    async def start(self) -> None:
        if not settings.s3_auto_create_bucket:
            await self.healthcheck()
            return
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=settings.s3_bucket)
            except ClientError:
                kwargs = {"Bucket": settings.s3_bucket}
                if settings.s3_region != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {
                        "LocationConstraint": settings.s3_region
                    }
                await client.create_bucket(**kwargs)

    async def healthcheck(self) -> None:
        async with self._client() as client:
            await client.head_bucket(Bucket=settings.s3_bucket)

    async def put_file(self, object_key: str, path: Path, mime_type: str) -> None:
        extra = {"ContentType": mime_type, **self._encryption_args()}
        async with self._client() as client:
            await client.upload_file(str(path), settings.s3_bucket, object_key, ExtraArgs=extra)

    async def get_bytes(self, object_key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=settings.s3_bucket, Key=object_key)
            return await response["Body"].read()

    async def download_file(self, object_key: str, destination: Path) -> None:
        async with self._client() as client:
            await client.download_file(settings.s3_bucket, object_key, str(destination))

    async def delete(self, object_key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=settings.s3_bucket, Key=object_key)


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def start(self) -> None:
        return None

    async def healthcheck(self) -> None:
        return None

    async def put_file(self, object_key: str, path: Path, _mime_type: str) -> None:
        self.objects[object_key] = path.read_bytes()

    async def get_bytes(self, object_key: str) -> bytes:
        return self.objects[object_key]

    async def download_file(self, object_key: str, destination: Path) -> None:
        destination.write_bytes(self.objects[object_key])

    async def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)
