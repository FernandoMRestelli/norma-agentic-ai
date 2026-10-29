#!/usr/bin/env python3
"""Hook de seguridad: detecta números de teléfono en archivos de datos.

Revisa:
- Archivos *.csv, *.json, *.txt, *.md bajo data/, tests/ y datos_ejemplo/
- Números de teléfono argentinos (+54 9 ..., 11 ....-...., etc.)
- NUNCA imprime los números ni datos sensibles encontrados (salida redactada).
"""

import re
import sys
from pathlib import Path

# Extensiones de datos a inspeccionar
EXTENSIONES_DATOS = {".csv", ".json", ".txt", ".md"}

# Carpetas donde se inspeccionan archivos de datos
CARPETAS_OBJETIVO = {"data", "tests", "datos_ejemplo"}

# Patrones de teléfonos argentinos
PATRONES_TELEFONO = [
    # Internacional con +54 o 0054 (ej: +54 9 11 1234-5678, +54 11 12345678, etc.)
    re.compile(r"(?:\+54|0054)[\s\-]?(?:9[\s\-]?)?(?:\(?0?[1-9]\d{1,3}\)?)?[\s\-]?(?:15[\s\-]?)?\d{2,4}[\s\-]?\d{4}\b"),
    # Con prefijo celular 15 (ej: 11 15 1234-5678, (011) 15-1234-5678, 15-1234-5678)
    re.compile(r"\b(?:\(?0?[1-9]\d{1,3}\)?[\s\-])?15[\s\-]?\d{3,4}[\s\-]?\d{4}\b"),
    # Código de área 11 (AMBA) con separador (ej: 11 1234-5678, 011-1234-5678, (11) 1234-5678)
    re.compile(r"\b(?:011|\(011\)|\(11\)|11)[\s\-]\d{4}[\s\-]?\d{4}\b"),
    # Código de área entre paréntesis (ej: (0351) 123-4567)
    re.compile(r"\b\((?:0?[1-9]\d{1,3})\)[\s\-]?\d{3,4}[\s\-]?\d{4}\b"),
]

# Lista explícita de excepciones permitidas (ej. datos sintéticos de prueba documentados).
# Un falso positivo se resuelve agregándolo acá, NUNCA usando --no-verify.
NUMEROS_PERMITIDOS = set()


def debe_inspeccionar(ruta: Path) -> bool:
    if ruta.suffix.lower() not in EXTENSIONES_DATOS:
        return False
    partes = set(ruta.parts)
    return bool(partes & CARPETAS_OBJETIVO)


def buscar_telefonos_en_archivo(ruta: Path) -> list[int]:
    lineas_con_telefono = []
    try:
        contenido = ruta.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    for num_linea, linea in enumerate(contenido.splitlines(), start=1):
        for patron in PATRONES_TELEFONO:
            coincidencias = patron.findall(linea)
            for m in coincidencias:
                valor = m.strip() if isinstance(m, str) else m[0].strip()
                if valor not in NUMEROS_PERMITIDOS:
                    lineas_con_telefono.append(num_linea)
                    break
            if num_linea in lineas_con_telefono:
                break

    return lineas_con_telefono


def main() -> int:
    archivos = sys.argv[1:]
    if not archivos:
        return 0

    hallazgos = []
    for arch in archivos:
        ruta = Path(arch)
        if debe_inspeccionar(ruta) and ruta.is_file():
            lineas = buscar_telefonos_en_archivo(ruta)
            if lineas:
                hallazgos.append((arch, lineas))

    if hallazgos:
        print("\n" + "=" * 70, file=sys.stderr)
        print("❌ [HOOK DE SEGURIDAD] Posible número de teléfono o dato personal detectado:", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for ruta, lineas in hallazgos:
            lineas_str = ", ".join(str(n) for n in lineas)
            # IMPORTANTE: No se imprime el valor del teléfono por seguridad y privacidad
            print(f"  • {ruta} (línea{'s' if len(lineas) > 1 else ''}: {lineas_str})", file=sys.stderr)
            print("    -> Se detectó un patrón de teléfono argentino [VALOR REDACTADO]", file=sys.stderr)

        print("\n💡 QUÉ HACER:", file=sys.stderr)
        print("  1. No subas números reales ni datos personales de clientes.", file=sys.stderr)
        print("  2. Si son datos de prueba, usá identificadores ficticios (ej. cli-001) o", file=sys.stderr)
        print(
            "     agregá el número sintético a NUMEROS_PERMITIDOS en '.github/scripts/verificar_telefonos.py'.",
            file=sys.stderr,
        )
        print("  3. Si es un archivo que no debería estar versionado, agregalo a '.gitignore'.", file=sys.stderr)
        print("  ⚠️  NUNCA uses '--no-verify' para saltear este control por costumbre.", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
