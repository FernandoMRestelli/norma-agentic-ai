#!/usr/bin/env python3
"""Hook de seguridad: bloquea archivos prohibidos y multimedia fuera de tests/.

Frena:
- *.opus, *.m4a, *.vcf, _chat.txt, *.zip
- .env* (salvo .env.example)
- credentials/** y *service-account*.json
- Imágenes y audios fuera de tests/ (ej: export de WhatsApp)
"""

import fnmatch
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".wav", ".ogg", ".aac", ".flac", ".wma"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tiff"}

# Lista explícita de archivos permitidos (falsos positivos documentados).
# Un falso positivo se resuelve agregándolo a esta lista, NUNCA usando --no-verify por costumbre.
ARCHIVOS_PERMITIDOS = {
    "docs/Flujo completo de Norma Agentic AI.png",
}


def es_archivo_prohibido(ruta: Path) -> tuple[bool, str]:
    ruta_posix = ruta.as_posix()
    nombre = ruta.name
    partes = ruta.parts

    # Si está en la lista de permitidos, pasa
    if ruta_posix in ARCHIVOS_PERMITIDOS or nombre in ARCHIVOS_PERMITIDOS:
        return False, ""

    # .env* salvo .env.example
    if nombre.startswith(".env") and nombre != ".env.example":
        return True, "Variables de entorno (.env*). Solo se permite versionar '.env.example'."

    # credentials/**
    if "credentials" in partes:
        return True, "Archivos dentro de 'credentials/'. Las credenciales no se versionan en el repositorio."

    # *service-account*.json
    if fnmatch.fnmatch(nombre.lower(), "*service-account*.json"):
        return True, "Claves de cuenta de servicio (*service-account*.json)."

    # _chat.txt (export de WhatsApp)
    if fnmatch.fnmatch(nombre, "*_chat.txt*") or nombre == "_chat.txt":
        return True, "Export de chat de WhatsApp (_chat.txt) con datos personales."

    # *.vcf (contactos)
    if ruta.suffix.lower() == ".vcf":
        return True, "Archivo de contactos vCard (*.vcf)."

    # *.zip (archivos comprimidos)
    if ruta.suffix.lower() == ".zip":
        return True, "Archivo comprimido (*.zip). Puede contener volcados de datos o multimedia no revisada."

    # Imágenes y audios solo dentro de tests/
    es_audio = ruta.suffix.lower() in AUDIO_EXTENSIONS
    es_imagen = ruta.suffix.lower() in IMAGE_EXTENSIONS

    if es_audio or es_imagen:
        esta_en_tests = "tests" in partes
        if not esta_en_tests:
            tipo = "Audio" if es_audio else "Imagen"
            return (
                True,
                f"{tipo} ({ruta.suffix}) fuera de 'tests/'. La multimedia solo está permitida dentro de tests/.",
            )

    return False, ""


def main() -> int:
    archivos = sys.argv[1:]
    if not archivos:
        return 0

    bloqueados = []
    for arch in archivos:
        prohibido, motivo = es_archivo_prohibido(Path(arch))
        if prohibido:
            bloqueados.append((arch, motivo))

    if bloqueados:
        print("\n" + "=" * 70, file=sys.stderr)
        print("❌ [HOOK DE SEGURIDAD] Archivos prohibidos detectados en el commit:", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for ruta, motivo in bloqueados:
            print(f"  • {ruta}", file=sys.stderr)
            print(f"    Razón: {motivo}", file=sys.stderr)
        print("\n💡 QUÉ HACER:", file=sys.stderr)
        print("  1. Quitá estos archivos del commit con 'git reset HEAD <archivo>'.", file=sys.stderr)
        print("  2. Asegurate de que los secretos o datos personales estén en '.gitignore'.", file=sys.stderr)
        print("  3. Si es un falso positivo legítimo (ej. un diagrama en docs/), agregalo a", file=sys.stderr)
        print("     ARCHIVOS_PERMITIDOS en '.github/scripts/verificar_archivos_prohibidos.py'.", file=sys.stderr)
        print("  ⚠️  NUNCA uses '--no-verify' para saltear este control por costumbre.", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
