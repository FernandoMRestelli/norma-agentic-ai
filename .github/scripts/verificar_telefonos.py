#!/usr/bin/env python3
"""Hook de seguridad: detecta números de teléfono en archivos de datos y código.

Revisa:
- Archivos *.csv, *.json, *.txt, *.md, *.py, *.yaml, *.yml
- Números de teléfono argentinos con y sin separadores (+54 9 ..., wa_id 549..., 11..., 351...)
- Soporta UTF-8 y UTF-16 (exports de Excel); ante fallos de lectura frena el commit por seguridad.
- NUNCA imprime los números ni datos sensibles encontrados (salida redactada).
"""

import re
import sys
from pathlib import Path

# Extensiones a inspeccionar
EXTENSIONES_TEXTO = {".csv", ".json", ".txt", ".md", ".py", ".yaml", ".yml"}

# Patrones de teléfonos argentinos
PATRONES_TELEFONO = [
    # 13 dígitos: 549 + 10 dígitos (wa_id / WhatsApp from)
    re.compile(r"\b549\d{10}\b"),
    # 12 dígitos: 54 + 10 dígitos
    re.compile(r"\b54\d{10}\b"),
    # Internacional con separadores (+54 o 54, opcional 9)
    re.compile(
        r"(?:\+54|0054|\b54)[\s\-]?(?:9[\s\-]?)?(?:\(?0?[1-9]\d{1,3}\)?)?[\s\-]?(?:15[\s\-]?)?\d{2,4}[\s\-]?\d{4}\b"
    ),
    # 10 dígitos continuos nacionales (área 11, 2x o 3x + resto = 10 dígitos, ej: 1112345678, 3511234567)
    re.compile(r"\b(?:11|[23]\d)\d{8}\b"),
    # Con prefijo 15 (11 15 1234-5678, 15-1234-5678, 1512345678, etc.)
    re.compile(r"\b(?:\(?0?[1-9]\d{1,3}\)?[\s\-])?15[\s\-]?\d{3,4}[\s\-]?\d{4}\b"),
    re.compile(r"\b15\d{8}\b"),
    # Código de área con separador y número local (ej. 351 4123456, 11 1234-5678, (011) 1234-5678)
    re.compile(r"\b(?:0?11|\(0?11\))[\s\-]\d{4}[\s\-]?\d{4}\b"),
    re.compile(r"\b\((?:0?[1-9]\d{1,3})\)[\s\-]?\d{3,4}[\s\-]?\d{4}\b"),
    re.compile(r"\b(?:0?[1-9]\d{1,3})[\s\-](?:15[\s\-])?\d{3,4}[\s\-]?\d{4}\b"),
]

# Lista explícita de excepciones permitidas (ej. datos sintéticos de prueba documentados).
# Un falso positivo se resuelve agregándolo acá, NUNCA usando --no-verify.
NUMEROS_PERMITIDOS = set()


def debe_inspeccionar(ruta: Path) -> bool:
    if ruta.suffix.lower() not in EXTENSIONES_TEXTO:
        return False
    # Evitar auto-inspección de este script de verificación
    if ruta.name == "verificar_telefonos.py" or ruta.name == "test_seguridad_hooks.py":
        return False
    return True


def leer_contenido(ruta: Path) -> str:
    """Lee el archivo intentando UTF-8 primero y luego UTF-16 (exports de Excel)."""
    try:
        return ruta.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return ruta.read_text(encoding="utf-16")
        except Exception as e:
            raise ValueError(f"No se pudo decodificar como UTF-8 ni UTF-16: {e}") from e
    except Exception as e:
        raise ValueError(f"Error al leer el archivo: {e}") from e


def buscar_telefonos_en_archivo(ruta: Path) -> list[int]:
    lineas_con_telefono = []
    contenido = leer_contenido(ruta)

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
    errores_lectura = []

    for arch in archivos:
        ruta = Path(arch)
        if debe_inspeccionar(ruta) and ruta.is_file():
            try:
                lineas = buscar_telefonos_en_archivo(ruta)
                if lineas:
                    hallazgos.append((arch, lineas))
            except ValueError as e:
                errores_lectura.append((arch, str(e)))

    hubo_problemas = False

    if errores_lectura:
        hubo_problemas = True
        print("\n" + "=" * 70, file=sys.stderr)
        print("❌ [HOOK DE SEGURIDAD] Archivos ilegibles o con codificación incompatible:", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for ruta, error_msg in errores_lectura:
            print(f"  • {ruta}: {error_msg}", file=sys.stderr)
        print("\n💡 QUÉ HACER:", file=sys.stderr)
        print("  1. Guardá el archivo en codificación UTF-8 o UTF-16 válida.", file=sys.stderr)
        print("  2. Si es un archivo binario o no texto, agregalo a '.gitignore'.", file=sys.stderr)
        print("=" * 70, file=sys.stderr)

    if hallazgos:
        hubo_problemas = True
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

    return 1 if hubo_problemas else 0


if __name__ == "__main__":
    sys.exit(main())
