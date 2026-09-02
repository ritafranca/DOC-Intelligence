from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import unicodedata
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from PIL import Image, ImageOps
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import BASE_DIR, settings


V2_EXTRACTED_FIELD_ORDER = (
    "nome",
    "nome_mae",
    "nome_pai",
    "cpf",
    "numero_rg",
    "naturalidade",
    "data_nascimento",
    "data_casamento",
    "orgao_emissor",
)
SUPPORTED_EXTRACTED_FIELDS = set(V2_EXTRACTED_FIELD_ORDER)
LEGACY_EXTRACTED_FIELDS = {"nome", "cpf", "rg", "data_nascimento"}
SUPPORTED_DOCUMENT_TYPES = {
    "RG",
    "CPF",
    "CNH",
    "CERTIDAO_NASCIMENTO",
    "CERTIDAO_CASAMENTO",
    "COMPROVANTE_RESIDENCIA",
    "OUTRO",
    "OUTROS",
}
V2_DOCUMENT_TYPES = {"RG", "CNH", "CERTIDAO_NASCIMENTO", "CERTIDAO_CASAMENTO", "OUTROS"}
OPENAI_PROMPT_VERSION = "document_extraction_v2"


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_type: str = Field(min_length=2, max_length=100)
    extracted_data: dict[str, Any]
    confidence_score: float = Field(ge=0, le=1)
    filename_suggested: str = Field(
        min_length=3,
        max_length=255,
        validation_alias=AliasChoices("filename_suggested", "suggested_filename"),
    )

    @field_validator("document_type")
    @classmethod
    def supported_document_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in SUPPORTED_DOCUMENT_TYPES:
            raise ValueError("Tipo de documento não suportado.")
        return normalized

    @field_validator("extracted_data")
    @classmethod
    def strict_extracted_data(cls, value: dict[str, Any]) -> dict[str, str | None]:
        keys = frozenset(value)
        if keys not in {frozenset(SUPPORTED_EXTRACTED_FIELDS), frozenset(LEGACY_EXTRACTED_FIELDS)}:
            raise ValueError("Conjunto de campos extraídos incompatível com os contratos publicados.")
        if any(item is not None and not isinstance(item, str) for item in value.values()):
            raise ValueError("Campos extraídos devem conter somente texto ou null.")
        return value

    @field_validator("filename_suggested")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        stem = Path(value).stem
        ascii_name = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
        cleaned = re.sub(r"[^A-Z0-9]+", "_", ascii_name.upper()).strip("_")
        if not cleaned:
            raise ValueError("Nome sugerido inválido.")
        return cleaned


def add_filename_extension(filename: str, mime_type: str) -> str:
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}[mime_type]
    return filename if filename.lower().endswith(extension) else f"{filename}{extension}"


def validate_prompt_contract(result: ExtractionResult, prompt_version: str) -> ExtractionResult:
    if prompt_version == "document_extraction_v2":
        if result.document_type not in V2_DOCUMENT_TYPES:
            raise ValueError("Tipo de documento incompatível com document_extraction_v2.")
        if set(result.extracted_data) != SUPPORTED_EXTRACTED_FIELDS:
            raise ValueError("Campos extraídos incompatíveis com document_extraction_v2.")
    return result


def empty_v2_extracted_data() -> dict[str, str | None]:
    return {field: None for field in V2_EXTRACTED_FIELD_ORDER}


def _safe_fallback(document_id: str) -> tuple[str, dict[str, str | None], float, str]:
    technical_suffix = hashlib.sha256(document_id.encode()).hexdigest()[:8].upper()
    return "OUTROS", empty_v2_extracted_data(), 0.0, f"OUTROS_DOCUMENTO_{technical_suffix}"


def _normalized_image_bytes(file_bytes: bytes) -> tuple[bytes, str]:
    with Image.open(io.BytesIO(file_bytes)) as image:
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


