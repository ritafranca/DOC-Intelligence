from __future__ import annotations

import argparse
import json
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from faker import Faker
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT_DIR / "templates" / "rg_blank.jpg"
DEFAULT_IMAGES_DIR = ROOT_DIR / "tests" / "dataset" / "images"
DEFAULT_GROUND_TRUTH_DIR = ROOT_DIR / "tests" / "dataset" / "ground_truth"


@dataclass(frozen=True, slots=True)
class SyntheticRG:
    nome: str
    nome_mae: str
    nome_pai: str
    cpf: str
    numero_rg: str
    naturalidade: str
    data_nascimento: str
    orgao_emissor: str

    def extracted_data(self) -> dict[str, str | None]:
        return {
            "nome": self.nome,
            "nome_mae": self.nome_mae,
            "nome_pai": self.nome_pai,
            "cpf": self.cpf,
            "numero_rg": self.numero_rg,
            "naturalidade": self.naturalidade,
            "data_nascimento": self.data_nascimento,
            "data_casamento": None,
            "orgao_emissor": self.orgao_emissor,
        }


def _ascii_filename(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "_", ascii_value.upper()).strip("_")


def _format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def generate_rg_data(fake: Faker) -> SyntheticRG:
    """Gera uma identidade inteiramente fictícia usando o provider pt_BR do Faker."""
    return SyntheticRG(
        nome=fake.name().upper(),
        nome_mae=fake.name_female().upper(),
        nome_pai=fake.name_male().upper(),
        cpf=fake.cpf(),
        numero_rg=fake.rg(),
        naturalidade=f"{fake.city()} - {fake.estado_sigla()}".upper(),
        data_nascimento=_format_date(fake.date_of_birth(minimum_age=18, maximum_age=90)),
        orgao_emissor=f"SSP/{fake.estado_sigla()}",
    )


def _single_document_panel(background: Image.Image) -> Image.Image:
    """Recorta o primeiro verso quando o template for uma folha com seis cartões."""
    width, height = background.size
    if width / height > 1.9:
        return background.crop(
            (
                int(width * 0.01),
                int(height * 0.515),
                int(width * 0.328),
                int(height * 0.95),
            )
        )
    return background.copy()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=max(10, size))


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, preferred: int) -> Any:
    for size in range(preferred, 9, -1):
        candidate = _font(size)
        if draw.textlength(text, font=candidate) <= max_width:
            return candidate
    return _font(10)


def _draw_field(
    draw: ImageDraw.ImageDraw,
    *,
    label: str,
    value: str,
    position: tuple[float, float],
    max_width: float,
    canvas_size: tuple[int, int],
) -> None:
    width, height = canvas_size
    x = int(position[0] * width)
    y = int(position[1] * height)
    available = int(max_width * width)
    label_font = _font(max(10, int(height * 0.028)))
    value_font = _fit_font(draw, value, available, max(13, int(height * 0.045)))
    draw.text((x, y), label, fill=(15, 65, 38), font=label_font)
    draw.text((x, y + int(height * 0.034)), value, fill=(8, 20, 14), font=value_font)


