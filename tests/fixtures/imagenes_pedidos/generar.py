"""Genera las imágenes ficticias de pedidos que usa `python -m internal.comparar_vision`.

    python tests/fixtures/imagenes_pedidos/generar.py

Los PNG ya están en el repo; solo hace falta correrlo para cambiarlos o sumar casos (y actualizar casos.json).
Usa fuentes de macOS para imitar letra a mano; en otro sistema cae en la fuente por defecto de Pillow.
"""

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CARPETA = Path(__file__).parent
FUENTES = Path("/System/Library/Fonts")


def fuente(nombre: str, tamaño: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for ruta in (FUENTES / nombre, FUENTES / "Supplemental" / nombre):
        if ruta.exists():
            return ImageFont.truetype(str(ruta), tamaño)
    return ImageFont.load_default(tamaño)


def hoja_cuaderno(renglones: list[str], letra: str, tamaño: int, semilla: int) -> Image.Image:
    rnd = random.Random(semilla)
    img = Image.new("RGB", (900, 120 + 70 * len(renglones)), (250, 248, 238))
    dibujo = ImageDraw.Draw(img)
    for y in range(90, img.height, 70):
        dibujo.line([(0, y + 8), (img.width, y + 8)], fill=(170, 200, 230), width=2)
    dibujo.line([(70, 0), (70, img.height)], fill=(230, 150, 150), width=2)
    f = fuente(letra, tamaño)
    for i, renglon in enumerate(renglones):
        x = 95 + rnd.randint(-6, 10)
        dibujo.text((x, 40 + 70 * i), renglon, font=f, fill=(25, 35, 110))
    return img.rotate(rnd.uniform(-3, 3), expand=True, fillcolor=(60, 60, 60))


def lista_impresa() -> Image.Image:
    renglones = ["PEDIDO PERFUMERÍA LA ESQUINA", "", "5 x Shampoo Pantene 400 ml", "3 x Tintura Koleston 6.1",
                 "2 x Oxidante Koleston 20 vol", "", "Gracias!"]
    img = Image.new("RGB", (820, 80 + 52 * len(renglones)), "white")
    dibujo = ImageDraw.Draw(img)
    f = fuente("Helvetica.ttc", 34)
    for i, renglon in enumerate(renglones):
        dibujo.text((50, 40 + 52 * i), renglon, font=f, fill="black")
    return img


def lista_manuscrita() -> Image.Image:
    return hoja_cuaderno(
        ["Pedido martes", "media docena jabon dove", "2 sedal ceramidas 340", "1 crema enjuague sedal",
         "4 pasta colgate"],
        "Bradley Hand Bold.ttf", 44, semilla=1,
    )


def lista_abreviada() -> Image.Image:
    return hoja_cuaderno(
        ["- 3 sh. elvive", "- 1 acond. pantene 400", "- 10 desod. rexona", "- 2 cepillos colgate"],
        "Noteworthy.ttc", 46, semilla=2,
    )


def captura_chat() -> Image.Image:
    img = Image.new("RGB", (720, 760), (236, 229, 221))
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle([0, 0, 720, 90], fill=(0, 128, 105))
    dibujo.text((30, 28), "Distribuidora (Ventas)", font=fuente("Helvetica.ttc", 30), fill="white")
    f = fuente("Helvetica.ttc", 28)
    burbujas = [
        ("Hola! te paso el pedido de la semana", 120),
        ("6 shampoo pantene de 200", 210),
        ("2 algodon estrella", 300),
        ("y una tintura koleston 6.0", 390),
        ("lo paso a buscar el jueves", 480),
    ]
    for texto, y in burbujas:
        ancho = int(dibujo.textlength(texto, font=f)) + 40
        dibujo.rounded_rectangle([700 - ancho, y, 700, y + 60], radius=14, fill=(220, 248, 198))
        dibujo.text((720 - ancho, y + 14), texto, font=f, fill=(20, 20, 20))
        dibujo.text((640, y + 64), "10:4" + str(y % 10), font=fuente("Helvetica.ttc", 16), fill=(120, 120, 120))
    return img


def foto_borrosa() -> Image.Image:
    img = hoja_cuaderno(["8 jabones dove", "3 pantene 400 shampoo", "2 koleston 6.1"],
                        "Chalkboard.ttc", 44, semilla=3)
    img = img.resize((img.width // 2, img.height // 2)).filter(ImageFilter.GaussianBlur(1.6))
    oscura = Image.new("RGB", img.size, (90, 80, 60))
    return Image.blend(img, oscura, 0.35)


def sin_texto() -> Image.Image:
    rnd = random.Random(4)
    img = Image.new("RGB", (640, 480), (200, 215, 230))
    dibujo = ImageDraw.Draw(img)
    for _ in range(12):
        x, y, r = rnd.randint(0, 640), rnd.randint(0, 480), rnd.randint(20, 90)
        color = tuple(rnd.randint(60, 230) for _ in range(3))
        dibujo.ellipse([x - r, y - r, x + r, y + r], fill=color)
    return img


CASOS = {
    "lista-impresa": lista_impresa,
    "lista-manuscrita": lista_manuscrita,
    "lista-abreviada": lista_abreviada,
    "captura-chat": captura_chat,
    "foto-borrosa": foto_borrosa,
    "sin-texto": sin_texto,
}

if __name__ == "__main__":
    for nombre, crear in CASOS.items():
        crear().save(CARPETA / f"{nombre}.png", optimize=True)
        print(f"✅ {nombre}.png")
