# Benchmark de pedidos escritos a mano

> **Estado: diseño aprobado en general (15 renglones por hoja, sorteados, juntados con un Google Form; tres
> modelos); faltan los detalles de la §8.** Las listas, el Form, el armado de los casos, la corrida y la scoring de
> la §6 ya están implementados (RF-126 a RF-147 en la [spec](spec/benchmark-manuscritos.md)); falta validar la
> scoring con el orden a mano (§6.9). El comportamiento nuevo entra primero a la spec (RF y CA) y después al código,
> como pide `CONTRIBUTING.md`.
>
> **Dependencias:** todo lo que se cita del código ya está en `develop`: D-42, D-50 (grupos `desarrollo`/`reservado`),
> RNF-18, el intervalo de Wilson de `evaluar_agente`, la lectura, `renglones_desde_lectura`, `buscar_renglones` y
> `comparar_vision`. La spec de lectura de imágenes (`docs/spec/lectura-de-imagenes.md`) es un plan local que no se
> versionó: lo que el benchmark necesita de ella está en la [spec del benchmark](spec/benchmark-manuscritos.md).

La idea viene de la clase 9 de IA Agéntica (AI Benchmarks): un leaderboard global no dice qué modelo conviene para
*esta* tarea, así que armamos un benchmark propio, orientado a la tarea, con la forma

```
input::image (foto de un pedido escrito a mano)  →  output::json (el pedido)  +  scoring_function
```

La scoring function es la representación de lo que más le importa al distribuidor: que **cada renglón que sale en el
pedido sea el producto y la cantidad que pidió el cliente**, y que lo que no se entiende se pregunte en vez de
mandarse mal.

---

## 1. Cómo funciona hoy la extracción

El camino de una foto, de punta a punta:

```
Telegram ──► message_service.leer_imagen_del_mensaje
               └─► vision_service.leer_imagen(imagen)            ← modelo de visión (una llamada)
                     ├─ preparar_imagen: gira por EXIF, achica a 1600 px, JPEG/PNG
                     ├─ INSTRUCCIONES (+ marcas del catálogo si VISION_MARCAS)
                     └─ salida estructurada ImagenLeida (JSON Schema)
            ──► graph: renglones_desde_lectura(lectura)          ← determinístico
            ──► precarga: catalog_tools.buscar_renglones(...)    ← búsqueda tolerante, sin LLM
            ──► agente (LLM_PROVIDER): skills tomar_pedido / confirmar_pedido → borrador con SKU y cantidad
```

- **No hay OCR previo.** Un modelo multimodal lee la imagen y devuelve directamente el JSON. El proveedor sale de
  `LLM_VISION_PROVIDER`: `openai` (por ejemplo `gpt-6-luna`), `openrouter`, `gemini` o `huggingface` (Qwen3-VL 2B
  local, sin API key). Los de API usan `with_structured_output(ImagenLeida, method="json_schema")`; el local genera
  texto y `parsear_json` saca el objeto.
- **El prompt** es `INSTRUCCIONES` en `internal/services/vision_service.py`: «solo leer y ordenar la hoja, no
  interpretar contra ningún catálogo». Explica el encabezado, lo tachado, la cantidad adelante/al final/en un
  círculo, las variantes y tonos («7/2» es tono 7 cantidad 2), `sigue_a` y cuándo algo es `duda`. Con
  `VISION_MARCAS=true` se le suman las marcas del catálogo (decisión D-42, abierta: «medir»).
- **La salida** es `ImagenLeida` (`internal/schemas/vision_schema.py`):

  | Campo | Qué es |
  |---|---|
  | `legible`, `tipo`, `texto` | Si se lee, qué es la imagen y todo el texto tal cual |
  | `encabezado` | Cliente, fecha, «hoja 1»: no se pide |
  | `renglones[]` | Un elemento por producto: `texto`, `producto`, `marca`, `presentacion`, `cantidad`, `cantidad_escrita`, `unidad` (`unidades`/`cajas`/`cada_uno`), `variantes[]` (`detalle`, `tono`, `cantidad`, `duda`), `sigue_a`, `duda` |
  | `tachados[]`, `dudas[]` | Lo tachado (no se pide) y las dudas que no son de un renglón |

- **Después** el modelo de visión ya no participa: `renglones_desde_lectura` arma los renglones del estado (uno por
  variante, heredando producto y marca con `sigue_a`) y la precarga los busca en el catálogo con
  `busqueda_service`. Cada renglón queda `unico`, `varias`, `aproximado`, `sin_stock` o `no_encontrado`. El agente
  confirma, pregunta lo dudoso y arma el borrador: **la salida final es una lista de `{sku, cantidad}`** (más la nota
  de la línea con el tono), la misma que puntúa `evaluar_agente` (RF-60).

**Lo que ya existe y reusamos:**

- `python -m internal.comparar_vision` compara modelos sobre `tests/fixtures/imagenes_pedidos/casos.json` (8
  imágenes ficticias, una sola foto real). Puntúa por imagen (boolean `ok`), renglones acertados (palabras clave +
  cantidad), sobrantes y tachados, y mide latencia, tokens y USD con `--precio`. Acepta `--casos` con otro archivo.
- `internal/evaluar_agente.py`: `medir_lineas` (recall/precision por SKU y cantidad), el intervalo de Wilson y la
  separación `desarrollo`/`reservado` (D-50).
- Catálogos: `data_example/catalogo.csv` (15 productos) y `data_example/catalogo_evaluacion.csv` (~394 productos
  ficticios con el formato del real: 12 marcas, mismo producto en 300 ml / 900 ml / 5 L, keratina vs. argan vs.
  color). El catálogo real de `data/catalogo.csv` no se versiona.

**Ojo con el dominio:** es una distribuidora de perfumería y peluquería. Las unidades difíciles no son «1/2 kg» o
«docena de huevos» sino «x900» (presentación, no cantidad), «20 vol», «5 L», «1 k», tonos «7.1», «media docena»,
«x24», «(3)», «c/u» y cajas.

## 2. Qué se puede evaluar por separado

