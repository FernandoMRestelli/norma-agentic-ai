"""Carga las skills y sus BR al arrancar. Es el único lugar que lee esos archivos (spec del agente §4.5).

Las tools consultan un índice en memoria: nunca arman una ruta con lo que manda el LLM.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILE = "skill.md"
BR_DIR = "BR"
DATOS_FILE = "datos_negocio.md"  # opcional: datos del negocio que la skill le puede dar al cliente (RF-57)

# Un dato es un texto corto o null, si el negocio todavía no lo confirmó
_DATOS = TypeAdapter(dict[str, str | None])


class SkillError(ValueError):
    """Una skill está mal armada. La aplicación no arranca hasta corregirla."""


class SkillHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1)
    funcion: str = Field(min_length=1)  # qué logra y cómo termina: el agente elige con esto y `cuando`
    cuando: str = Field(min_length=1)
    tools: list[str] = Field(min_length=1)
    requiere_propuesta: bool = False  # sin propuesta vigente, el índice no la muestra


class Skill(SkillHeader):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrucciones: str
    br: dict[str, str]
    datos_negocio: dict[str, str | None] | None = None


def _split_header(path: Path) -> tuple[object, str]:
    """Separa el encabezado YAML entre --- del texto de abajo."""
    name = f"{path.parent.name}/{path.name}"
    parts = path.read_text(encoding="utf-8").split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise SkillError(f"{name} tiene que empezar con un encabezado YAML entre ---")
    try:
        return yaml.safe_load(parts[1]), parts[2].strip()
    except yaml.YAMLError as exc:
        raise SkillError(f"YAML inválido en {name}: {exc}") from exc


def _parse_datos(path: Path) -> dict[str, str | None] | None:
    if not path.is_file():
        return None
    header, _ = _split_header(path)
    try:
        return _DATOS.validate_python(header)
    except ValidationError as exc:
        raise SkillError(f"Datos inválidos en {path.parent.name}/{path.name}: cada dato es un texto o null") from exc


def _parse_skill(folder: Path) -> Skill:
    path = folder / SKILL_FILE
    if not path.is_file():
        raise SkillError(f"La skill {folder.name} no tiene {SKILL_FILE}")
    data, instrucciones = _split_header(path)
    try:
        header = SkillHeader.model_validate(data)
    except ValueError as exc:
        raise SkillError(f"Encabezado inválido en {folder.name}/{SKILL_FILE}: {exc}") from exc
    if header.nombre != folder.name:
        raise SkillError(f"La skill de la carpeta {folder.name} se llama {header.nombre}")
    br = {file.stem: file.read_text(encoding="utf-8") for file in sorted((folder / BR_DIR).glob("*.md"))}
    datos = _parse_datos(folder / DATOS_FILE)
    return Skill(**header.model_dump(), instrucciones=instrucciones, br=br, datos_negocio=datos)


def load_skills(base: Path = SKILLS_DIR) -> dict[str, Skill]:
    skills = {}
    for folder in sorted(base.iterdir()):
        if folder.is_dir() and not folder.name.startswith((".", "_")):
            skills[folder.name] = _parse_skill(folder)
    return skills


@lru_cache
def get_skills() -> dict[str, Skill]:
    return load_skills()
