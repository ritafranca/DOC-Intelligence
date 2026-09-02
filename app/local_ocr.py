from __future__ import annotations

import hashlib
import io
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol, Sequence

from PIL import Image, ImageOps


CPF_PATTERN = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
RG_PATTERN = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-[A-Z0-9]{1,2}\b", re.IGNORECASE)
RG_COMPACT_PATTERN = re.compile(r"\b\d{7,10}(?:-[A-Z0-9]{1,2})?\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class LocalPipelineResult:
    document_type: str
    extracted_data: dict[str, str | None]
    confidence_score: float
    suggested_filename: str


class OCRBackend(Protocol):
    model_version: str

    def recognize(self, image: Any) -> list[OCRLine]: ...


def normalize_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", ascii_value.upper()).strip()


def safe_filename_component(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", normalize_text(value)).strip("_")


def _compact(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def _line_matches_label(value: str, label: str) -> bool:
    candidate = _compact(value)
    expected = _compact(label)
    if expected.endswith("PAI") and candidate.endswith("MAE"):
        return False
    if expected.endswith("MAE") and candidate.endswith("PAI"):
        return False
    if candidate == expected:
        return True
    if len(expected) >= 6 and candidate.startswith(expected):
        return True
    ascii_raw = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().upper()
    if re.match(rf"^\s*{re.escape(ascii_label)}\s*[:\-]", ascii_raw):
        return True
    if len(expected) < 6 or len(candidate) < 6:
        return False
    return SequenceMatcher(None, candidate[: max(len(expected), len(candidate))], expected).ratio() >= 0.68


def classify_document(lines: Sequence[OCRLine]) -> str:
    text = "\n".join(normalize_text(line.text) for line in lines)
    if any(
        term in text for term in ("CERTIDAO DE CASAMENTO", "CERTIDAO CASAMENTO")
    ) or any(
        "CERT" in normalize_text(line.text)
        and _line_matches_label(line.text, "CERTIDAO DE CASAMENTO")
        for line in lines
    ):
        return "CERTIDAO_CASAMENTO"
    if any(
        term in text for term in ("CERTIDAO DE NASCIMENTO", "CERTIDAO NASCIMENTO")
    ) or any(
        "CERT" in normalize_text(line.text)
        and _line_matches_label(line.text, "CERTIDAO DE NASCIMENTO")
        for line in lines
    ):
        return "CERTIDAO_NASCIMENTO"
    if any(
        term in text
        for term in (
            "CARTEIRA NACIONAL DE HABILITACAO",
            "PERMISSAO PARA DIRIGIR",
            "DETRAN",
            "CNH",
        )
    ) or any(
        any(token in normalize_text(line.text) for token in ("HABIL", "PERMISS", "CARTEIRA NACIONAL"))
        and _line_matches_label(line.text, term)
        for line in lines
        for term in ("CARTEIRA NACIONAL DE HABILITACAO", "PERMISSAO PARA DIRIGIR")
    ):
        return "CNH"
    if any(
        term in text
        for term in (
            "REGISTRO GERAL",
            "CARTEIRA DE IDENTIDADE",
            "SECRETARIA DE SEGURANCA PUBLICA",
        )
    ) or any(
        any(token in normalize_text(line.text) for token in ("REGI", "CARTEIRA"))
        and _line_matches_label(line.text, term)
        for line in lines
        for term in ("REGISTRO GERAL", "CARTEIRA DE IDENTIDADE")
    ):
        return "RG"
    rg_layout_markers = sum(
        any(_line_matches_label(line.text, label) for line in lines)
        for label in ("NOME", "CPF", "NATURALIDADE", "ORGAO EMISSOR")
    )
    if rg_layout_markers >= 3:
        return "RG"
    return "OUTROS"


KNOWN_LABELS = {
    "NOME",
    "FILIACAO",
    "FILIACAO MAE",
    "FILIACAO PAI",
    "NOME DA MAE",
    "NOME DO PAI",
    "MAE",
    "PAI",
    "CPF",
    "REGISTRO GERAL",
    "RG",
    "NATURALIDADE",
    "DATA DE NASCIMENTO",
    "NASCIMENTO",
    "DATA DE CASAMENTO",
    "ORGAO EMISSOR",
    "ORGAO EXPEDIDOR",
}


def _looks_like_label(value: str) -> bool:
    return any(_line_matches_label(value, label) for label in KNOWN_LABELS)


def _looks_like_name(value: str) -> bool:
    normalized = normalize_text(value).strip(" :-")
    if len(normalized) < 5 or any(character.isdigit() for character in normalized):
        return False
    if _looks_like_label(normalized):
        return False
    words = re.findall(r"[A-Z]+", normalized)
    return len(words) >= 2


def _same_line_value(raw: str, normalized_label: str) -> str | None:
    if ":" in raw:
        candidate = raw.split(":", 1)[1].strip(" -")
        return candidate or None
    normalized = normalize_text(raw)
    if normalized.startswith(f"{normalized_label} "):
        word_count = len(normalized_label.split())
        candidate = " ".join(raw.strip().split()[word_count:]).strip(" -")
        return candidate or None
    return None


def value_after_label(
    lines: Sequence[OCRLine],
    labels: Sequence[str],
    *,
    validator=None,
) -> str | None:
    normalized_labels = tuple(normalize_text(label) for label in labels)
    for index, line in enumerate(lines):
        for label in normalized_labels:
            if _line_matches_label(line.text, label):
                inline = _same_line_value(line.text, label)
                if inline and (validator is None or validator(inline)):
                    return inline
                for candidate_line in lines[index + 1 : index + 4]:
                    candidate = candidate_line.text.strip(" :-")
                    if not candidate or _looks_like_label(candidate):
                        continue
                    if validator is None or validator(candidate):
                        return candidate
    return None


def _first_pattern(lines: Sequence[OCRLine], pattern: re.Pattern[str]) -> str | None:
    for line in lines:
        match = pattern.search(line.text)
        if match:
            return match.group(0)
    return None


def _valid_date(value: str | None) -> str | None:
    if not value:
        return None
    match = DATE_PATTERN.search(value)
    if not match:
        return None
    candidate = match.group(0)
    try:
        datetime.strptime(candidate, "%d/%m/%Y")
    except ValueError:
        return None
    return candidate


def _extract_date(lines: Sequence[OCRLine], labels: Sequence[str]) -> str | None:
    labeled = value_after_label(lines, labels, validator=lambda value: DATE_PATTERN.search(value) is not None)
    result = _valid_date(labeled)
    return result or _valid_date(_first_pattern(lines, DATE_PATTERN))


def _extract_rg(lines: Sequence[OCRLine]) -> str | None:
    labeled = value_after_label(
        lines,
        ("REGISTRO GERAL", "RG"),
        validator=lambda value: bool(RG_PATTERN.search(value) or RG_COMPACT_PATTERN.search(value)),
    )
    if labeled:
        match = RG_PATTERN.search(labeled) or RG_COMPACT_PATTERN.search(labeled)
        if match:
            return match.group(0)
    return _first_pattern(lines, RG_PATTERN) or _first_pattern(lines, RG_COMPACT_PATTERN)


def extract_fields(lines: Sequence[OCRLine], document_type: str) -> dict[str, str | None]:
    nome = value_after_label(lines, ("NOME", "NOME COMPLETO"), validator=_looks_like_name)
    nome_mae = value_after_label(
        lines,
        ("NOME DA MAE", "FILIACAO MAE", "MAE"),
        validator=_looks_like_name,
    )
    nome_pai = value_after_label(
        lines,
        ("NOME DO PAI", "FILIACAO PAI", "PAI"),
        validator=_looks_like_name,
    )

    if not nome_mae or not nome_pai:
        filiacao_index = next(
            (
                index
                for index, line in enumerate(lines)
                if normalize_text(line.text).strip(" :-") == "FILIACAO"
            ),
            None,
        )
        if filiacao_index is not None:
            candidates = [
                line.text.strip(" :-")
                for line in lines[filiacao_index + 1 : filiacao_index + 6]
                if _looks_like_name(line.text)
            ]
            nome_mae = nome_mae or (candidates[0] if candidates else None)
            nome_pai = nome_pai or (candidates[1] if len(candidates) > 1 else None)

    if nome and (not nome_mae or not nome_pai) and document_type in {"RG", "CNH"}:
        holder_index = next(
            (
                index
                for index, line in enumerate(lines)
                if normalize_text(line.text) == normalize_text(nome)
            ),
            None,
        )
        if holder_index is not None:
            end_index = next(
                (
                    index
                    for index in range(holder_index + 1, len(lines))
                    if _line_matches_label(lines[index].text, "NATURALIDADE")
                    or _line_matches_label(lines[index].text, "CPF")
                ),
                len(lines),
            )
            existing = {normalize_text(value) for value in (nome, nome_mae, nome_pai) if value}
            candidates = [
                line.text.strip(" :-")
                for line in lines[holder_index + 1 : end_index]
                if _looks_like_name(line.text)
                and normalize_text(line.text) not in existing
            ]
            if not nome_mae and candidates:
                nome_mae = candidates.pop(0)
            if not nome_pai and candidates:
                nome_pai = candidates.pop(0)

    cpf = _first_pattern(lines, CPF_PATTERN)
    data_nascimento = _extract_date(lines, ("DATA DE NASCIMENTO", "NASCIMENTO", "DATA NASC"))
    data_casamento = (
        _extract_date(lines, ("DATA DE CASAMENTO", "CASAMENTO"))
        if document_type == "CERTIDAO_CASAMENTO"
        else None
    )
    naturalidade = value_after_label(lines, ("NATURALIDADE",))
    orgao_emissor = value_after_label(lines, ("ORGAO EMISSOR", "ORGAO EXPEDIDOR"))

    return {
        "nome": nome,
        "nome_mae": nome_mae,
        "nome_pai": nome_pai,
        "cpf": cpf,
        "numero_rg": _extract_rg(lines) if document_type in {"RG", "CNH"} else None,
        "naturalidade": naturalidade,
        "data_nascimento": data_nascimento,
        "data_casamento": data_casamento,
        "orgao_emissor": orgao_emissor,
    }


def calculate_confidence(
    lines: Sequence[OCRLine],
    document_type: str,
    fields: dict[str, str | None],
) -> float:
    if not lines:
        return 0.0
    ocr_confidence = sum(max(0.0, min(1.0, line.confidence)) for line in lines) / len(lines)
    required_by_type = {
        "RG": ("nome", "cpf", "numero_rg", "data_nascimento", "nome_mae", "nome_pai"),
        "CNH": ("nome", "cpf", "numero_rg", "data_nascimento"),
        "CERTIDAO_NASCIMENTO": ("nome", "data_nascimento", "nome_mae", "nome_pai"),
        "CERTIDAO_CASAMENTO": ("nome", "data_casamento"),
        "OUTROS": ("nome", "cpf"),
    }
    required_fields = required_by_type[document_type]
    field_presence = sum(int(bool(fields[field])) for field in required_fields) / len(required_fields)
    classification_confidence = 1.0 if document_type != "OUTROS" else 0.0
    score = 0.45 * ocr_confidence + 0.45 * field_presence + 0.10 * classification_confidence
    cpf_is_critical = document_type in {"RG", "CNH", "OUTROS"}
    if not fields["nome"] or (cpf_is_critical and not fields["cpf"]):
        score = min(score, 0.84)
    if document_type == "OUTROS":
        score = min(score, 0.70)
    return round(max(0.0, min(1.0, score)), 3)


def _box_sort_key(line: OCRLine) -> tuple[float, float]:
    if not line.box:
        return (float("inf"), float("inf"))
    return (min(point[1] for point in line.box), min(point[0] for point in line.box))


def _coerce_box(value: Any) -> tuple[tuple[float, float], ...]:
    try:
        return tuple((float(point[0]), float(point[1])) for point in value)
    except (TypeError, ValueError, IndexError):
        return ()


def parse_paddle_v3_results(results: Sequence[Any]) -> list[OCRLine]:
    parsed: list[OCRLine] = []
    for result in results:
        payload = getattr(result, "json", result)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, dict):
            continue
        payload = payload.get("res", payload)
        texts = payload.get("rec_texts") or payload.get("texts") or []
        scores = payload.get("rec_scores") or payload.get("scores") or []
        boxes = payload.get("rec_polys") or payload.get("dt_polys") or []
        for index, text in enumerate(texts):
            if not str(text).strip():
                continue
            score = float(scores[index]) if index < len(scores) else 0.0
            box = _coerce_box(boxes[index]) if index < len(boxes) else ()
            parsed.append(OCRLine(str(text).strip(), score, box))
    return sorted(parsed, key=_box_sort_key)


def parse_paddle_v2_results(results: Sequence[Any]) -> list[OCRLine]:
    parsed: list[OCRLine] = []
    pages = results or []
    if pages and pages[0] and len(pages[0]) == 2 and isinstance(pages[0][1], tuple):
        pages = [pages]
    for page in pages:
        for item in page or []:
            try:
                box, recognition = item
                text, score = recognition
            except (TypeError, ValueError):
                continue
            if str(text).strip():
                parsed.append(OCRLine(str(text).strip(), float(score), _coerce_box(box)))
    return sorted(parsed, key=_box_sort_key)


class PaddleOCRBackend:
    def __init__(self, *, lang: str = "pt", cpu_threads: int = 4) -> None:
        try:
            import paddleocr
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR não instalado. Use Python 3.11-3.13 e instale requirements.txt."
            ) from exc

        self.model_version = (
            f"paddleocr-{getattr(paddleocr, '__version__', 'unknown')}-ppocrv5-mobile-latin-cpu"
        )
        self._lock = threading.Lock()
        self._api_version = 3
        try:
            self._engine = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="latin_PP-OCRv5_mobile_rec",
                device="cpu",
                use_doc_orientation_classify=True,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                enable_mkldnn=True,
                cpu_threads=cpu_threads,
            )
        except TypeError:
            self._api_version = 2
            self._engine = PaddleOCR(
                lang=lang,
                use_angle_cls=True,
                use_gpu=False,
                show_log=False,
                cpu_threads=cpu_threads,
                enable_mkldnn=True,
            )

    def recognize(self, image: Any) -> list[OCRLine]:
        with self._lock:
            if self._api_version == 3 and hasattr(self._engine, "predict"):
                return parse_paddle_v3_results(list(self._engine.predict(image)))
            return parse_paddle_v2_results(self._engine.ocr(image, cls=True))


