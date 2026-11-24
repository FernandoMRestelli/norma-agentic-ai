"""Carga las skills y sus BR al arrancar. Es el único lugar que lee esos archivos (spec del agente §4.5).

Las tools consultan un índice en memoria: nunca arman una ruta con lo que manda el LLM.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILE = "skill.md"
BR_DIR = "BR"
DATOS_FILE = "datos_negocio.md"  # opcional: datos del negocio que la skill le puede dar al cliente (RF-57)
# Solo en una skill `sub_loop` (spec del orquestador §4.1): lo que hace su sub-loop y cómo responde el orquestador
# con la nota. `skill.md` sigue siendo lo que lee el grafo de hoy, que usa la skill inline
SUB_LOOP_FILE = "sub_loop.md"
RESPUESTA_FILE = "respuesta.md"
# La respuesta al cliente de una skill `sub_loop`: el sub-loop no la tiene, la usa quien la tiene activa (RF-90)
TOOL_RESPUESTA = "respond_to_customer"

# Un dato es un texto corto o null, si el negocio todavía no lo confirmó
_DATOS = TypeAdapter(dict[str, str | None])
_ENCABEZADO_BR = re.compile(r"# (?P<id>BR-\d+): (?P<titulo>\S.*)")


def _titulo_br(texto: str) -> str:
    return _ENCABEZADO_BR.match(texto)["titulo"].strip()


class SkillError(ValueError):
    """Una skill está mal armada. La aplicación no arranca hasta corregirla."""


class SkillHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1)
    funcion: str = Field(min_length=1)  # qué logra y cómo termina: el agente elige con esto y `cuando`
    cuando: str = Field(min_length=1)
    tools: list[str] = Field(min_length=1)
    requiere_propuesta: bool = False  # sin propuesta vigente, el índice no la muestra
    # inline: se lee con `leer_skill`. sub_loop: con el orquestador, se delega con `delegar_skill` (RF-88)
    modo: Literal["inline", "sub_loop"] = "inline"
    max_iteraciones: int = Field(default=8, ge=1, le=15)  # tope de su sub-loop


class Skill(SkillHeader):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrucciones: str
    br: dict[str, str]
    datos_negocio: dict[str, str | None] | None = None
    sub_loop: str | None = None  # instrucciones de su sub-loop
    respuesta: str | None = None  # cómo responde el orquestador con su nota

    @property
    def titulos_br(self) -> list[dict[str, str]]:
        """Cada BR con el título de su encabezado, para elegir cuál leer sin leerlas todas."""
        return [{"id": id_br, "titulo": _titulo_br(self.br[id_br])} for id_br in sorted(self.br)]

    @property
    def tools_inline(self) -> list[str]:
        """Las tools de quien la tiene activa. Una `sub_loop` suma la respuesta al cliente, que su sub-loop no tiene."""
        if self.modo == "sub_loop" and TOOL_RESPUESTA not in self.tools:
            return [*self.tools, TOOL_RESPUESTA]
        return list(self.tools)


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
    for id_br, texto in br.items():
        if (match := _ENCABEZADO_BR.match(texto)) is None or match["id"] != id_br:
            raise SkillError(f"{folder.name}/{BR_DIR}/{id_br}.md tiene que empezar con «# {id_br}: <título>»")
    datos = _parse_datos(folder / DATOS_FILE)
    extras = {}
    if header.modo == "sub_loop":
        for campo, archivo in (("sub_loop", SUB_LOOP_FILE), ("respuesta", RESPUESTA_FILE)):
            if not (folder / archivo).is_file():
                raise SkillError(f"La skill {folder.name} es sub_loop y no tiene {archivo}")
            extras[campo] = (folder / archivo).read_text(encoding="utf-8").strip()
    return Skill(**header.model_dump(), instrucciones=instrucciones, br=br, datos_negocio=datos, **extras)


def load_skills(base: Path = SKILLS_DIR) -> dict[str, Skill]:
    skills = {}
    for folder in sorted(base.iterdir()):
        if folder.is_dir() and not folder.name.startswith((".", "_")):
            skills[folder.name] = _parse_skill(folder)
    return skills


@lru_cache
def get_skills() -> dict[str, Skill]:
    return load_skills()
