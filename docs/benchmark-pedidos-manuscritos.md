# Benchmark de pedidos escritos a mano

> **Estado: diseño aprobado en general (15 renglones por hoja, tres modelos); faltan los detalles de la §8.** Todavía
> no hay código del runner. El comportamiento nuevo entra primero a la spec (RF y CA) y después al código, como pide
> `AGENTS.md`.
>
> **Dependencia:** esta rama sale de `develop`. Lo que se cita de `docs/spec/lectura-de-imagenes.md`, D-42, D-50
> (grupos `desarrollo`/`reservado`), RNF-18 y el intervalo de Wilson de `evaluar_agente` llega con
> `feature/mejoras-del-agente`, que todavía no está en `develop`. La lectura, `renglones_desde_lectura`,
> `buscar_renglones` y `comparar_vision` ya están en `develop`.

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
| Modelo local, por defecto `Qwen/Qwen3-VL-2B-Instruct` | `huggingface` | Sin API key ni envío de fotos a terceros | USD 0, tarda más (≈ 16 s por imagen) |

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

En vez de que cada amigo invente un pedido y después transcribirlo, **a cada uno se le manda una lista ya tipeada**
(productos y cantidades del catálogo ficticio) y la copia a mano «como haría un pedido real». La respuesta correcta
se conoce de antemano y sale de la misma planilla que genera las listas: no hay que transcribir nada ni hay
desacuerdo entre anotadores.

- Cada amigo copia **las mismas palabras y números** de cada renglón. Mayúsculas, letra, espaciado y si usa un
  renglón o dos son suyos.
- La **forma de la cantidad** (adelante, al final, entre paréntesis, «x3», «media docena») viene variada en la lista
  tipeada, así controlamos que aparezcan todas.
- Algunas listas piden **tachar un renglón** y **poner un encabezado** (nombre de negocio inventado y fecha), para
  medir lo que separa `ImagenLeida`.
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
  imagenes/
    p03-A.jpg                         ← persona 03, hoja A (sin nombres reales en el archivo)
    p03-B.jpg
  casos.json                          ← respuestas esperadas + metadatos de cada foto
data_example/benchmark_manuscritos/  ← se versiona (todo ficticio)
  pool.csv                            ← los ~100 SKU del pool, con su tipo de dificultad
  listas.json                         ← las listas tipeadas que se mandaron, generadas con semilla