def _pdf_first_page(file_bytes: bytes, poppler_path: str | None) -> Image.Image:
    try:
        from pdf2image import convert_from_bytes

        pages = convert_from_bytes(
            file_bytes,
            dpi=220,
            first_page=1,
            last_page=1,
            fmt="png",
            thread_count=1,
            poppler_path=poppler_path,
        )
        if not pages:
            raise ValueError("PDF sem páginas renderizáveis.")
        return pages[0].convert("RGB")
    except Exception:
        import pymupdf

        with pymupdf.open(stream=file_bytes, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                raise ValueError("PDF sem páginas.")
            pixmap = pdf.load_page(0).get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
            return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def preprocess_document(
    file_bytes: bytes,
    mime_type: str,
    *,
    poppler_path: str | None = None,
) -> Any:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV e NumPy são obrigatórios para o OCR local.") from exc

    if mime_type == "application/pdf":
        image = _pdf_first_page(file_bytes, poppler_path)
    else:
        with Image.open(io.BytesIO(file_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((2600, 2600))
    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, 8, 7, 21)
    contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    binary = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


class LocalOCRPipeline:
    def __init__(
        self,
        *,
        backend: OCRBackend | None = None,
        lang: str = "pt",
        cpu_threads: int = 4,
        poppler_path: str | None = None,
    ) -> None:
        self.backend = backend or PaddleOCRBackend(lang=lang, cpu_threads=cpu_threads)
        self.model_version = self.backend.model_version
        self.poppler_path = poppler_path

    def extract(
        self,
        *,
        document_id: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> LocalPipelineResult:
        image = preprocess_document(file_bytes, mime_type, poppler_path=self.poppler_path)
        lines = self.backend.recognize(image)
        document_type = classify_document(lines)
        fields = extract_fields(lines, document_type)
        confidence = calculate_confidence(lines, document_type, fields)
        if fields["nome"]:
            suggested = f"{document_type}_{safe_filename_component(fields['nome'])}"
        else:
            suffix = hashlib.sha256(document_id.encode()).hexdigest()[:8].upper()
            suggested = f"{document_type}_DOCUMENTO_{suffix}"
        return LocalPipelineResult(document_type, fields, confidence, suggested)
