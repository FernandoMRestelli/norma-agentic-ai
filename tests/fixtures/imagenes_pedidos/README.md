# Imágenes de pedidos ficticios

Fotos y capturas **inventadas** para comparar modelos de visión con `python -m internal.comparar_vision`. Están en
`tests/` porque el hook de pre-commit solo acepta multimedia acá.

Las genera `python tests/fixtures/imagenes_pedidos/generar.py`: lista impresa, lista a mano, abreviaturas, captura de
otro chat, foto borrosa y una imagen sin texto. Usa fuentes de macOS para imitar letra a mano.

En `casos.json`, cada caso dice si la imagen es `legible` y qué `renglones` hay que leer: la `cantidad` y las
`palabras` que tienen que aparecer en el renglón (sin importar tildes ni mayúsculas). Un modelo acierta la imagen si
lee todos los renglones y no inventa otros. Un renglón con `cantidad: null` tiene que leerse sin cantidad. Si el caso
trae `tachados`, cada uno son las `palabras` de algo tachado en la hoja: tiene que aparecer en `tachados` de la
lectura y nunca en un renglón.

`lista-con-tachados.jpg` es una hoja inventada escrita a mano, con productos del catálogo: un recuadro tachado con
una cruz, el encabezado con nombre y fecha, la cantidad al final de cada renglón y un «1» que parece «7». Está
recortada y en blanco y negro, como la foto real, para que no se vea el texto impreso del dorso.

`foto-real-manuscrita.jpg` es la única foto real: una lista escrita a mano con productos del catálogo, sacada con el
celular y mandada al bot de Telegram en la prueba de la PR. Está recortada y en blanco y negro para que no se vea el
texto impreso del dorso de la hoja. Es el caso difícil: el «1» con rayita parece un «7» y la marca va en cursiva.
Qwen3-VL 2B lee bien los dos primeros renglones y el tercero como «7 mascare curk alfa part».