def prepare_openai_media(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    if mime_type == "application/pdf":
        with pymupdf.open(stream=file_bytes, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                raise ValueError("PDF sem páginas.")
            page = pdf.load_page(0)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            return pixmap.tobytes("png"), "image/png"
    return _normalized_image_bytes(file_bytes)


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


class BaseExtractorStrategy(DocumentExtractor):
    """Nome compatível com as estratégias de OCR sem quebrar o contrato existente."""


def _prepared_media(file_path: Path, mime_type: str) -> tuple[bytes, str]:
    if mime_type == "application/pdf":
        return file_path.read_bytes(), mime_type
    return _normalized_image_bytes(file_path.read_bytes())


class MockExtractorStrategy(BaseExtractorStrategy):
    strategy_name = "mock"
    model_version = "mock-vision-1.0"

    async def extract(self, **kwargs) -> ExtractionResult:
        await asyncio.sleep(0.35)
        original = kwargs["original_filename"]
        stem = Path(original).stem.lower()
        if "cnh" in stem:
            document_type = "CNH"
        elif "casamento" in stem:
            document_type = "CERTIDAO_CASAMENTO"
        elif "nascimento" in stem or "nasc" in stem:
            document_type = "CERTIDAO_NASCIMENTO"
        elif "rg" in stem or "identidade" in stem:
            document_type = "RG"
        else:
            document_type = "OUTROS"
        fingerprint = hashlib.sha256(kwargs["document_id"].encode()).hexdigest()
        confidence = round(0.74 + (int(fingerprint[:2], 16) / 255) * 0.20, 3)
        numbers = re.sub(r"\D", "", stem)
        extracted = {
            "nome": None,
            "nome_mae": None,
            "nome_pai": None,
            "cpf": numbers[-11:] if len(numbers) >= 11 else None,
            "numero_rg": numbers[-9:] if document_type == "RG" and len(numbers) >= 7 else None,
            "naturalidade": None,
            "data_nascimento": None,
            "data_casamento": None,
            "orgao_emissor": None,
        }
        return ExtractionResult(
            document_type=document_type,
            extracted_data=extracted,
            confidence_score=confidence,
            filename_suggested=f"{document_type}_{fingerprint[:8]}",
        )


MockDocumentExtractor = MockExtractorStrategy


class LocalOCRStrategy(BaseExtractorStrategy):
    strategy_name = "local"
    model_version = "paddleocr-local-cpu-v1"
    prompt_version = "local_ocr_rules_v1"

    def __init__(self, pipeline=None, executor: ThreadPoolExecutor | None = None) -> None:
        if pipeline is None:
            from app.local_ocr import LocalOCRPipeline

            pipeline = LocalOCRPipeline(
                lang=settings.local_ocr_lang,
                cpu_threads=settings.local_ocr_cpu_threads,
                poppler_path=settings.local_ocr_poppler_path,
            )
        self.pipeline = pipeline
        self.model_version = getattr(pipeline, "model_version", self.model_version)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=settings.local_ocr_executor_workers,
            thread_name_prefix="doc-local-ocr",
        )

    async def extract(self, **kwargs) -> ExtractionResult:
        file_bytes = await asyncio.to_thread(kwargs["file_path"].read_bytes)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._executor,
            partial(
                self.pipeline.extract,
                document_id=kwargs["document_id"],
                file_bytes=file_bytes,
                mime_type=kwargs["mime_type"],
            ),
        )
        return ExtractionResult(
            document_type=result.document_type,
            extracted_data=result.extracted_data,
            confidence_score=result.confidence_score,
            suggested_filename=result.suggested_filename,
        )


class OpenAIVisionStrategy(BaseExtractorStrategy):
    strategy_name = "openai"
    prompt_version = OPENAI_PROMPT_VERSION

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        if client is None and not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY é obrigatória para a estratégia openai.")
        self.model_version = settings.openai_model
        self.prompt = load_prompt(self.prompt_version)
        self.client = client or AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    async def extract_file_bytes(
        self,
        *,
        document_id: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, dict[str, str | None], float, str]:
        fallback = _safe_fallback(document_id)
        try:
            media, prepared_mime = await asyncio.to_thread(
                prepare_openai_media,
                file_bytes,
                mime_type,
            )
            encoded = base64.b64encode(media).decode("ascii")
            response = await self.client.chat.completions.create(
                model=self.model_version,
                messages=[
                    {"role": "system", "content": self.prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analise este documento e devolva somente o objeto JSON solicitado.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{prepared_mime};base64,{encoded}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=1200,
                store=False,
                extra_headers={"X-Client-Request-Id": f"doc-{document_id}"},
            )
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Resposta vazia do modelo.")
            candidate = json.loads(content)
            result = validate_prompt_contract(
                ExtractionResult.model_validate(candidate),
                self.prompt_version,
            )
            return (
                result.document_type,
                result.extracted_data,
                result.confidence_score,
                result.filename_suggested,
            )
        except (
            APITimeoutError,
            RateLimitError,
            APIConnectionError,
            APIStatusError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
            OSError,
        ):
            return fallback

    async def extract(self, **kwargs) -> ExtractionResult:
        file_bytes = await asyncio.to_thread(kwargs["file_path"].read_bytes)
        document_type, extracted_data, confidence, suggested_filename = (
            await self.extract_file_bytes(
                document_id=kwargs["document_id"],
                file_bytes=file_bytes,
                mime_type=kwargs["mime_type"],
            )
        )
        return ExtractionResult(
            document_type=document_type,
            extracted_data=extracted_data,
            confidence_score=confidence,
            suggested_filename=suggested_filename,
        )


class HttpVisionExtractor(BaseExtractorStrategy):
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
        result = ExtractionResult.model_validate(candidate)
        return validate_prompt_contract(result, settings.prompt_version)


def load_prompt(version: str) -> str:
    path = BASE_DIR / "prompts" / f"{version}.txt"
    if not path.is_file():
        raise RuntimeError(f"Prompt versionado não encontrado: {path.name}")
    return path.read_text(encoding="utf-8")


def build_extractor() -> DocumentExtractor:
    strategies: dict[str, type[DocumentExtractor]] = {
        "local": LocalOCRStrategy,
        "mock": MockExtractorStrategy,
        "openai": OpenAIVisionStrategy,
        "http-vision": HttpVisionExtractor,
    }
    extractor_type = strategies.get(settings.extractor_strategy)
    if not extractor_type:
        raise RuntimeError(f"Estratégia de extração desconhecida: {settings.extractor_strategy}")
    return extractor_type()