| Nivel | Qué entra → qué sale | Qué mide | Costo por imagen | Herramienta |
|---|---|---|---|---|
| **(a) Lectura** | `image` → `ImagenLeida` | Si el modelo de visión lee bien la letra: producto (palabras clave) y cantidad | 1 llamada de visión | `comparar_vision` (ya existe) |
| **(b) Lectura + búsqueda** | `image` → `[{sku, cantidad}]` sin agente | Lo que importa al negocio, sin el ruido ni el costo del agente | 1 llamada de visión (la búsqueda es local) | runner nuevo: `leer_imagen` → `renglones_desde_lectura` → `buscar_renglones` |
| **(c) Agente completo** | `image` → borrador del agente | El sistema entero: preguntas, confirmaciones, tachados | visión + varias llamadas al LLM del agente | `evaluar_agente` con `lectura=` (**cuesta plata, pedir permiso**) |

### 2.1 Modelos a comparar

Tres modelos de visión, uno por tipo, con el mismo prompt (`INSTRUCCIONES`) y la misma salida (`ImagenLeida`):

| Modelo | Proveedor (`LLM_VISION_PROVIDER`) | Qué representa | Costo |
|---|---|---|---|
| `gpt-6-luna` | `openai` | El que está configurado hoy | Por token |
| GPT Terra (id exacto a confirmar) | `openai` (u `openrouter`) | Otro modelo de frontera, de otra escala | Por token |
| Modelo local, por defecto `Qwen/Qwen3-VL-2B-Instruct` | `huggingface` | Sin API key ni envío de fotos a terceros | USD 0, tarda más: 100 a 140 s por hoja de 15 renglones en una Mac (≈ 16 s con las imágenes cortas de `tests/fixtures/`) |

El nivel (a) de los tres sale de una sola corrida, que ya soporta `comparar_vision`:

```bash
python -m internal.comparar_vision --casos data/benchmark_manuscritos/casos.json \
  --modelo openai:gpt-6-luna --modelo openai:<id-de-terra> --modelo huggingface:Qwen/Qwen3-VL-2B-Instruct \
  --salida data/benchmark_manuscritos/corrida.json
```

Ojo con `--precio`: hoy toma un solo par entrada,salida para todos los modelos de API. Para comparar Luna con Terra
el runner nuevo tiene que aceptar un precio por modelo. Las llamadas a la API cuestan plata: se corre con permiso de
quien paga la clave.

Hay una diferencia que el benchmark tiene que mostrar: los de API usan salida estructurada por JSON Schema y el local
genera texto y `parsear_json` saca el objeto. Un JSON inválido cuenta como imagen sin renglones (S = 0 en esa hoja), y
la tasa de JSON válido se reporta aparte.

**Recomendación:** el nivel **(b)** es el benchmark principal. Es el más barato que responde la pregunta real
(¿qué SKU y cuánto?), compara modelos de visión de forma justa y sirve para optimizar el prompt de visión. El (a) es
el diagnóstico (¿falló la lectura o la búsqueda?) y el (c) se corre pocas veces, para confirmar antes de cambiar el
modelo en producción.

## 3. Ground truth: listas asignadas que los amigos copian

En vez de que cada amigo invente un pedido y después transcribirlo, **a cada uno le toca una lista numerada ya
tipeada** (productos y cantidades del catálogo ficticio), que ve en un Google Form (§5), y la copia a mano «como haría
un pedido real». La respuesta correcta se conoce de antemano y sale de la misma planilla que genera las listas: no hay
que transcribir nada ni hay desacuerdo entre anotadores. Nadie elige productos, solo el número de lista, y es la única respuesta del Form que
importa para el puntaje es el número de lista.

- Cada amigo copia **las mismas palabras y números** de cada renglón. Mayúsculas, letra, espaciado y si usa un
  renglón o dos son suyos.
- La **forma de la cantidad** (adelante, al final, entre paréntesis, «x3», «media docena») viene variada en la lista
  tipeada, así controlamos que aparezcan todas.
- Todas las listas piden **poner el mismo encabezado**, «Pedido para Norma», para medir lo que separa `ImagenLeida`:
  el encabezado no se pide. Ningún renglón está asignado para tachar: si la persona se equivoca, tacha y sigue, como en un pedido
  real, y lo tachado se anota al revisar la foto.
- La foto la saca cada uno **como un cliente**: con su celular, la luz que tenga, en el papel que tenga a mano
  (cuaderno, hoja lisa, papel de almacén), sin escanear ni recortar. Anotamos las condiciones para poder cortar los
  resultados después.

### 3.1 Selección de productos: diseño híbrido

Hay dos opciones y cada una mide otra cosa:

- **Siempre los mismos productos:** aísla el efecto de la letra (mismo texto, distinta mano), pero cubre poco
  vocabulario y se puede sobreajustar el prompt a esos 15 renglones.
- **Cada amigo elige 10 de 100:** cubre mucho vocabulario, pero la elección no es al azar (cada uno elige lo fácil o
  lo conocido) y no hay dos hojas comparables.

**Propuesta: 15 renglones por hoja = 5 de núcleo fijo + 10 variables** (cubre lo de «15 productos» y lo de «10 de
100»):

- **Núcleo fijo (5 renglones):** los escriben todos, en el mismo orden. Permite comparar personas y fotos con el
  mismo contenido.
- **Parte variable (10 renglones):** sorteados con semilla fija desde un **pool de ~100 productos** de
  `catalogo_evaluacion.csv`. **Se asignan al azar, no se eligen**: es reproducible y no tiene sesgo de selección. El
  sorteo es estratificado para que cada hoja tenga al menos un renglón de cada tipo difícil.
- **Listas numeradas:** Google Forms no puede sortear contenido distinto para cada persona, así que el sorteo se hace
  antes: se generan unas 30 listas (`L01`…`L30`), cada una con el núcleo, sus 10 renglones sorteados y el encabezado
  «Pedido para Norma». Cada persona elige una lista en el Form (y otra si escribe una segunda hoja). Puede
  repetirse una lista entre personas: sigue siendo un dato válido, con otra letra.
- **Cada renglón del pool ya trae su cantidad escrita** («4 Belkor sh matizador 900», «media docena masc argan Lumine
  250»): la lista dice exactamente qué copiar, y la forma de la cantidad la controlamos nosotros.

Tipos difíciles que tienen que aparecer (a propósito):

