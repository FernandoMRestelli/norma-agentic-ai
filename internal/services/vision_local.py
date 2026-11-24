"""Lectura de imágenes con un modelo liviano de Hugging Face corriendo en la máquina, sin API.

Es para experimentar y comparar contra la API (`python -m internal.comparar_vision`), no para producción: necesita
torch y transformers, que no están en requirements.txt. Instalar con:

    pip install -r requirements-vision-local.txt

La primera vez descarga los pesos del modelo a la caché de Hugging Face (~/.cache/huggingface).
"""

import io
import json
import threading
import time
from functools import lru_cache

from internal.schemas.vision_schema import ImagenLeida

# Qwen3-VL 2B: chico (≈4 GB en bf16), entiende español y sigue instrucciones de formato. Se cambia con
# LLM_VISION_MODEL o --modelo, por ejemplo HuggingFaceTB/SmolVLM2-500M-Video-Instruct (más liviano, peor en español).
MODELO_POR_DEFECTO = "Qwen/Qwen3-VL-2B-Instruct"
MAX_TOKENS_NUEVOS = 1024
REPETITION_PENALTY = 1.1

# El bot puede atender varios chats a la vez y el modelo es uno solo en memoria: de a una imagen por vez
_lock = threading.Lock()


def _prompt() -> str:
    # Sin salida estructurada nativa, el esquema va en el prompt y el JSON se valida al final.
    from internal.services.vision_service import instrucciones

    esquema = json.dumps(ImagenLeida.model_json_schema(), ensure_ascii=False)
    return (
        f"{instrucciones()}\n\nRespondé SOLO con un objeto JSON que cumpla este JSON Schema, sin texto antes ni "
        f"después:\n{esquema}"
    )


def _dispositivo() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():  # Mac con Apple Silicon
        return "mps"
    return "cpu"


@lru_cache(maxsize=2)
def cargar(modelo: str):
    """Carga el modelo y su procesador una sola vez por proceso (tarda varios segundos)."""
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor

    dispositivo = _dispositivo()
    procesador = AutoProcessor.from_pretrained(modelo)
    red = AutoModelForImageTextToText.from_pretrained(
        modelo, dtype=torch.float32 if dispositivo == "cpu" else torch.bfloat16
    ).to(dispositivo)
    red.eval()
    return procesador, red


def generar(imagen: bytes, modelo: str, prompt: str) -> tuple[str, int, int]:
    """Corre el modelo con la imagen y el prompt. Devuelve el texto generado y los tokens de entrada y salida."""
    import torch
    from PIL import Image

    procesador, red = cargar(modelo)
    with Image.open(io.BytesIO(imagen)) as abierta:
        img = abierta.convert("RGB")
    mensajes = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": prompt}]}]
    entradas = procesador.apply_chat_template(
        mensajes, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
    ).to(red.device)
    largo_entrada = entradas["input_ids"].shape[1]
    with torch.inference_mode():
        # Sin la penalización, con una imagen sin texto el modelo se traba repitiendo un carácter hasta el tope
        salida = red.generate(
            **entradas, max_new_tokens=MAX_TOKENS_NUEVOS, do_sample=False, repetition_penalty=REPETITION_PENALTY
        )
    nuevos = salida[:, largo_entrada:]
    texto = procesador.batch_decode(nuevos, skip_special_tokens=True)[0]
    return texto, largo_entrada, nuevos.shape[1]


def leer(imagen: bytes, modelo: str = MODELO_POR_DEFECTO):
    from internal.services.vision_service import Lectura, parsear_json

    inicio = time.perf_counter()
    try:
        with _lock:
            crudo, tokens_entrada, tokens_salida = generar(imagen, modelo, _prompt())
    except ImportError as exc:
        raise RuntimeError(
            f"Falta instalar el modelo local ({exc}): pip install -r requirements-vision-local.txt"
        ) from exc
    segundos = time.perf_counter() - inicio
    try:
        resultado, error = parsear_json(crudo), None
    except ValueError as exc:
        resultado, error = None, str(exc)
    return Lectura("huggingface", modelo, resultado, crudo, segundos, tokens_entrada, tokens_salida, error)
