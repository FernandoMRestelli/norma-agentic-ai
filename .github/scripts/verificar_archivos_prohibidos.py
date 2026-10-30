#!/usr/bin/env python3
"""Hook de seguridad: bloquea archivos prohibidos y multimedia fuera de tests/.

Frena:
- *.opus, *.m4a, *.vcf, *.zip
- Exports de WhatsApp (iOS: *_chat.txt; Android: *WhatsApp*Chat*.txt, *Chat*WhatsApp*.txt)
- .env* (salvo .env.example)
- credentials/** y *service[-_]account*.json (sin importar mayúsculas/minúsculas)
- Multimedia (imágenes, audios y videos, incluyendo .heic y .mp4) fuera de tests/
"""

import fnmatch
import re
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".wav", ".ogg", ".aac", ".flac", ".wma", ".amr", ".3ga"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".3gp", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tiff", ".heic", ".heif"}
MULTIMEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

# Lista explícita de archivos permitidos (falsos positivos documentados).
# Un falso positivo se resuelve agregándolo a esta lista, NUNCA usando --no-verify por costumbre.
ARCHIVOS_PERMITIDOS = {
    "docs/flujo completo de norma agentic ai.png",
}


def es_archivo_prohibido(ruta: Path) -> tuple[bool, str]:
    ruta_posix = ruta.as_posix().lower()
    nombre = ruta.name.lower()
    partes = [p.lower() for p in ruta.parts]

    # Si está en la lista de permitidos, pasa
    if ruta_posix in ARCHIVOS_PERMITIDOS or nombre in ARCHIVOS_PERMITIDOS:
        return False, ""

    # .env* salvo .env.example
    if nombre.startswith(".env") and nombre != ".env.example":
        return True, "Variables de entorno (.env*). Solo se permite versionar '.env.example'."

    # credentials/** (case-insensitive)
    if "credentials" in partes:
        return True, "Archivos dentro de 'credentials/'. Las credenciales no se versionan en el repositorio."

    # *service[-_]account*.json (case-insensitive, acepta guión y guión bajo)
    if re.search(r"service[-_]account.*\.json$", nombre):
        return True, "Claves de cuenta de servicio (*service-account*.json o *service_account*.json)."

    # Exports de chat de WhatsApp (iOS y Android)
    if (
        fnmatch.fnmatch(nombre, "*_chat*.txt")
        or fnmatch.fnmatch(nombre, "*whatsapp*chat*.txt")
        or fnmatch.fnmatch(nombre, "*chat*whatsapp*.txt")
    ):
        return True, "Export de chat de WhatsApp (iOS/Android) con posibles datos personales."

    # *.vcf (contactos)
    if ruta.suffix.lower() == ".vcf":
        return True, "Archivo de contactos vCard (*.vcf)."

    # *.zip (archivos comprimidos)
    if ruta.suffix.lower() == ".zip":
        return True, "Archivo comprimido (*.zip). Puede contener volcados de datos o multimedia no revisada."

    # Multimedia (audios, videos, imágenes) solo dentro de tests/
    suffix = ruta.suffix.lower()
    if suffix in MULTIMEDIA_EXTENSIONS:
        esta_en_tests = "tests" in partes
        if not esta_en_tests:
            if suffix in AUDIO_EXTENSIONS:
                tipo = "Audio"
            elif suffix in VIDEO_EXTENSIONS:
                tipo = "Video"
            else:
                tipo = "Imagen"
            return (
                True,
                f"{tipo} ({suffix}) fuera de 'tests/'. La multimedia solo está permitida dentro de tests/.",
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