| Dificultad | Ejemplo en la lista | Por qué cuesta |
|---|---|---|
| Nombres casi iguales | «sh keratina Nordika 900» vs. «acond keratina Nordika 900» | Una palabra cambia el SKU |
| Misma línea, otra marca | Kapris / Trevia / Belkor «sh matizador 300» | La marca define el SKU |
| Presentación con «x» | «Nordika sh keratina x900 — 2» | «x900» es medida, «2» es cantidad |
| Números que se confunden | cantidades 1, 7, 11, 17 | El «1» con rayita se lee «7» (pasó con Qwen) |
| Números en el producto | «oxidante 20 vol», «40 vol» | Cantidad vs. parte del nombre |
| Cantidad en palabras | «media docena», «una», «doce» | Hay que convertirla |
| Cantidad entre paréntesis o en círculo | «Silkara protector térmico (7)» | Posición y forma |
| Abreviaturas | sh, acond, masc, trat, ox, decol | No están en el catálogo así |
| Tono de coloración | «Kapris tintura 7.1 x3» | Tono vs. cantidad; el «7/2» es tono 7 cantidad 2 |
| Unidad escrita | «2 cajas», «24 c/u» | `unidad` cambia el pedido |
| Tachado | un renglón tachado con una línea | No se tiene que pedir |

Núcleo fijo propuesto (SKU de `catalogo_evaluacion.csv`):

| # | Renglón tipeado | SKU esperado | Cantidad | Dificultad |
|---|---|---|---|---|
| N1 | `2 sh keratina Nordika x900` | `NOR-SH-KER-900` | 2 | abreviatura + «x» de medida |
| N2 | `Kapris oxidante 20 vol x900 — (1)` | `KAP-OX-20-900` | 1 | número en el nombre, cantidad al final |
| N3 | `Kapris tintura 7.1 x3` | `KAP-CO-60` (tono 7.1) | 3 | tono vs. cantidad |
| N4 | `media docena masc argan Lumine 250` | `LUM-MA-ARG-250` | 6 | cantidad en palabras |
| N5 | `Silkara protector térmico (7)` | `SIL-CU-PRO-250` | 7 | «7» vs. «1», paréntesis |

### 3.2 Tamaño y fotos

Con 10 amigos × 2 hojas × 15 renglones salen **20 imágenes y 300 renglones**. Para dar una idea de la precisión
(intervalo de Wilson al 95 %):

- un acierto por renglón de 90 % sobre 300 renglones queda en **[86 %, 93 %]**: alcanza para comparar modelos;
- un acierto por imagen de 50 % sobre 20 imágenes queda en **[30 %, 70 %]**: por eso el puntaje por imagen entera no
  sirve como métrica principal.

## 4. Formato del dataset

Las fotos son letra de personas reales: **no van al repo** (es público, y el hook de pre-commit solo acepta
multimedia en `tests/`). Viven en `data/` (que no se versiona). Las listas y las respuestas esperadas son ficticias:
esas sí se pueden versionar.

```
data/benchmark_manuscritos/          ← no se versiona
  respuestas.csv                      ← export del Form, sin mail ni nombre (§5)
  revision.json                       ← lo que anota quien revisa cada foto: salteados, tachados, descartes
  imagenes/
    p03-A.jpg                         ← persona 03, hoja A (sin nombres reales en el archivo)
    p03-B.jpg
  casos.json                          ← respuestas esperadas + metadatos de cada foto
  corridas/
    2026-12-02-luna/
      openai_gpt-6-luna/p03-A.json    ← lo que devolvió el modelo para esa foto, con segundos y tokens
      resumen.json                    ← el puntaje de cada modelo
data_example/benchmark_manuscritos/  ← se versiona (todo ficticio)
  pool.csv                            ← los ~100 renglones del pool: texto tipeado, SKU, cantidad, tono, unidad, dificultad
  listas.json                         ← las 30 listas numeradas (L01…L30), generadas con la semilla 116
  crear_form.gs                       ← el Apps Script que crea el Form (§5), generado desde listas.json
```

`casos.json` se arma con un script que cruza `respuestas.csv` (número de lista, condiciones, control) con
`listas.json`: la respuesta esperada de cada foto es la de su lista.

`casos.json` es **compatible con el de `comparar_vision`** (los campos `id`, `archivo`, `legible`, `renglones`,
`tachados`), así el nivel (a) corre hoy mismo con `--casos data/benchmark_manuscritos/casos.json`. Suma lo que hace
falta para (b) y (c) y para cortar resultados:

```json
{
  "id": "p03-A",
  "archivo": "imagenes/p03-A.jpg",
  "persona": "p03",
  "hoja": "A",
  "lista": "L07",
  "grupo": "reservado",
  "condiciones": {"luz": "artificial", "papel": "cuaderno rayado"},
  "sexo": "prefiero no responder",
  "legible": true,
  "encabezado": "Pedido para Norma",
  "renglones": [
    {"n": 1, "origen": "nucleo", "texto": "2 sh keratina Nordika x900",
     "palabras": ["keratina", "nordika", "900"], "cantidad": 2,
     "sku": "NOR-SH-KER-900", "tono": null, "unidad": null, "dificultad": ["abreviatura", "x_medida"]},
    {"n": 3, "origen": "nucleo", "texto": "Kapris tintura 7.1 x3",
     "palabras": ["kapris", "tintura"], "cantidad": 3,
     "sku": "KAP-CO-60", "tono": "7.1", "unidad": null, "dificultad": ["tono"]}
  ],
  "tachados": [["belkor", "rulos"]]
}
```

- `palabras` son las claves que tiene que leer el nivel (a): marca, la palabra que distingue y el número de la
  medida, tal como están escritas en la lista (si la lista dice «sh», no se exige «shampoo»).
- `sku`, `cantidad`, `tono` y `unidad` son la respuesta del nivel (b) y (c).
- `grupo` separa desarrollo de reservado (§7).

## 5. El Google Form para los participantes

Un solo Form para todos, generado con un Google Apps Script a partir de `listas.json` (crear 30 secciones a mano es
lento y fácil de errarle). Se manda **un solo link para todos** y cada persona elige su lista en el desplegable.
El detalle está en la
[spec de las listas y el Form](spec/benchmark-manuscritos.md).

```bash
python -m internal.benchmark_manuscritos listas   # pool.csv → listas.json (30 listas, semilla 116)
python -m internal.benchmark_manuscritos form     # listas.json → crear_form.gs
```