def render_rg_image(
    background_path: Path,
    data: SyntheticRG,
    *,
    rng: random.Random,
    apply_distortion: bool = True,
) -> Image.Image:
    """Desenha os dados em um template e simula uma fotografia leve de celular."""
    with Image.open(background_path) as source:
        canvas = _single_document_panel(source.convert("RGB"))

    draw = ImageDraw.Draw(canvas)
    size = canvas.size
    width, height = size
    banner_font = _fit_font(draw, "AMOSTRA FICTÍCIA - SEM VALIDADE", int(width * 0.70), 18)
    banner = "AMOSTRA FICTÍCIA - SEM VALIDADE"
    banner_box = draw.textbbox((0, 0), banner, font=banner_font)
    banner_width = banner_box[2] - banner_box[0]
    draw.rectangle((0, 0, width, int(height * 0.07)), fill=(255, 235, 225))
    draw.text(
        ((width - banner_width) // 2, int(height * 0.012)),
        banner,
        fill=(145, 20, 20),
        font=banner_font,
    )

    _draw_field(
        draw,
        label="REGISTRO GERAL",
        value=data.numero_rg,
        position=(0.055, 0.085),
        max_width=0.34,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="ÓRGÃO EMISSOR",
        value=data.orgao_emissor,
        position=(0.63, 0.085),
        max_width=0.28,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="NOME",
        value=data.nome,
        position=(0.055, 0.205),
        max_width=0.88,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="FILIAÇÃO — MÃE",
        value=data.nome_mae,
        position=(0.055, 0.33),
        max_width=0.88,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="FILIAÇÃO — PAI",
        value=data.nome_pai,
        position=(0.055, 0.455),
        max_width=0.88,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="NATURALIDADE",
        value=data.naturalidade,
        position=(0.055, 0.59),
        max_width=0.52,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="DATA DE NASCIMENTO",
        value=data.data_nascimento,
        position=(0.65, 0.59),
        max_width=0.28,
        canvas_size=size,
    )
    _draw_field(
        draw,
        label="CPF",
        value=data.cpf,
        position=(0.055, 0.75),
        max_width=0.38,
        canvas_size=size,
    )

    if apply_distortion:
        angle = rng.choice((-1, 1)) * rng.uniform(1.0, 5.0)
        canvas = canvas.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        if rng.random() < 0.5:
            canvas = canvas.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.25, 0.75)))
    return canvas


def build_ground_truth(data: SyntheticRG) -> dict[str, Any]:
    return {
        "document_type": "RG",
        "confidence_score": 1.0,
        "suggested_filename": f"RG_{_ascii_filename(data.nome)}",
        "extracted_data": data.extracted_data(),
    }


def generate_dataset(
    *,
    template_path: Path = DEFAULT_TEMPLATE,
    images_dir: Path = DEFAULT_IMAGES_DIR,
    ground_truth_dir: Path = DEFAULT_GROUND_TRUTH_DIR,
    count: int = 10,
    seed: int = 20260902,
    apply_distortion: bool = True,
) -> list[tuple[Path, Path]]:
    if count < 1:
        raise ValueError("A quantidade deve ser maior que zero.")
    if not template_path.is_file():
        raise FileNotFoundError(f"Template de RG não encontrado: {template_path}")

    images_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_dir.mkdir(parents=True, exist_ok=True)
    fake = Faker("pt_BR")
    fake.seed_instance(seed)
    Faker.seed(seed)
    rng = random.Random(seed)
    generated: list[tuple[Path, Path]] = []

    for index in range(1, count + 1):
        data = generate_rg_data(fake)
        stem = f"rg_falso_{index:03d}"
        image_path = images_dir / f"{stem}.jpg"
        truth_path = ground_truth_dir / f"{stem}.json"
        image = render_rg_image(
            template_path,
            data,
            rng=rng,
            apply_distortion=apply_distortion,
        )
        image.save(image_path, format="JPEG", quality=92, optimize=True)
        truth_path.write_text(
            json.dumps(build_ground_truth(data), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        generated.append((image_path, truth_path))

    return generated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera RGs sintéticos e respectivos gabaritos para testes de OCR."
    )
    parser.add_argument("--count", type=int, default=10, help="Quantidade de documentos.")
    parser.add_argument("--seed", type=int, default=20260902, help="Seed reproduzível.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--ground-truth-dir", type=Path, default=DEFAULT_GROUND_TRUTH_DIR)
    parser.add_argument(
        "--no-distortion",
        action="store_true",
        help="Desativa rotação e desfoque para depuração visual.",
    )
    args = parser.parse_args()
    generated = generate_dataset(
        template_path=args.template.resolve(),
        images_dir=args.images_dir.resolve(),
        ground_truth_dir=args.ground_truth_dir.resolve(),
        count=args.count,
        seed=args.seed,
        apply_distortion=not args.no_distortion,
    )
    print(f"Dataset sintético gerado: {len(generated)} documento(s).")
    print(f"Imagens: {args.images_dir.resolve()}")
    print(f"Gabaritos: {args.ground_truth_dir.resolve()}")


if __name__ == "__main__":
    main()