```

`casos.json` es **compatible con el de `comparar_vision`** (los campos `id`, `archivo`, `legible`, `renglones`,
`tachados`), así el nivel (a) corre hoy mismo con `--casos data/benchmark_manuscritos/casos.json`. Suma lo que hace
falta para (b) y (c) y para cortar resultados:

```json
{
  "id": "p03-A",
  "archivo": "imagenes/p03-A.jpg",
  "persona": "p03",
  "hoja": "A",
  "grupo": "reservado",
  "condiciones": {"luz": "artificial", "papel": "cuaderno rayado", "angulo": "de costado"},
  "legible": true,
  "encabezado": {"cliente": "Peluquería Los Álamos", "fecha": "12/11"},
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

## 5. Plantilla para mandarle a los amigos

Un mensaje por persona, con su lista generada (ejemplo de la hoja A de la persona 03):

> ¡Hola! Te pido una mano para un trabajo de la facu: estoy midiendo qué tan bien una IA lee pedidos escritos a mano.
>
> 1. **Copiá a mano esta lista en un papel**, con tu letra de siempre, como si le hicieras un pedido a tu proveedor.
>    Copiá las mismas palabras y números de cada renglón; mayúsculas, letra y espacios como te salgan.
> 2. Arriba poné el nombre del negocio y la fecha que figuran abajo (son inventados).
> 3. **Tachá el renglón que dice «tachar»** con una línea, como si te hubieras arrepentido.
> 4. **Sacale una foto con el celu** como la mandarías por WhatsApp: sin escanear, sin recortar, con la luz que
>    tengas. Mandámela por acá.
>
> No pongas tu nombre ni datos tuyos en la hoja. La foto se usa solo para este trabajo, no se publica, y se manda a
> la API del modelo que la lee (OpenAI o Google) para medir. Si preferís que no, no pasa nada.
>
> **Peluquería Los Álamos — 12/11**
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
> 2 Belkor crema de peinar rulos ← **tachar**
> Duomo decolorante sachet x24
> 2 acond keratina Nordika 900
> Brisa acond color 900 x 2
> 3 Kapris acond color 300
> 1 Fiorela sh nutrición 5 L
> 5 Lumine sh nutrición 900

Al recibir la foto anotamos `condiciones` (luz, papel, ángulo) sin guardar nada más de la persona.

## 6. Scoring function

### 6.1 Opciones

| Scoring | Cómo | A favor | En contra |
|---|---|---|---|
| **Boolean** por imagen | 1 si todos los renglones salen bien y no sobra ninguno | Es lo que ve el cliente: «salió perfecto» | Con 15 renglones casi siempre da 0; con 20 imágenes el intervalo es enorme; no da señal para optimizar |
| **Por ítem**: precision / recall / F1 | Un renglón es correcto si coinciden producto y cantidad | Separa omitidos (recall) de inventados (precision); ya existe en `evaluar_agente` | Un «7» leído «1» pesa igual que un producto inventado; no premia preguntar |
| **Por campo, con pesos** | Crédito parcial por producto, cantidad y detalle | Da gradiente (útil para DSPy/GEPA); refleja qué error es peor | Hay que elegir pesos y se puede «jugar» con las dudas si no se acota |

### 6.2 La elegida: puntaje por renglón con pesos y dudas

Se usa la ponderada como **métrica principal** (la que se optimiza), y se reportan la boolean y la de
precision/recall como diagnóstico, más dos **guardarraíles** que no se promedian.

**Emparejar.** Cada renglón esperado se empareja con, como mucho, un renglón de la salida **por producto**:

- nivel (a): el renglón leído tiene todas las `palabras` (en `texto`, `producto`, `marca` o `presentacion`);
- nivel (b) y (c): el SKU coincide, o el SKU esperado está entre las opciones de un resultado `varias`.

Un producto mal leído no se empareja: cuenta como **omitido** y su lectura como **sobrante** (el cliente no recibe lo
que pidió y recibe otra cosa: dos errores).

**Puntaje de un par:**

```
s = 0,8                         si el renglón tiene duda (de lectura, o quedó entre varias opciones)
s = 0,5 + 0,4·C + 0,1·D         si no

C = 1 si la cantidad es la esperada
D = 1 si el detalle es el esperado: tono y unidad (si el esperado no tiene detalle, D = 1)
```

- El producto vale 0,5 porque es la condición para emparejar; la cantidad 0,4 porque una cantidad mal es casi tan
  grave como un producto mal; el detalle 0,1.
- **Una duda vale 0,8**: el agente va a preguntar, así que no sale mal, pero cuesta una pregunta al cliente. Un
  error silencioso de cantidad vale 0,6 (peor que preguntar) y un renglón bien sin duda vale 1 (mejor que
  preguntar). Así marcar duda en todo no conviene: 15 renglones con duda dan 0,8, menos que un modelo que acierta
  el 90 %.

**Puntaje de una imagen y del benchmark:**

```
S = Σ s(pares) / (esperados + sobrantes)
```

El denominador suma los sobrantes (inventados o tachados que se pidieron): así un modelo que inventa renglones no
puede subir el puntaje. El puntaje del benchmark es la suma de numeradores sobre la suma de denominadores de todas
las imágenes (micro-promedio: cada renglón pesa igual), con su intervalo por bootstrap sobre imágenes.

**Guardarraíles (se reportan aparte, objetivo 0):**

- **errores silenciosos**: pares con la cantidad o el detalle mal y sin duda, más los sobrantes;
- **tachados pedidos**: renglones tachados que terminaron en el pedido (ya los mide `comparar_vision`, RNF-18).

### 6.3 Normalización antes de comparar

| Qué | Regla | Ejemplos |
|---|---|---|
| Texto | Minúsculas, sin tildes ni signos, espacios simples, singular (`busqueda_service.normalizar`) | «Máscara», «MASCARA», «mascaras» → `mascara` |
| Marcas | Sin apóstrofos ni guiones; distancia de edición ≤ 1 contra la lista del catálogo solo en el nivel (a) | «L'Oréal» → `loreal`; «Nordica» → `nordika` |
| Abreviaturas | Solo en el nivel (a) y solo si la lista no la trae abreviada: sh → shampoo, acond → acondicionador, masc → mascara, trat → tratamiento, ox → oxidante, decol → decolorante | «acond.» = «acondicionador» |
| Cantidades | Número; palabras a número; fracciones a decimal | «una» = 1, «media docena» = 6, «doce» = 12, «x3» = «(3)» = «3u» = 3, «1/2» = «0,5» = «½» = 0,5 |
| Medidas | A ml o g; «x» delante es medida si va pegada a un número de presentación del catálogo | «x900» = «900ml» = «900 ml» = «0,9 L»; «1L» = «1 lt» = 1000 ml; «1k» = «1 kg» = 1000 g |
| Tonos | Punto o coma decimal; la barra no es decimal (es tono/cantidad, como en `INSTRUCCIONES`) | «7.1» = «7,1»; «7/2» = tono 7, cantidad 2 |
| Unidad | `unidades`, `cajas`, `cada_uno` | «u», «unid» → unidades; «cj», «cajas» → cajas; «c/u» → cada_uno |

En los niveles (b) y (c) casi toda la normalización la hace el SKU: si llegó al SKU correcto, la abreviatura o la
tilde ya no importan.

### 6.4 Ejemplo calculado

Hoja `p03-A` de la §5 (15 renglones esperados, uno tachado), nivel (b). Supongamos que la lectura + búsqueda da:

| Renglones | Qué pasó | s |
|---|---|---|
| N1–N4, Belkor matizador, Fiorela gel, baño Trevia, Duomo, acond Nordika, Brisa, Kapris acond y Lumine (12 renglones) | SKU y cantidad bien, sin duda | 12 × 1 = **12** |
| N5 «Silkara protector térmico (7)» | Leyó «(1)», sin duda: error silencioso | 0,5 + 0,4·0 + 0,1·1 = **0,6** |
| «10 serum puntas Orkidea» | Leyó «1» con duda «¿1 o 10?» | **0,8** |
| «1 Fiorela sh nutrición 5 L» | No lo leyó (quedó en el pliegue) | omitido: **0** |
| «2 Belkor crema de peinar rulos» (tachado) | Lo pidió | sobrante (+1 al denominador) |

```
S = (12 + 0,6 + 0,8 + 0) / (15 esperados + 1 sobrante) = 13,4 / 16 = 0,84
```

Las otras dos, sobre el mismo resultado:

- **Boolean:** 0 (no salió perfecta).
- **Por ítem:** 12 correctos (SKU y cantidad); 15 renglones en la salida (14 de los esperados + el tachado):
  precision = 12/15 = 0,80, recall = 12/15 = 0,80, F1 = 0,80.
- **Guardarraíles:** errores silenciosos = 2 (la cantidad de N5 y el tachado), tachados pedidos = 1.

La ponderada (0,84) distingue algo que el F1 (0,80) no ve: el «10» con duda no sale mal porque el agente lo pregunta,
y el «7» leído «1» sí. Si un cambio de prompt convierte el error de N5 en una duda, S sube a 0,85 y los errores
silenciosos bajan a 1; el F1 no se mueve.

## 7. Costo, latencia y separación desarrollo / reservado

**Por imagen** se registra: modelo, segundos, tokens de entrada y salida, USD (`--precio`), si el JSON salió
válido y el error si lo hubo (todo eso ya está en `Lectura`). Por modelo se reporta: S con intervalo, p50 y p95 de
segundos, USD por imagen y USD por cada 1000 renglones. Con eso se arma la **frontera puntaje vs. costo**: por
ejemplo, si Qwen local (USD 0, ~16 s) queda cerca de un modelo de API, o si `VISION_MARCAS` vale los tokens extra
(cierra D-42).

**Separación por persona, no por imagen:** las dos hojas de una persona van al mismo grupo, así lo reservado mide
letra que el prompt nunca vio, como un cliente nuevo.

- `desarrollo` (≈ 40 % de las personas): se mira, se itera `INSTRUCCIONES` y se optimiza con DSPy/GEPA usando S
  como métrica.
- `reservado` (≈ 60 %): solo se corre para medir. No se ajusta nada mirando sus casos (misma regla que D-50 en
  `evaluar_agente`).
- Opcional: partir también el pool de 100 productos (40/60), para que lo reservado tenga vocabulario no visto.

El mismo benchmark sirve para comparar un modelo solo, modelo + prompt (con y sin `VISION_MARCAS`, otro
`INSTRUCCIONES`), el workflow determinístico (nivel b) o el agente (nivel c).

## 8. Preguntas abiertas para cerrar el diseño

1. ~~**Renglones por hoja**~~: cerrado, 15 (5 de núcleo + 10 sorteados).
2. **Personas y fotos:** ¿cuántos amigos van a participar y cuántas hojas escribe cada uno (1 o 2)? ¿Una foto por
   hoja, o también una segunda foto «mala» (de costado, con sombra) para medir robustez?
3. **Elegir vs. sortear:** la propuesta es sortear los 10 variables con semilla; ¿te parece, o querés que elijan?
4. **Catálogo:** ¿usamos el ficticio `catalogo_evaluacion.csv` (versionable, recomendado) o el real (más fiel, pero
   las listas y respuestas no pueden ir al repo)?
5. **Fotos en el repo:** ¿con permiso explícito de alguno, sumamos 2 o 3 fotos a `tests/fixtures/imagenes_pedidos/`,
   o quedan todas en `data/`?
6. **Modelos a comparar:** `gpt-6-luna`, GPT Terra y un modelo local (§2.1). Falta el id exacto de Terra y si el
   local es Qwen3-VL 2B u otro.

## 9. Próximos pasos (después del OK)

1. Spec: sumar RF y CA del benchmark a `docs/spec/lectura-de-imagenes.md`.
2. Script que genera `pool.csv` y `listas.json` con semilla, y el texto de cada mensaje.
3. Juntar las fotos y armar `casos.json`.
4. Correr el nivel (a) con `comparar_vision --casos` (sin código nuevo).
5. Runner del nivel (b) con el puntaje de la §6 (tests con datos sintéticos que citen sus CA) y línea en `CHANGELOG.md`.
6. Nivel (c) y optimización con DSPy/GEPA solo con permiso, porque llaman a un LLM pago.
