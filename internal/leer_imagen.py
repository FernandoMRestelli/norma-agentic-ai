"""Lee una imagen y muestra el JSON: python -m internal.leer_imagen foto.jpg [--proveedor openai --modelo gpt-6-luna]

Sin --proveedor ni --modelo usa LLM_VISION_PROVIDER y LLM_VISION_MODEL de .env. Solo con imágenes de prueba:
lo que se manda a una API puede quedar guardado del lado del proveedor (RNF-03).
"""

import argparse
import json
import sys
from pathlib import Path

from internal.services.vision_service import PROVEEDORES, leer_imagen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("imagen", type=Path)
    parser.add_argument("--proveedor", choices=PROVEEDORES)
    parser.add_argument("--modelo")
    parser.add_argument("--crudo", action="store_true", help="muestra también la respuesta sin procesar")
    args = parser.parse_args(argv)

    lectura = leer_imagen(args.imagen.read_bytes(), args.proveedor, args.modelo)
    tokens = f", {lectura.tokens_entrada} → {lectura.tokens_salida} tokens" if lectura.tokens_entrada else ""
    print(f"{lectura.proveedor} / {lectura.modelo}: {lectura.segundos:.1f} s{tokens}", file=sys.stderr)
    if args.crudo or lectura.resultado is None:
        print(f"--- respuesta cruda ---\n{lectura.crudo}\n---", file=sys.stderr)
    if lectura.resultado is None:
        print(f"❌ {lectura.error}", file=sys.stderr)
        return 1
    print(json.dumps(lectura.resultado.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
