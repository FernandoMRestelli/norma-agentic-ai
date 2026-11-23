# Imágenes de pedidos ficticios

Fotos y capturas **inventadas** para comparar modelos de visión con `python -m internal.comparar_vision`. Están en
`tests/` porque el hook de pre-commit solo acepta multimedia acá.

Las genera `python tests/fixtures/imagenes_pedidos/generar.py`: lista impresa, lista a mano, abreviaturas, captura de
otro chat, foto borrosa y una imagen sin texto. Usa fuentes de macOS para imitar letra a mano.

En `casos.json`, cada caso dice si la imagen es `legible` y qué `renglones` hay que leer: la `cantidad` y las
`palabras` que tienen que aparecer en el renglón (sin importar tildes ni mayúsculas). Un modelo acierta la imagen si
lee todos los renglones y no inventa otros.
