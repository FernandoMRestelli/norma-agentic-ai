"""Carga las skills y sus BR al arrancar. Es el único lugar que lee esos archivos (spec del agente §4.5).

Las tools consultan un índice en memoria: nunca arman una ruta con lo que manda el LLM.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILE = "skill.md"
BR_DIR = "BR"


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


def _parse_skill(folder: Path) -> Skill:
    path = folder / SKILL_FILE
    if not path.is_file():
        raise SkillError(f"La skill {folder.name} no tiene {SKILL_FILE}")
    parts = path.read_text(encoding="utf-8").split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise SkillError(f"{folder.name}/{SKILL_FILE} tiene que empezar con un encabezado YAML entre ---")
    try:
        header = SkillHeader.model_validate(yaml.safe_load(parts[1]))
    except ValueError as exc:  # yaml.YAMLError no hereda de ValueError: se trata abajo
        raise SkillError(f"Encabezado inválido en {folder.name}/{SKILL_FILE}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SkillError(f"YAML inválido en {folder.name}/{SKILL_FILE}: {exc}") from exc
    if header.nombre != folder.name:
        raise SkillError(f"La skill de la carpeta {folder.name} se llama {header.nombre}")
    br = {file.stem: file.read_text(encoding="utf-8") for file in sorted((folder / BR_DIR).glob("*.md"))}
    return Skill(**header.model_dump(), instrucciones=parts[2].strip(), br=br)


def load_skills(base: Path = SKILLS_DIR) -> dict[str, Skill]:
    skills = {}
    for folder in sorted(base.iterdir()):
        if folder.is_dir() and not folder.name.startswith((".", "_")):
            skills[folder.name] = _parse_skill(folder)
    return skills


@lru_cache
def get_skills() -> dict[str, Skill]:
    return load_skills()