`crear_form.gs` se pega en un proyecto nuevo de [script.google.com](https://script.google.com) y se corre
`crearForm()` una vez, con la cuenta que va a recibir las fotos. Después, en la sección final, se agrega **a mano** la
pregunta «Subir archivo» (solo imágenes, un archivo, obligatoria): ni Apps Script ni la API de Forms pueden crearla.

**Estructura del Form:**

1. **Inicio.** La explicación de abajo, la casilla obligatoria de consentimiento y la pregunta «Elegí un pedido»
   (desplegable «Pedido 01»…«Pedido 30», que son las listas `L01`…`L30`) con «ir a la sección según la respuesta». Conviene activar «Mezclar orden de las
   opciones» para que no gane siempre `L01`.
2. **Una sección por lista.** Solo texto: el encabezado «Pedido para Norma» y los 15 renglones a copiar. Cada una sigue a la
   sección final.
3. **Sección final, común a todas:**
   - la foto (subir archivo, una imagen);
   - control: «¿Escribiste todos los renglones tal cual?» (sí / no) y, si no, «¿Cuáles cambiaste o salteaste?»;
   - condiciones: luz (natural / artificial / poca), y papel (cuaderno rayado / hoja lisa / otro). No se pregunta el
     ángulo: quien manda la foto de un pedido la saca lo mejor que puede;
   - sexo: masculino / femenino / prefiero no responder.

**Al recibir cada respuesta:**

- Se mira la foto contra su lista (15 renglones, menos de un minuto). Un renglón que la persona salteó o cambió no se
  le cuenta al modelo: se corrige en el esperado de esa foto o se descarta la hoja. Lo que la persona tachó se anota
  en `tachados`.
- Si dos respuestas traen la misma lista (dos personas eligieron el mismo pedido), las dos valen: es la misma lista
  con otra letra. Solo se cubre menos vocabulario.
- Para subir archivos el Form obliga a entrar con una cuenta de Google, y la foto queda en el Drive de quien armó el
  Form con el nombre de la persona (`foto - Nombre Apellido.jpg`). Al bajarla se renombra a `pNN-A.jpg`, y del CSV se
  borran el mail y el nombre antes de guardarlo como `respuestas.csv`. De la persona solo queda el sexo que eligió (o «prefiero no responder»), para cortar
  los resultados; `respuestas.csv` vive en `data/` y no se versiona.

**Texto de inicio del Form:**

> ¡Hola! Te pido una mano para un trabajo de la facu: estoy midiendo qué tan bien una IA lee pedidos escritos a mano.
>
> 1. **Elegí abajo el pedido que quieras** y tocá «Siguiente»: vas a ver los renglones que tenés que copiar.
> 2. **Copialo a mano en un papel**, con tu letra de siempre, como si se lo hicieras a tu proveedor.
> 3. Arriba, como encabezado, poné «Pedido para Norma».
> 4. **Si te equivocás al escribir, tachalo con una línea y seguí**, como harías en un pedido de verdad. No hace
>    falta empezar de nuevo.
> 5. **Sacale una foto con el celu** como la mandarías por WhatsApp: sin escanear, sin recortar, con la luz que
>    tengas, y subila al final del Form.
>
> No pongas tu nombre ni datos tuyos en la hoja. La foto se usa solo para este trabajo, no se publica, y se manda a
> la API del modelo que la lee (OpenAI o Google) para medir. Si preferís que no, no pasa nada.

**Ejemplo de la sección de una lista** (ilustrativo: las 30 listas sorteadas están en
`data_example/benchmark_manuscritos/listas.json`):

> **Pedido para Norma**
>
> 2 sh keratina Nordika x900
> Kapris oxidante 20 vol x900 — (1)
> Kapris tintura 7.1 x3
> media docena masc argan Lumine 250
> Silkara protector térmico (7)
> 4 Belkor sh matizador 900
> Fiorela gel fijador x500 — 3
> 1 baño de crema coco Trevia 1k
> 10 serum puntas Orkidea
> Duomo decolorante sachet x24
> 2 acond keratina Nordika 900
> Brisa acond color 900 x 2
> 3 Kapris acond color 300
> 1 Fiorela sh nutrición 5 L
> 5 Lumine sh nutrición 900

Las `condiciones` (luz y papel) salen de la sección final del Form.

## 6. Scoring function

### 6.1 Por qué tiene que estar escrita

No hay forma de decir qué modelo es mejor sin una scoring function. Comparar lo extraído contra la respuesta correcta
ya es una; si queda implícita, cada caso dudoso («¿«Nordika keratina» sin la medida está bien o mal?») se decide a
ojo, y el ranking entre modelos puede cambiar según cómo se decida. Por eso cada caso ambiguo tiene su puntaje en la
§6.5, y la spec los convierte en tests.

La anatomía del benchmark tiene tres partes, y conviene no confundirlas:

```
input::type        foto del pedido escrito a mano
output::type       el JSON de extracción: ImagenLeida y sus renglones (internal/schemas/vision_schema.py)
scoring_function   compara ese JSON con el JSON de la respuesta correcta y devuelve un número
```

`ImagenLeida` es el **output::type**, no la scoring: dice qué forma tiene la salida, no si está bien. La respuesta
correcta de cada foto (`casos.json`, §4) está cargada en los campos que se puntúan, para comparar campo por campo:

| Campo de la respuesta correcta | Con qué se compara de `ImagenLeida` | Criterio | Nivel |
|---|---|---|---|
| `palabras` que no son números (marca, la palabra que distingue) | `texto`, `producto`, `marca`, `presentacion` del renglón | Normalizado (§6.6); las marcas, con una letra de tolerancia | (a): decide el emparejamiento |
| `palabras` con números (la medida: «900», «20» de «20 vol») | Los mismos campos | Normalizado, exacto | (a): variante |
| `cantidad` | `cantidad`, o la suma de las de sus `variantes` | Exacto después de normalizar; si no, cerca o lejos (§6.4) | (a) y (b) |
| `unidad` | `unidad` | Exacto por clase: cajas contra todo lo demás | (a) y (b): cuenta como cantidad |
| `tono` | `variantes[].tono` | Normalizado: coma o punto decimal | (a) y (b): variante |
| `sku` | El SKU al que llega la búsqueda desde lo leído (`renglones_desde_lectura` → `buscar_renglones`) | Exacto, o entre las opciones si la búsqueda quedó en varias | (b): decide el emparejamiento |
| `tachados` | Lo que sobra de la salida | Tiene todas las palabras de un tachado | (a) y (b): guardarraíl |

**Una scoring, dos usos.** La misma función sirve para:

- **Comparar modelos:** el mismo prompt y las mismas fotos, con distinto modelo.
- **Mejorar el sistema:** el mismo modelo, con variantes del prompt hechas a mano o con DSPy/GEPA. Un optimizador
  mejora exactamente lo que la scoring premia, ni más ni menos: por eso los pesos se validan (§6.9) antes de
  optimizar nada.

**Lo que se evalúa es modelo + prompt, no el modelo solo.** Cada resultado se identifica por el modelo y la huella
del prompt de visión (`INSTRUCCIONES`, las marcas si `VISION_MARCAS` está activo y el tamaño de la imagen). Dos
modelos se comparan solo con la misma huella. Si se optimiza el prompt para un modelo, se reporta la matriz completa,
{prompt base, prompt optimizado} × {cada modelo}, para que no gane un modelo solo porque su prompt se trabajó más.

### 6.2 Opciones que se miraron

| Scoring | Cómo | A favor | En contra |
|---|---|---|---|
| **Boolean** por imagen | 1 si todos los renglones salen bien y no sobra ninguno | Es lo que ve el cliente: «salió perfecto» | Con 15 renglones casi siempre da 0; con 20 imágenes el intervalo es enorme; no da señal para optimizar |
| **Por ítem**: precision / recall / F1 | Un renglón es correcto si coinciden producto y cantidad | Separa omitidos (recall) de inventados (precision); ya existe en `evaluar_agente` | Un «7» leído «1» pesa igual que un producto inventado; no premia preguntar |
| **Por campo, con penalizaciones** | Crédito parcial según qué campo salió mal y cuánto cuesta ese error | Da gradiente (útil para DSPy/GEPA); refleja qué error es peor | Hay que elegir los pesos y validarlos (§6.9), y se puede «jugar» con las dudas si no se acota |

Se usa la de **penalizaciones** como métrica principal, la que se optimiza. La boolean y la de precision/recall se
reportan como diagnóstico, junto con los guardarraíles y el filtro de la §6.8, que no se promedian.

### 6.3 Qué se mide: lectura e interpretación

Para saber si un error es de lectura o de entendimiento, la misma foto se puntúa dos veces:

- **(a) Lectura:** ¿el modelo transcribió bien lo escrito? Se compara lo leído con las palabras, la cantidad, la
  unidad y el tono de la lista. No usa el catálogo, salvo para reconocer las marcas.
- **(b) Interpretación:** ¿lo leído lleva al producto correcto? Lo leído pasa por la misma búsqueda que en
  producción y se compara el SKU. Es lo que le importa al negocio.

Un renglón bien en (a) y mal en (b) es un problema de la búsqueda o del catálogo, no del modelo de visión. Uno mal
en (a) que en (b) da bien es un error de lectura que la búsqueda absorbió (por ejemplo, «Nordica» por «Nordika»).

Límite de (a): solo ve las palabras que la lista marca como clave. «sh» contra «acond» de la misma línea no está en
las `palabras` (la lista no exige expandir abreviaturas), así que esa confusión la mide (b), donde son dos SKU.

### 6.4 El puntaje de un renglón

**Emparejar.** Cada esperado se empareja con, como mucho, un renglón leído, y al revés, recorriendo los esperados en
el orden de la hoja:

- (a): el leído tiene todas las palabras del esperado que no son números. Si hay varios, se prefiere el que también
  tiene las de números (la medida).
- (b): el SKU al que llega el leído es el esperado, o está entre las opciones si la búsqueda quedó en `varias` o
  `aproximado`.

Un esperado sin pareja es **omitido**; un leído sin pareja es **sobrante**.

**Una pregunta cuesta 0,2, y un error silencioso cuesta más que una pregunta.** Es la regla de la que salen todos los
pesos: si el modelo duda, el agente le pregunta al cliente; eso molesta, pero el pedido no sale mal. Si el modelo se
equivoca sin dudar, el pedido sale mal y el cliente se entera al recibirlo. Si algún error silencioso valiera más de
0,8, al modelo le convendría equivocarse callado antes que preguntar.

```
s = 0,8                                    si el renglón leído tiene duda (o le falta la cantidad)
s = máx(0,1 ; 1 − Σ penalizaciones)        si no
```

| Error, sin duda | Penalización | s si es el único | Por qué |
|---|---|---|---|
| Variante mal: el tono, o la medida en (a) | 0,3 | 0,7 | Llega otro color u otro tamaño. Es silencioso, pero el producto es el pedido |
| Cantidad cerca: mal, pero menos del doble o la mitad (3 por 2, 6 por 5) | 0,4 | 0,6 | Llega de más o de menos y hay que corregirlo |
| Cantidad lejos: el doble o más, la mitad o menos («1» por «10», «7» por «1»), o cajas por unidades | 0,6 | 0,4 | El error caro: devoluciones, o un cliente sin mercadería. «1» por «10» es un dígito perdido, no un desliz |

- Las penalizaciones se suman, con un piso de 0,1: el producto acertado vale algo aunque lo demás esté mal. Tono mal
  y cantidad cerca dan 0,3; tono mal y cantidad lejos, 0,1.
- La **unidad** se puntúa como cantidad, no como detalle: «2 cajas» leído «2» puede ser 24 contra 2. Las clases son
  «cajas» y «el resto» (`unidades`, `cada_uno` o sin unidad, que cuentan unidades sueltas). Cambiar de clase es
  cantidad lejos.
- Un renglón **sin cantidad** leída cuenta como duda en los dos niveles: el agente la va a preguntar.
- El **producto equivocado** no tiene penalización propia porque no empareja: el esperado queda omitido y lo leído
  sobra. Son dos errores: al cliente le falta lo que pidió y le llega otra cosa.

**Lo que sobra** suma al denominador, como un renglón que no puntúa:

| Sobrante | Suma al denominador | Por qué |
|---|---|---|
| Silencioso: sin duda y, en (b), con la búsqueda en `unico` o `sin_stock` | 1 | Se mandaría algo que el cliente no pidió |
| Dudoso: con duda o, en (b), con la búsqueda en `varias`, `aproximado` o `no_encontrado` | 0,2 | El agente pregunta antes de pedirlo: cuesta una pregunta, como una duda |
| Un renglón tachado, con o sin duda | 1 | Lo tachado no se pide ni se pregunta (RNF-18) |

**Puntaje de una hoja:**

```
S = Σ s(pares) / (esperados + Σ costo de los sobrantes)
```

Una salida sin JSON válido, o que dice ilegible una foto que se lee, da S = 0 con todos los esperados omitidos. Una
foto que quien revisa no puede leer no entra al puntaje: solo se cuenta si el modelo también la marcó ilegible.

### 6.5 Casos ambiguos, resueltos

Renglón escrito «2 sh keratina Nordika x900» (SKU `NOR-SH-KER-900`, cantidad 2). Es el equivalente, en este
catálogo, de «3 Coca 2.25». Lo que vale cada salida del modelo, en los dos niveles:

| # | Salida | (a) Lectura | (b) Interpretación |
|---|---|---|---|
| 1 | «Nordika sh keratina 900», cantidad 2 | 1 | 1 |
| 2 | Variantes de escritura: «NORDIKA», «nórdika», «Nordika-», «sh. keratina» | 1 (§6.6) | 1 |
| 3 | La marca con una letra cambiada: «Nordica» | 1 | 1 si la búsqueda llega al SKU |
| 4 | Producto bien, cantidad 3 | 0,6 | 0,6 |
| 5 | Producto bien, cantidad 20 (o 1, si decía 10) | 0,4 | 0,4 |
| 6 | Producto bien, «2 cajas» | 0,4 | 0,4 |
| 7 | Producto bien, cantidad con duda «¿2 o 7?» | 0,8 | 0,8 |
| 8 | Producto bien, sin cantidad | 0,8 | 0,8 |
| 9 | Otra presentación: «keratina Nordika x300» | 0,7: un número mal leído | 0 y +1 al denominador: es otro SKU |
| 10 | Sin presentación: «keratina Nordika» | 0,7 | 0,8: la búsqueda da las tres medidas y el agente pregunta |
| 11 | Otro producto de la misma línea: «acond keratina Nordika 900» | 1: (a) no lo ve (§6.3) | 0 y +1 al denominador |
| 12 | El renglón no está en la salida | 0 | 0 |
| 13 | Un renglón inventado sin duda, que la búsqueda encuentra | +1 al denominador | +1 |
| 14 | Un renglón inventado con duda, o que la búsqueda no encuentra | +0,2 | +0,2 |
| 15 | Un renglón que la persona tachó | +1, y cuenta como tachado pedido | +1, y cuenta como tachado pedido |

Para un renglón de coloración, «Kapris tintura 7.1 x3», en (a): «7,1» con 3 vale 1; el tono «7.2» con 3 vale 0,7;
«7.1» con «1» vale 0,4 (cantidad lejos), y «7/3» leído como tono 7 y cantidad 3 vale 0,7 (variante mal). En (b)
valen lo mismo si la búsqueda llega a un solo SKU. Con `catalogo_evaluacion.csv`, «Kapris tintura» queda entre la
tintura sola y la que trae oxidante, así que es una duda (0,8) salvo que lo leído diga cuál: eso lo mide la
búsqueda, no la lectura.

### 6.6 Normalización antes de comparar

| Qué | Regla | Ejemplos |
|---|---|---|
| Texto | Minúsculas, sin tildes, los signos como espacios y espacios simples. Una palabra esperada se busca dentro del texto leído, así «mascara» está en «mascaras» | «Máscara», «MASCARA» → `mascara`; «sh.» → `sh` |
| Palabras pegadas | Una palabra esperada también vale si aparece con los espacios y guiones sacados | «CocaCola» = «Coca-Cola» = «coca cola» |
| Marcas | Además, en (a), una letra de tolerancia (cambiada, de más o de menos) contra las marcas del catálogo | «Nordica» → `nordika` |
| Abreviaturas | No se expanden: las `palabras` de la lista no llevan abreviaturas | La lista dice «masc argan Lumine 250» y las palabras son `argan lumine 250` |
| Cantidades | Número; palabras a número; fracciones a decimal | «una» = 1, «media docena» = 6, «doce» = 12, «x3» = «(3)» = «3u» = 3, «1/2» = «0,5» = «½» = 0,5 |
| Tonos | Punto o coma decimal; la barra no es decimal (es tono/cantidad, como en `INSTRUCCIONES`) | «7.1» = «7,1»; «7/2» = tono 7, cantidad 2 |
| Unidad | Dos clases: cajas, y el resto | «cj», «cajas» → cajas; «u», «unid», «c/u» o nada → unidades sueltas |

Las cantidades en palabras y las medidas las convierte el modelo de visión en `ImagenLeida.cantidad`: la scoring
compara números, no vuelve a interpretar el texto. En (b) casi toda la normalización la hace el SKU: si llegó al SKU
correcto, la abreviatura o la tilde ya no importan.

### 6.7 Agregación

- **S del benchmark** (la principal): micro-promedio, la suma de numeradores sobre la suma de denominadores de todas
  las hojas, así cada renglón pesa igual. Con intervalo al 95 % por bootstrap sobre hojas: los renglones de una
  misma hoja no son independientes.
- **S por hoja:** el promedio de los S de cada hoja, y las **5 peores hojas** de cada modelo con su S, para mirarlas.
  Un promedio alto puede esconder un par de hojas desastrosas.
- **Peores renglones:** los 10 renglones del pool con menor s promedio entre las hojas en que aparecieron (al menos
  dos), para saber qué productos o qué forma de escribir fallan.
- **Cortes:** S por tipo de dificultad, origen (núcleo o pool), persona, letra (clara o difícil), foto (nítida o
  borrosa), luz y papel. La letra y la nitidez las anota quien revisa cada foto; la luz y el papel salen del Form.
- **Diagnóstico:** fotos perfectas, precision, recall y F1, errores silenciosos, errores graves, tachados pedidos,
  JSON válido, segundos p50 y p95, y USD por hoja y por cada 1000 renglones.

Todo eso se calcula por modelo y huella del prompt, por nivel, y para desarrollo y reservado por separado.

### 6.8 Errores que no se perdonan: un filtro, no un cero

Poner en cero la hoja entera por un error grave parece estricto, pero con unas 20 hojas deja un número muy ruidoso, y
al optimizador le saca la señal: un prompt que arregla tres renglones de una hoja vetada no sube nada. En cambio:

- **Errores graves**, contados aparte: los sobrantes silenciosos (producto equivocado o inventado, que se mandaría) y
  las cantidades lejos sin duda.
- **Filtro de elegibilidad:** una configuración (modelo + prompt) **no se puede elegir**, tenga el S que tenga, si
  en el reservado, nivel (b), pide algún tachado o tiene errores graves en más del 2 % de los renglones esperados. El
  ranking se arma solo con las elegibles; las demás se muestran marcadas.

Un producto que no existe en el catálogo no necesita veto: en (b) no puede salir en silencio, porque la búsqueda
queda `no_encontrado` y el agente pregunta. Cuesta 0,2, como cualquier sobrante dudoso.

### 6.9 Validar la scoring antes de usarla

Los pesos de la §6.4 son una propuesta razonada, no una verdad. Antes de comparar modelos u optimizar prompts:

1. Se toman de 15 a 20 salidas de **desarrollo** (pares modelo + hoja, al azar con semilla), del nivel (b).
2. Se muestran sin el modelo ni el puntaje, en orden al azar: cada una con su lista y lo que extrajo el modelo.
3. Quien conoce el negocio las ordena de mejor a peor, sin mirar los números.
4. Se compara su orden con el de S: el porcentaje de pares que quedan en el mismo orden, y los pares invertidos.

Si coinciden menos del 80 % de los pares, o se invierte algún par con más de 0,1 de diferencia en S, la scoring está
mal definida: se mira qué tienen esos pares, se ajustan los pesos (y esta sección) y se vuelve a validar. Solo
después se usa para comparar u optimizar.

### 6.10 Ejemplo calculado

Hoja `p03-A` de la §5 (15 renglones esperados), en la que la persona escribió de más «2 Belkor crema de peinar
rulos» y lo tachó, nivel (b). Supongamos que la lectura + búsqueda da:

| Renglones | Qué pasó | s |
|---|---|---|
| N1–N4, Belkor matizador, Fiorela gel, baño Trevia, Duomo, acond Nordika, Brisa, Kapris acond y Lumine (12 renglones) | SKU y cantidad bien, sin duda | 12 × 1 = **12** |
| N5 «Silkara protector térmico (7)» | Leyó «(1)», sin duda: cantidad lejos | 1 − 0,6 = **0,4** |
| «10 serum puntas Orkidea» | Leyó «1» con duda «¿1 o 10?» | **0,8** |
| «1 Fiorela sh nutrición 5 L» | No lo leyó (quedó en el pliegue) | omitido: **0** |
| «2 Belkor crema de peinar rulos» (tachado) | Lo pidió | sobrante tachado: +1 al denominador |

```
S = (12 + 0,4 + 0,8 + 0) / (15 esperados + 1) = 13,2 / 16 = 0,825
```

Lo demás, sobre el mismo resultado:

- **Boolean:** 0 (no salió perfecta).
- **Por ítem:** 12 correctos (SKU y cantidad); 15 renglones en la salida (14 de los esperados + el tachado):
  precision = 12/15 = 0,80, recall = 12/15 = 0,80, F1 = 0,80.
- **Guardarraíles:** errores silenciosos = 2 (la cantidad de N5 y el tachado), errores graves = 2 (los mismos: 13 %
  de los renglones), tachados pedidos = 1. Con esta hoja sola, la configuración no sería elegible.

La ponderada distingue lo que el F1 no ve: el «10» con duda no sale mal porque el agente lo pregunta, y el «7» leído
«1» sí, y caro. Si un cambio de prompt convierte el error de N5 en una duda, S sube a 13,6 / 16 = 0,85 y los errores
graves bajan a 1; el F1 no se mueve. Sigue sin ser elegible mientras pida el tachado.

## 7. Costo, latencia y separación desarrollo / reservado

**Por imagen** se registra: modelo, huella del prompt, segundos, tokens de entrada y salida, USD (`--precio`), si el
JSON salió válido y el error si lo hubo (todo eso ya está en `Lectura`). Por modelo se reporta: S con intervalo, p50 y
p95 de segundos, USD por imagen y USD por cada 1000 renglones. Con eso se arma la **frontera puntaje vs. costo**: por
ejemplo, si Qwen local (USD 0, unos 2 minutos por hoja) queda cerca de un modelo de API, o si `VISION_MARCAS` vale los tokens extra
(cierra D-42).

**Separación por persona, no por imagen:** las dos hojas de una persona van al mismo grupo, así lo reservado mide
letra que el prompt nunca vio, como un cliente nuevo. Optimizar y medir sobre las mismas fotos sube el número sin
garantizar que el sistema mejoró (ley de Goodhart): el prompt aprende esas letras, no a leer.

- `desarrollo` (≈ 40 % de las personas): se mira, se itera `INSTRUCCIONES`, se valida la scoring (§6.9) y se
  optimiza con DSPy/GEPA usando S como métrica.
- `reservado` (≈ 60 %): no se mira ni se ajusta nada con sus casos (misma regla que D-50 en `evaluar_agente`). Sobre
  él se reporta el puntaje final y se aplica el filtro de la §6.8.
- **Los grupos no cambian:** quien ya tiene grupo lo conserva cuando llegan fotos nuevas.
- **El reservado se congela** en la primera medición final. Desde ahí, toda persona o foto nueva va a `desarrollo`,
  también las que se sumen por traer errores reales: sirven para mejorar, pero no pueden cambiar el reservado con el
  que ya se midió.
- Opcional: partir también el pool de 100 productos (40/60), para que lo reservado tenga vocabulario no visto.

El mismo benchmark sirve para comparar un modelo solo, modelo + prompt (con y sin `VISION_MARCAS`, otro
`INSTRUCCIONES`), el workflow determinístico (nivel b) o el agente (nivel c).

## 8. Preguntas abiertas para cerrar el diseño

1. ~~**Renglones por hoja**~~: cerrado, 15 (5 de núcleo + 10 sorteados).
2. **Personas y fotos:** ¿cuántos amigos van a participar y cuántas hojas escribe cada uno (1 o 2)? ¿Una foto por
   hoja, o también una segunda foto «mala» (de costado, con sombra) para medir robustez?
3. ~~**Elegir vs. sortear**~~: cerrado, se sortean. Los 10 variables salen de listas numeradas generadas con
   semilla, y cada persona recibe su número; nadie elige productos.
4. **Catálogo:** ¿usamos el ficticio `catalogo_evaluacion.csv` (versionable, recomendado) o el real (más fiel, pero
   las listas y respuestas no pueden ir al repo)?
5. **Fotos en el repo:** ¿con permiso explícito de alguno, sumamos 2 o 3 fotos a `tests/fixtures/imagenes_pedidos/`,
   o quedan todas en `data/`?
6. **Modelos a comparar:** `gpt-6-luna`, GPT Terra y un modelo local (§2.1). Falta el id exacto de Terra y si el
   local es Qwen3-VL 2B u otro.

## 9. Próximos pasos (después del OK)

1. ~~Spec, pool, listas y Form~~: hecho en [`docs/spec/benchmark-manuscritos.md`](spec/benchmark-manuscritos.md)
   (RF-126 a RF-129) e `internal/benchmark_manuscritos.py`. Los RF y CA del runner van en esa misma spec.
2. ~~Crear el Form con `crear_form.gs`, agregar la pregunta de la foto y mandar el link.~~
3. Juntar las fotos con el Form y armar `casos.json` cruzando `respuestas.csv` con `listas.json`. Hay 6 (§10); la
   revisión foto por foto se saltea y se asume que cada persona copió su lista tal cual.
4. ~~Correr el nivel (a) con `comparar_vision --casos`~~: lo reemplaza `evaluar_manuscritos`, que mide (a) y (b).
5. ~~Runner de los niveles (a) y (b)~~: `python -m internal.evaluar_manuscritos correr` guarda cada salida y
   `puntuar` la puntúa sin llamar a ningún modelo ([spec](spec/benchmark-manuscritos.md#33-corrida), RF-133 a
   RF-147), con la scoring de la §6.
6. Validar la scoring con el orden a mano de la §6.9, sobre desarrollo, antes de comparar modelos.
7. Nivel (c) y optimización con DSPy/GEPA solo con permiso, porque llaman a un LLM pago.
8. Con 15 a 20 salidas de desarrollo, validar la scoring (§6.9) y revisar los dos puntos de la §10.

## 10. Primera prueba (2026-11-26)

Con las primeras 6 fotos del Form, para probar el circuito de punta a punta: 3 personas quedaron en `desarrollo` y 3
en `reservado`, que no se corrió. Las fotos se unieron con su pedido por la hora de subida, y lo leído sigue el orden
de cada lista, así que ninguna quedó cruzada. Sin revisión: se asume que todos copiaron bien.

| Modelo | S (a) lectura | S (b) interpretación | Intervalo de (b) | Errores graves en (b) | Elegible | Segundos por hoja |
|---|---|---|---|---|---|---|
| Qwen3-VL 2B, local | 0,15 | 0,33 | [0,14; 0,70] | 2,2 % | no | ≈ 120 |
| `gpt-6-luna` | 0,49 | 0,56 | [0,32; 0,95] | 2,2 % | no | ≈ 21 |

Con 3 hojas los intervalos se pisan y no alcanza para elegir modelo. Lo que sí mostró:

- **El tope de 1024 tokens del modelo local cortaba el JSON** de una hoja de 15 renglones (unos 3.000 tokens con
  Qwen y hasta 4.800 con Luna). Se subió a 20.000 (RF-148).
- **Una de las tres letras se lee bien y dos no.** En la hoja clara Luna llega a S = 0,95 en (b) y Qwen a 0,70; en
  las otras dos los dos caen por debajo de 0,45. Es la diferencia que el corte por letra tiene que mostrar cuando se
  anote en la revisión.
- **Errores de los modelos que la scoring castigó bien:** el encabezado leído como renglón, la medida tomada como
  cantidad («300» por «4») y un renglón de más, sin duda, que se habría mandado.
- **Dos puntos de la scoring para la validación de la §6.9**, sin cambiar nada todavía:
  - En (a), un renglón mal leído cuenta doble (omitido y sobrante de costo 1), y una letra cambiada en una palabra
    que no es marca («tinture» por «tintura») ya no empareja. Por eso (a) queda muy por debajo de (b) con letra
    difícil. Puede ser lo correcto para diagnosticar la lectura, o puede convenir una tolerancia de una letra en las
    palabras largas.
  - En (b), lo que se lee tan mal que la búsqueda no lo encuentra cuesta 0,2 y no 1, porque el agente pregunta. Con
    letra difícil eso deja los errores graves bajos aunque el S sea malo: el filtro mide que no salga nada mal, y S
    mide cuánto sale bien. Hay que ver si esa división coincide con el orden a mano.

### 10.1 Con `VISION_MARCAS` (D-42)

Las mismas 3 fotos de desarrollo y los mismos modelos, con la lista de marcas de `catalogo_evaluacion.csv` sumada al
prompt. Es el único cambio, y por eso cambia la huella.

| Modelo | S (b) sin marcas | S (b) con marcas | S (a) sin → con | Errores graves en (b) sin → con |
|---|---|---|---|---|
| Qwen3-VL 2B, local | 0,33 | 0,50 | 0,15 → 0,32 | 1 → 1 |
| `gpt-6-luna` | 0,56 | 0,73 | 0,49 → 0,68 | 1 → 3 |

- **Mejoran las 3 hojas en los dos modelos**, y más en las de letra difícil: Luna pasa de 0,32 a 0,66 y de 0,44 a
  0,57 en (b). La lista de marcas ayuda justo donde la marca se leía mal.
- **Con las marcas bien leídas, los errores de número salen sin duda.** Los 3 errores graves de Luna son números mal
  leídos, y uno de ellos lleva a otro producto que la búsqueda encuentra seguro (otro oxidante): antes la marca mal
  leída lo dejaba en duda y ahora sale silencioso. Es el riesgo que la scoring tiene que mostrar, y lo muestra.
- Con 3 hojas no alcanza para cerrar D-42, pero la dirección es clara: vale la pena medirlo con más fotos y, si se
  confirma, prenderlo en el bot. Cuesta 90 tokens de entrada más por foto (≈ 2 %).

