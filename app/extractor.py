from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import BASE_DIR, settings


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: str = Field(min_length=2, max_length=100)
    extracted_data: dict[str, Any]
    confidence_score: float = Field(ge=0, le=1)
    filename_suggested: str = Field(min_length=3, max_length=255)

    @field_validator("filename_suggested")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._() -]+", "_", Path(value).name).strip(" .")
        if not cleaned:
            raise ValueError("Nome sugerido inválido.")
        return cleaned


class DocumentExtractor(ABC):
    strategy_name: str
    model_version: str

    @abstractmethod
    async def extract(
        self,
        *,
        document_id: str,
        file_path: Path,
        mime_type: str,
        original_filename: str,
        prompt: str,
    ) -> ExtractionResult:
        raise NotImplementedError


def _prepared_media(file_path: Path, mime_type: str) -> tuple[bytes, str]:
    if mime_type == "application/pdf":
        return file_path.read_bytes(), mime_type
    with Image.open(file_path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            canvas = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                canvas.paste(image, mask=image.getchannel("A"))
            else:
                canvas.paste(image)
            image = canvas
        elif image.mode == "L":
            image = image.convert("RGB")
        image.thumbnail((2400, 2400))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        return buffer.getvalue(), "image/jpeg"


class MockDocumentExtractor(DocumentExtractor):
    strategy_name = "mock"
    model_version = "mock-vision-1.0"

    async def extract(self, **kwargs) -> ExtractionResult:
        await asyncio.sleep(0.35)
        original = kwargs["original_filename"]
        stem = Path(original).stem.lower()
        document_type = "CPF" if "cpf" in stem else "RG" if "rg" in stem else "DOCUMENTO_PESSOAL"
        fingerprint = hashlib.sha256(kwargs["document_id"].encode()).hexdigest()
        confidence = round(0.74 + (int(fingerprint[:2], 16) / 255) * 0.20, 3)
        numbers = re.sub(r"\D", "", stem)
        extracted = {
            "nome": None,
            "cpf": numbers[-11:] if len(numbers) >= 11 else None,
            "rg": numbers[-9:] if document_type == "RG" and len(numbers) >= 7 else None,
            "data_nascimento": None,
        }
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}[
            kwargs["mime_type"]
        ]
        return ExtractionResult(
            document_type=document_type,
            extracted_data=extracted,
            confidence_score=confidence,
            filename_suggested=f"{document_type.lower()}_{fingerprint[:8]}{extension}",
        )


class HttpVisionExtractor(DocumentExtractor):
    strategy_name = "http-vision"

    def __init__(self) -> None:
        if not settings.provider_url or not settings.provider_api_key:
            raise RuntimeError("LLM_PROVIDER_URL e LLM_PROVIDER_API_KEY são obrigatórios para http-vision.")
        self.model_version = settings.provider_model

    async def extract(self, **kwargs) -> ExtractionResult:
        media, prepared_mime = await asyncio.to_thread(
            _prepared_media, kwargs["file_path"], kwargs["mime_type"]
        )
        payload = {
            "model": self.model_version,
            "prompt": kwargs["prompt"],
            "prompt_version": settings.prompt_version,
            "document": {
                "mime_type": prepared_mime,
                "content_base64": base64.b64encode(media).decode("ascii"),
            },
        }
        headers = {
            "Authorization": f"Bearer {settings.provider_api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"doc-{kwargs['document_id']}",
        }
        timeout = httpx.Timeout(settings.provider_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(settings.provider_url, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        candidate = body.get("result", body)
        if isinstance(candidate, str):
            candidate = json.loads(candidate)
        return ExtractionResult.model_validate(candidate)


def load_prompt(version: str) -> str:
    path = BASE_DIR / "prompts" / f"{version}.txt"
    if not path.is_file():
        raise RuntimeError(f"Prompt versionado não encontrado: {path.name}")
    return path.read_text(encoding="utf-8")


def build_extractor() -> DocumentExtractor:
    strategies: dict[str, type[DocumentExtractor]] = {
        "mock": MockDocumentExtractor,
        "http-vision": HttpVisionExtractor,
    }
    extractor_type = strategies.get(settings.extractor_strategy)
    if not extractor_type:
        raise RuntimeError(f"Estratégia de extração desconhecida: {settings.extractor_strategy}")
    return extractor_type()
