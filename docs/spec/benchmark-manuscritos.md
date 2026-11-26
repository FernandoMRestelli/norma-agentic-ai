# Spec: Benchmark de pedidos escritos a mano

> Cómo se generan las listas que copian a mano los participantes del benchmark, el Google Form con el que se juntan
> las fotos, y cómo se pasa cada foto por los modelos de visión y se puntúa contra su lista. El diseño completo del
> benchmark (niveles, scoring, separación) está en
> [`docs/benchmark-pedidos-manuscritos.md`](../benchmark-pedidos-manuscritos.md). Ver
> [cómo trabajamos con SDD](README.md#1-cómo-trabajamos-con-sdd).

| | |
|---|---|
| Etapa del roadmap | 03 · Pedidos multimodales |
| Estado | Implementado (RF-126 a RF-140); falta correrlo con las fotos |
| Responsable | Federico Picado |
| Requisitos relacionados | RF-101 a RF-110 y RNF-18 ([lectura de imágenes](lectura-de-imagenes.md)), D-50 (desarrollo y reservado) |
| IDs que reserva | RF-126 a RF-140, CA-218 a CA-238 |

## 1. Objetivo

Que la respuesta correcta de cada foto del benchmark se conozca de antemano y salga de una planilla versionada, sin
transcribir nada. Cada participante elige una lista numerada, ya sorteada, en un Google Form, la copia a mano y
sube ahí la foto. Para el puntaje, lo único que importa de su respuesta es el número de lista.

Con las fotos juntas, decidir qué modelo de visión conviene para leer pedidos escritos a mano: pasar cada foto por
cada modelo una sola vez, guardar lo que devolvió y puntuarlo contra su lista todas las veces que haga falta, sin
volver a pagar.

## 2. Alcance

| Dentro | Fuera |
|---|---|
| El pool de renglones tipeados (`pool.csv`) | Bajar las fotos del Drive, renombrarlas y borrar mail y nombre del CSV: se hace a mano (diseño, §5) |
| El sorteo de las listas con semilla (`listas.json`) | La pregunta de subir la foto: Apps Script no puede crearla y se agrega a mano |
| El Google Apps Script que crea el Form (`crear_form.gs`) | El nivel (c), con el agente completo: lo cubre `evaluar_agente` y cuesta plata |
| Armar `casos.json` desde las respuestas del Form y la revisión de cada foto | Optimizar `INSTRUCCIONES` con DSPy/GEPA |
| Correr los modelos de visión sobre las fotos y guardar cada salida | Cambiar el modelo de visión de producción: se decide con los resultados, en otra PR |
| Puntuar los niveles (a) lectura y (b) lectura + búsqueda, con costo y latencia | |

## 3. Comportamiento

```
pool.csv ──► python -m internal.benchmark_manuscritos listas ──► listas.json
listas.json ──► python -m internal.benchmark_manuscritos form ──► crear_form.gs ──► script.google.com ──► Form

respuestas.csv + revision.json + listas.json ──► python -m internal.benchmark_manuscritos casos ──► casos.json
casos.json + imagenes/ ──► python -m internal.evaluar_manuscritos correr ──► corridas/<corrida>/<modelo>/<foto>.json
corridas/<corrida>/ ──► python -m internal.evaluar_manuscritos puntuar ──► corridas/<corrida>/resumen.json + tabla
```

`listas` y `form` trabajan sobre `data_example/benchmark_manuscritos/`, que se versiona. `casos`, `correr` y
`puntuar` trabajan sobre `data/benchmark_manuscritos/`, que no se versiona: las fotos son letra de personas reales.

### 3.1 Listas y Form

1. **Pool.** Cada fila es un renglón tal como se copia («Kapris tintura 7.1 x3»), con su respuesta: SKU de
   `catalogo_evaluacion.csv`, cantidad, tono y unidad si los tiene, las `palabras` del nivel (a) y sus tipos de
   dificultad. Las filas `nucleo` (N1 a N5) van en todas las listas; las `pool` (unas 100) se sortean.
2. **Listas.** Cada lista trae el núcleo en los renglones 1 a 5, en orden, y después 10 renglones sorteados del pool,
   mezclados. El sorteo es estratificado: primero cubre cada tipo de dificultad que todavía no
   tiene la parte variable y después completa al azar. En una lista no se repite un SKU. Cada
   lista lleva el mismo encabezado, «Pedido para Norma». La misma semilla da las mismas listas.
3. **Form.** El script crea un Form con: una sección de inicio con las instrucciones, la casilla de consentimiento y
   el desplegable «Elegí un pedido», que lleva a la sección de esa lista; una sección por lista, solo
   con el texto a copiar, que sigue a la sección final; y la sección final con el control de lo copiado, la luz y
   el papel de la foto y el sexo (masculino, femenino o prefiero no responder), para cortar los resultados. No pide
   nombre ni mail y no recolecta mails. Las instrucciones dicen que, si se equivocan
   al escribir, pueden tachar y seguir; ningún renglón está asignado para tachar. Quien participa ve cada lista como
   un pedido: `L07` aparece como «Pedido 07» en el desplegable y en el título de su sección, y así queda en las
   respuestas.
4. **A mano**, después de correr el script: agregar en la sección final la pregunta «Subir archivo» (solo imágenes,
   un archivo, obligatoria). El script lo recuerda en el log, junto con el link para editar.

Tipos de dificultad que tiene que cubrir la parte variable de cada lista: `abreviatura`, `x_medida`,
`numero_en_producto`, `cantidad_en_palabras`, `cantidad_al_final`, `numeros_confusos`, `tono`, `unidad` y
`casi_iguales`.

### 3.2 Casos

Antes de correr el comando, quien revisa las fotos:

1. Baja cada foto del Drive como `data/benchmark_manuscritos/imagenes/pNN-A.jpg` (persona `pNN`, hoja `A` o `B`).
   El número de persona lo pone quien revisa: el Form no sabe quién es quién, y dos hojas de la misma persona llevan
   el mismo `pNN`.
2. Exporta las respuestas del Form a CSV, borra la columna del archivo, el mail y el nombre si los hubiera, y suma
   la columna `foto` con el id de la foto de esa fila (`p03-A`). Lo guarda como `respuestas.csv`.
3. Mira cada foto contra su lista y anota en `revision.json` lo que no coincide: los renglones que la persona no
   copió o cambió, lo que tachó, si no se lee, o si la hoja se descarta entera.

`python -m internal.benchmark_manuscritos casos` arma un caso por foto:

- La lista sale de «Elegí un pedido» («Pedido 07» → `L07`), y los esperados son sus 15 renglones menos los de
  `sin_copiar`. Un renglón cambiado se saca igual que uno salteado: no se le cuenta al modelo.
- `tachados`, `legible` y las notas salen de `revision.json`; `condiciones` (luz y papel) y `sexo`, del Form.
- Una hoja con `descartar` no da caso, y el comando dice cuántas descartó y por qué.
- El grupo se asigna **por persona**, con semilla: se ordenan los `pNN`, se mezclan y el 40 % (redondeado para
  arriba) va a `desarrollo` y el resto a `reservado`. Las dos hojas de una persona van al mismo grupo, y la misma
  semilla con las mismas personas da los mismos grupos.
- **Un grupo no cambia.** Si ya hay un `casos.json`, cada persona que estaba conserva su grupo, y solo las nuevas se
  sortean, completando el 40 % de `desarrollo` sobre el total. Las fotos llegan de a poco, y alguien de `desarrollo`
  que pasara a `reservado` llevaría ahí letra que ya se miró.
- Antes de escribir nada valida todo y, si algo falla, lista los problemas con el id de cada foto y no escribe
  `casos.json` (§7).

### 3.3 Corrida

`python -m internal.evaluar_manuscritos correr --corrida <nombre> --modelo proveedor:modelo [--modelo …]` pasa cada
foto de `casos.json` por cada modelo con `vision_service.leer_imagen`, el mismo camino que en producción (mismo
`preparar_imagen`, mismas `INSTRUCCIONES`, misma salida `ImagenLeida`).

- **Una salida por foto y modelo**, en cuanto vuelve el modelo: `corridas/<corrida>/<modelo>/<foto>.json`. Si se
  corta a la mitad, lo que ya volvió queda guardado.
- **Retomar sin volver a pagar.** Si la salida de una foto ya existe y trae `resultado`, se saltea. Una salida sin
  `resultado` (el modelo falló o devolvió un JSON inválido) también se saltea, salvo con `--reintentar`: un JSON
  inválido es un resultado del modelo, no un error de la corrida.
- **Huella del prompt.** Cada salida guarda la huella de lo que se le mandó al modelo: `INSTRUCCIONES`, las marcas si
  `VISION_MARCAS` está activo y el lado máximo de la imagen. Con `VISION_MARCAS`, las marcas salen de
  `catalogo_evaluacion.csv`, el catálogo de las listas, y no del de producción. Si la huella actual no es la de las salidas ya
  guardadas de ese modelo en esa corrida, `correr` se niega a seguir y pide otra `--corrida`: mezclar dos prompts en
  una corrida invalida la comparación.
- **Solo desarrollo por defecto.** `--grupo` es `desarrollo` (por defecto), `reservado` o `todos`. El reservado se
  corre para medir, no para iterar el prompt (D-50).
- **Tope de llamadas.** Un modelo de API (`PROVEEDORES_API`) cuesta plata: sin `--max-llamadas N`, `correr` no llama
  a ninguno y dice cuántas llamadas haría. Con el tope, no hace más de N llamadas nuevas en total, entre todos los
  modelos; las salteadas no cuentan. El modelo local (`huggingface`) no lo necesita.
- `--solo p03-A` corre una sola foto.

Al terminar muestra, por modelo, cuántas salidas nuevas guardó, cuántas salteó y cuántas fallaron.

### 3.4 Puntaje

`python -m internal.evaluar_manuscritos puntuar <corrida>` lee las salidas guardadas, las cruza con `casos.json` y
puntúa los dos niveles. No llama a ningún modelo ni a la red: se puede correr todas las veces que haga falta, y las
mismas salidas dan siempre el mismo resumen.

**Nivel (a), lectura.** Los renglones leídos son los de `ImagenLeida.renglones`. Un leído se empareja con un esperado
si tiene todas sus `palabras` en `texto`, `producto`, `marca` o `presentacion`, después de normalizar (minúsculas,
sin tildes ni signos, espacios simples). Una palabra que es marca de `catalogo_evaluacion.csv` también empareja con
distancia de edición 1 («Nordica» con «nordika»). Cantidad, tono, unidad y duda:

- cantidad: la del renglón o, si tiene variantes, la suma de las suyas (como `comparar_vision`);
- tono: el de su variante, con punto o coma decimal («7,1» = «7.1»);
- unidad: la del renglón;
- duda: la del renglón o la de cualquiera de sus variantes.

**Nivel (b), lectura + búsqueda.** Los renglones leídos son los de `renglones_desde_lectura`, buscados con
`catalog_tools.buscar_renglones` contra `catalogo_evaluacion.csv` con stock suficiente en todo (como
`evaluar_busqueda`), sin Sheets ni la base. Se busca el `buscar.texto` del renglón y, si no lo tiene (le falta
producto o cantidad), su `texto`. Según el estado:

| Estado | SKU propuesto | Duda |
|---|---|---|
| `unico`, `sin_stock` | el del primer candidato | la de lectura, si tiene |
| `varias`, `aproximado` | cualquiera de los candidatos | sí: el agente pregunta o confirma |
| `no_encontrado` | ninguno: no empareja | — |

Un leído se empareja con un esperado si su SKU propuesto es el esperado. Un renglón sin cantidad leída se busca con
cantidad 1 y cuenta como duda: el agente la va a preguntar.

**Emparejar.** En los dos niveles cada esperado se empareja con, como mucho, un leído y al revés: se recorren los
esperados en el orden de la hoja y cada uno toma el primer leído libre que le corresponde. Un esperado sin pareja
es **omitido**; un leído sin pareja es **sobrante**. Un sobrante que tiene todas las palabras de un renglón de
`tachados` es además un **tachado pedido**.

**Puntaje de un par** (diseño, §6.2):

```
s = 0,8                         si el leído tiene duda
s = 0,5 + 0,4·C + 0,1·D         si no

C = 1 si la cantidad es la esperada
D = 1 si el tono y la unidad son los esperados (si el esperado no tiene ninguno de los dos, D = 1)
```

**Puntaje de una foto:** `S = Σ s / (esperados + sobrantes)`. Una salida sin `resultado` o con `legible: false`
cuando la foto se lee da S = 0 con todos los esperados omitidos. Una foto que el caso marca `legible: false` no
entra al puntaje, y se reporta si el modelo la marcó ilegible.

**Resumen**, por modelo y nivel, y para `desarrollo` y `reservado` por separado:

| Métrica | Cómo |
|---|---|
| **S** (la principal) | Micro-promedio: suma de numeradores sobre suma de denominadores de todas las fotos, con su intervalo al 95 % por bootstrap sobre fotos (`intervalo_por_casos`, semilla fija) |
| Fotos perfectas | Porcentaje de fotos con todos los pares en s = 1 y sin sobrantes |
| Precision, recall, F1 | Un par es correcto si coinciden producto y cantidad, con o sin duda |
| Errores silenciosos | Pares con C = 0 o D = 0 y sin duda, más los sobrantes. Objetivo 0 |
| Tachados pedidos | Objetivo 0 (RNF-18) |
| JSON válido | Porcentaje de salidas con `resultado` |
| Segundos | p50 y p95 por foto |
| USD | Por foto y por cada 1000 renglones esperados, si el modelo tiene `--precio` o es local (USD 0) |
| Cortes | S por tipo de dificultad (sobre los pares y omitidos de ese tipo), por `origen` (`nucleo` o `pool`), por luz y por papel |

El precio va por modelo: `--precio openai:gpt-6-luna=0.10,0.50` (USD por millón de tokens de entrada y salida), y se
puede repetir. Un modelo de API sin precio muestra el USD como desconocido, no como 0.

`puntuar` escribe `corridas/<corrida>/resumen.json` y muestra una tabla con un modelo por fila. Con `--detalle`
muestra, por foto, cada esperado con lo que se leyó y su s.

## 4. Contratos

### 4.1 `data_example/benchmark_manuscritos/pool.csv`

| Columna | Qué es |
|---|---|
| `id` | `N1`…`N5` para el núcleo, `P001`… para el pool. Único |
| `origen` | `nucleo` o `pool` |
| `texto` | El renglón tal como se copia |
| `sku` | SKU de `catalogo_evaluacion.csv` |
| `cantidad` | Número mayor que cero, ya convertido («media docena» → 6) |
| `tono`, `unidad` | Vacíos si no tiene. `unidad` es `unidades`, `cajas` o `cada_uno`, como en `ImagenLeida` |
| `palabras` | Las claves del nivel (a), separadas por espacio, como aparecen en `texto` sin tildes |
| `dificultad` | Los tipos de la §3.1, separados por espacio |

### 4.2 `data_example/benchmark_manuscritos/listas.json`

```json
{
  "semilla": 116,
  "listas": [
    {
      "id": "L01",
      "encabezado": "Pedido para Norma",
      "renglones": [
        {"n": 1, "id": "N1", "origen": "nucleo", "texto": "2 sh keratina Nordika x900", "sku": "NOR-SH-KER-900",
         "cantidad": 2, "tono": null, "unidad": null, "palabras": ["keratina", "nordika", "900"],
         "dificultad": ["abreviatura", "x_medida", "casi_iguales"]}
      ]
    }
  ]
}
```

`n` es la posición en la hoja (1 a 15) y los 15 `renglones` son los esperados. Los campos de cada renglón son los de `casos.json` del diseño (§4), así armar los casos es copiar la lista.

### 4.3 `data_example/benchmark_manuscritos/crear_form.gs`

Un Google Apps Script con una función `crearForm()` y los textos y las listas embebidos como JSON. Sale solo en
ASCII (los acentos van como `\uXXXX`): el portapapeles de macOS y algunos editores leen el archivo como Mac Roman y
rompen los acentos al copiarlo («Copiá» → «Copi√°»). Se pega en un proyecto nuevo
de script.google.com y se corre una vez con la cuenta que va a recibir las fotos.

### 4.4 `data/benchmark_manuscritos/respuestas.csv`

El export del Form, con una fila por foto. Las columnas se reconocen por el título de su pregunta en el Form
(`TEXTOS` de `internal/benchmark_manuscritos.py`); las demás (marca temporal, consentimiento) se ignoran.

| Columna | Qué es |
|---|---|
| `foto` | La agrega quien revisa: `pNN-A` o `pNN-B`, el nombre de la foto en `imagenes/` sin extensión. Única |
| «Elegí un pedido» | «Pedido 01»…«Pedido 30» |
| «¿Escribiste todos los renglones tal cual?» | «Sí» o «No» |
| «Si no, ¿cuáles cambiaste o salteaste?» | Texto libre, para quien revisa: no entra al caso |
| «Luz», «Papel», «Sexo» | Las opciones del Form |

No puede tener una columna de mail, correo, nombre ni archivo: `casos` falla si la encuentra (§7).

### 4.5 `data/benchmark_manuscritos/revision.json`

Lo escribe quien revisa las fotos. Solo llevan entrada las fotos con algo que anotar.

```json
{
  "p03-A": {"sin_copiar": [11], "tachados": [["belkor", "rulos"]], "notas": "el 11 lo escribió como «2 acond»"},
  "p04-A": {"legible": false},
  "p05-B": {"descartar": "la foto corta la mitad de la hoja"}
}
```

| Campo | Qué es | Por defecto |
|---|---|---|
| `sin_copiar` | Los `n` de la lista que la persona salteó o cambió | `[]` |
| `tachados` | Lo que tachó, como `palabras` de cada renglón tachado | `[]` |
| `legible` | `false` si una persona no puede leer la hoja | `true` |
| `descartar` | Por qué la hoja no se usa | no se descarta |
| `notas` | Texto libre | — |

### 4.6 `data/benchmark_manuscritos/casos.json`

Compatible con el de `comparar_vision` (`id`, `archivo`, `legible`, `renglones`, `tachados`), así el nivel (a)
también corre con `comparar_vision --casos`. Es una lista con un caso por foto, como en el diseño (§4):

```json
[
  {
    "id": "p03-A", "archivo": "imagenes/p03-A.jpg", "persona": "p03", "hoja": "A", "lista": "L07",
    "grupo": "reservado", "condiciones": {"luz": "artificial", "papel": "cuaderno rayado"},
    "sexo": "prefiero no responder", "legible": true, "encabezado": "Pedido para Norma",
    "renglones": [
      {"n": 1, "id": "N1", "origen": "nucleo", "texto": "2 sh keratina Nordika x900", "sku": "NOR-SH-KER-900",
       "cantidad": 2, "tono": null, "unidad": null, "palabras": ["keratina", "nordika", "900"],
       "dificultad": ["abreviatura", "x_medida", "casi_iguales"]}
    ],
    "tachados": [["belkor", "rulos"]],
    "sin_copiar": [11]
  }
]
```

`archivo` es relativo a la carpeta de `casos.json`. Las condiciones y el sexo van en minúsculas.

### 4.7 `data/benchmark_manuscritos/corridas/<corrida>/<modelo>/<foto>.json`

`<modelo>` es `proveedor:modelo` con `:` y `/` cambiados por `_` (`huggingface_Qwen_Qwen3-VL-2B-Instruct`).

```json
{
  "foto": "p03-A", "proveedor": "openai", "modelo": "gpt-6-luna", "huella": "3f9a…", "fecha": "2026-12-02T18:04:11",
  "segundos": 4.2, "tokens_entrada": 1830, "tokens_salida": 612, "error": null,
  "crudo": "{…}", "resultado": {"legible": true, "renglones": []}
}
```

Son los campos de `vision_service.Lectura`, con `resultado` como `ImagenLeida.model_dump()` o `null`, más `foto`,
`huella` (SHA-256 de lo que describe la §3.3) y `fecha`.

### 4.8 `data/benchmark_manuscritos/corridas/<corrida>/resumen.json`

```json
{
  "corrida": "2026-12-02-luna", "casos": "data/benchmark_manuscritos/casos.json",
  "modelos": [
    {"modelo": "openai:gpt-6-luna", "huella": "3f9a…",
     "niveles": {
       "b": {"desarrollo": {"fotos": 8, "S": 0.84, "S_intervalo": [0.79, 0.88], "fotos_perfectas_pct": 25.0,
                            "precision": 0.8, "recall": 0.8, "f1": 0.8, "errores_silenciosos": 9,
                            "tachados_pedidos": 1, "json_valido_pct": 100.0, "segundos_p50": 4.1,
                            "segundos_p95": 6.3, "usd_por_foto": 0.0012, "usd_por_1000_renglones": 0.08,
                            "por_dificultad": {"tono": 0.9}, "por_origen": {"nucleo": 0.88},
                            "por_luz": {"natural": 0.86}, "por_papel": {"hoja lisa": 0.85}},
             "reservado": {}}}}
  ]
}
```

## 5. Requisitos

| ID | Requisito |
|---|---|
| RF-126 | El pool tiene 5 renglones de núcleo y al menos 100 sorteables, cada uno con un SKU de `catalogo_evaluacion.csv`, una cantidad mayor que cero y al menos 5 renglones sorteables por cada tipo de dificultad |
| RF-127 | Cada lista tiene el núcleo en los renglones 1 a 5, 10 renglones sorteados que cubren todos los tipos de dificultad y ningún SKU repetido, sin renglones asignados para tachar; la misma semilla da las mismas listas |
| RF-128 | Si el pool no alcanza para cubrir un tipo de dificultad, el sorteo falla con un error que nombra el tipo, en vez de dar una lista incompleta |
| RF-129 | El script crea un Form con una sección por lista, a la que lleva el desplegable del inicio y que sigue a la sección final y no pide ni recolecta nombre ni mail |
| RF-130 | `casos` arma un caso por foto no descartada, con los renglones de su lista menos los `sin_copiar`, los tachados y la legibilidad de la revisión y las condiciones y el sexo del Form |
| RF-131 | El grupo se asigna por persona con semilla: el 40 % de las personas, redondeado para arriba, va a `desarrollo`; las hojas de una persona comparten grupo, la misma semilla da los mismos grupos y una persona que ya estaba en `casos.json` conserva el suyo |
| RF-132 | `casos` no escribe nada si encuentra un problema, y lista cada uno con su foto: una columna de mail, nombre o archivo; un pedido que no está en `listas.json`; una foto repetida, sin archivo o con un id que no es `pNN-A`/`pNN-B`; un «No» en «¿Escribiste todos…?» sin entrada en la revisión; un `sin_copiar` fuera de 1 a 15; una entrada de la revisión sin foto |
| RF-133 | `correr` guarda una salida por foto y modelo en cuanto vuelve el modelo, y saltea las que ya existen salvo, con `--reintentar`, las que no tienen `resultado` |
| RF-134 | `correr` no llama a un modelo de API sin `--max-llamadas`, y con el tope no hace más llamadas nuevas que esas en toda la corrida |
| RF-135 | Cada salida guarda la huella del prompt de visión; si no coincide con la de las salidas ya guardadas de ese modelo en la corrida, `correr` se niega a seguir |
| RF-136 | `correr` corre solo el grupo `desarrollo` salvo que se pida `--grupo reservado` o `todos` |
| RF-137 | El nivel (a) empareja por las `palabras` del esperado, con distancia de edición 1 en las marcas del catálogo, uno a uno y en el orden de la hoja |
| RF-138 | El nivel (b) empareja por SKU con la búsqueda contra `catalogo_evaluacion.csv`, sin red; `varias` y `aproximado` emparejan si el SKU esperado está entre los candidatos, y cuentan como duda |
| RF-139 | El puntaje de un par es 0,8 con duda y 0,5 + 0,4·C + 0,1·D sin duda; el de una foto es Σ s / (esperados + sobrantes), y una salida sin `resultado` da S = 0 |
| RF-140 | `puntuar` reporta por modelo, nivel y grupo el S micro con intervalo por bootstrap sobre fotos, fotos perfectas, precision, recall y F1, errores silenciosos, tachados pedidos, JSON válido, segundos p50 y p95, USD con precio por modelo y los cortes por dificultad, origen, luz y papel; las mismas salidas dan el mismo resumen |

## 6. Criterios de aceptación

| ID | Dado | Cuando | Entonces | Test |
|---|---|---|---|---|
| CA-218 | `pool.csv` y `catalogo_evaluacion.csv` | Se valida el pool | No hay problemas: ids únicos, 5 de núcleo, ≥ 100 sorteables, SKU del catálogo, cantidades > 0, unidades válidas y ≥ 5 por tipo | `tests/test_benchmark_manuscritos.py::test_pool_coherente_con_el_catalogo` |
| CA-219 | El pool y la semilla 116 | Se sortean 30 listas dos veces, y otra vez con la semilla 117 | Las dos primeras son iguales y la tercera no | `tests/test_benchmark_manuscritos.py::test_listas_reproducibles` |
| CA-220 | 30 listas sorteadas | Se mira cada una | El núcleo está en 1 a 5, hay 10 sorteados que cubren todos los tipos, las posiciones son 1 a 15 y no se repite ningún SKU | `tests/test_benchmark_manuscritos.py::test_lista_cubre_las_dificultades` |
| CA-221 | 30 listas sorteadas | Se miran sus renglones y el texto de inicio | Ninguna lista asigna un renglón para tachar, y el inicio dice que se puede tachar si se equivocan | `tests/test_benchmark_manuscritos.py::test_sin_renglon_para_tachar` |
| CA-222 | Un pool sintético sin renglones de `tono` | Se sortea una lista | Falla con un error que dice `tono` | `tests/test_benchmark_manuscritos.py::test_pool_sin_un_tipo_falla` |
| CA-223 | Dos listas, una con comillas en un renglón | Se genera el script | Trae una sección por lista con «Pedido para Norma» y sus 15 renglones, el desplegable con «Pedido 01» y «Pedido 02», el salto a la sección final, `setCollectEmail(false)`, ninguna pregunta por el ángulo y las listas como JSON válido; el archivo es solo ASCII y los acentos vuelven al leer el JSON | `tests/test_benchmark_manuscritos.py::test_script_del_form` |
| CA-224 | Un `respuestas.csv` sintético con tres fotos («Pedido 07», «Pedido 02» y otra descartada) y una revisión con `sin_copiar: [11]` y un tachado en la primera | Se arman los casos | Salen dos casos: el de `L07` con 14 renglones (sin el 11), su tachado y sus condiciones en minúsculas; el de `L02` con sus 15; la descartada no sale y se informa | `tests/test_benchmark_manuscritos.py::test_casos_desde_respuestas` |
| CA-225 | Diez personas sintéticas, tres con dos hojas | Se asignan los grupos dos veces con la misma semilla, y otra vez sumando cinco personas nuevas | Cuatro personas en `desarrollo` y seis en `reservado`, las dos hojas de cada persona en el mismo grupo e igual las dos veces; con las quince, las diez de antes siguen en su grupo y `desarrollo` llega a seis | `tests/test_benchmark_manuscritos.py::test_grupos_por_persona`, `test_grupos_comparten_las_hojas`, `test_comando_casos_conserva_los_grupos` |
| CA-226 | Respuestas con un «Pedido 31», una foto repetida, una sin archivo, un id que no es `pNN-A`, un «No» sin revisión, un `sin_copiar: [16]` y una entrada de la revisión sin foto; aparte, unas con una columna «Dirección de correo electrónico» o un link de Drive | Se arman los casos | Falla con un problema por cada uno, que nombra su foto, y no escribe `casos.json`; con la columna de mail o el link falla solo por eso, nombrando la columna | `tests/test_benchmark_manuscritos.py::test_casos_con_problemas`, `test_casos_sin_datos_de_la_persona` |
| CA-227 | Tres fotos sintéticas, un modelo falso que cuenta sus llamadas y una corrida con la salida de la primera ya guardada | Se corre | Llama dos veces y quedan las tres salidas, con los campos de la §4.7 | `tests/test_evaluar_manuscritos.py::test_correr_retoma_sin_repetir` |
| CA-228 | Una corrida con una salida sin `resultado` | Se corre sin y con `--reintentar` | Sin la opción no la vuelve a llamar; con ella sí, y la reemplaza | `tests/test_evaluar_manuscritos.py::test_correr_reintenta_solo_si_se_pide` |
| CA-229 | Un modelo `openai:` falso y cinco fotos | Se corre sin `--max-llamadas` y después con `--max-llamadas 2` | La primera no llama nada y dice que serían 5 llamadas; la segunda llama 2 veces | `tests/test_evaluar_manuscritos.py::test_correr_con_tope_de_llamadas` |
| CA-230 | Una corrida con salidas de una huella y otras `INSTRUCCIONES` | Se corre otra vez | Se niega, no llama al modelo y pide otra `--corrida` | `tests/test_evaluar_manuscritos.py::test_correr_con_otro_prompt_se_niega` |
| CA-231 | Casos de los dos grupos | Se corre sin `--grupo` | Solo llama con las fotos de `desarrollo` | `tests/test_evaluar_manuscritos.py::test_correr_solo_desarrollo` |
| CA-232 | Un esperado «Nordika» y un leído «Nordica 900 keratina» con otro leído antes que también tiene las palabras | Se puntúa el nivel (a) | El esperado se empareja con el primero, el otro queda sobrante, y la marca con una letra cambiada empareja | `tests/test_evaluar_manuscritos.py::test_nivel_a_empareja_por_palabras` |
| CA-233 | Leídos que la búsqueda deja `unico`, `varias` con el SKU esperado entre los candidatos, `aproximado` y `no_encontrado` contra `catalogo_evaluacion.csv` | Se puntúa el nivel (b) | El `unico` empareja sin duda, `varias` y `aproximado` emparejan con duda y el `no_encontrado` queda sobrante con su esperado omitido | `tests/test_evaluar_manuscritos.py::test_nivel_b_empareja_por_sku` |
| CA-234 | La hoja `p03-A` del diseño (§6.4): 12 renglones bien, N5 con la cantidad mal, uno con duda, uno omitido y un tachado pedido | Se puntúa | S = 13,4 / 16 = 0,84; precision y recall 0,80; errores silenciosos 2; tachados pedidos 1; no es perfecta | `tests/test_evaluar_manuscritos.py::test_ejemplo_del_diseno` |
| CA-235 | Una salida sin `resultado` para una foto legible de 15 renglones, y otra sin `resultado` ni `crudo` (el modelo no contestó) | Se puntúa | La primera da S = 0, 15 omitidos, y JSON válido baja; la segunda no entra al puntaje y se avisa que hay que `--reintentar` | `tests/test_evaluar_manuscritos.py::test_json_invalido_da_cero`, `test_sin_respuesta_no_cuenta` |
| CA-236 | Un par con tono «7,1» leído y «7.1» esperado y otro con unidad `cajas` leída y `unidades` esperada | Se puntúa | El primero da D = 1 y el segundo D = 0 | `tests/test_evaluar_manuscritos.py::test_detalle_normalizado` |
| CA-237 | Una corrida con dos modelos, uno de API con precio y otro local, y fotos de los dos grupos | Se puntúa dos veces | Las dos dan el mismo `resumen.json`, con desarrollo y reservado separados, el local con USD 0, el de API con su USD, y los cortes por dificultad, origen, luz y papel | `tests/test_evaluar_manuscritos.py::test_resumen_por_modelo_y_grupo` |
| CA-238 | Una corrida guardada | Se puntúa con la red cortada y sin claves | Termina sin llamar a ningún modelo | `tests/test_evaluar_manuscritos.py::test_puntuar_sin_red` |

## 7. Errores y casos borde

- Un SKU del pool que no está en el catálogo: `listas` no sortea y muestra los problemas de `validar_pool`.
- Pedir más renglones variables que tipos se puedan cubrir sin repetir SKU: falla con RF-128.
- Una persona que salteó o cambió un renglón: va en `sin_copiar` de la revisión y no se le cuenta al modelo.
- Un «No» en el control sin entrada en la revisión: `casos` falla (RF-132). Si al revisar resulta que copió todo, se
  anota la foto con `{}` en la revisión.
- `respuestas.csv` con datos de la persona (mail, nombre, el link del archivo, que puede llevar su nombre): `casos`
  falla antes de leer nada más.
- Dos personas eligieron el mismo pedido: son dos casos válidos con la misma lista.
- Una foto que una persona no puede leer (`legible: false` en la revisión): no entra al puntaje; si el modelo la
  marcó ilegible, se cuenta como acierto de legibilidad.
- El modelo dice `legible: false` en una foto que se lee: S = 0 con todos los esperados omitidos, igual que un JSON
  inválido.
- El modelo no contestó (cuota, timeout, red): la salida queda sin `resultado` y sin `crudo`. No es un resultado
  del modelo, así que `puntuar` no la cuenta como S = 0: la trata como foto sin salida y sugiere `--reintentar`. Un
  JSON inválido, en cambio, sí trae `crudo` y cuenta como S = 0.
- El modelo local falla al cargar (faltan `requirements-vision-local.txt`): `correr` lo dice y sigue con los demás
  modelos.
- Una corrida cortada por el tope de llamadas: `puntuar` puntúa lo que hay y avisa cuántas fotos de cada modelo no
  tienen salida; esas no entran al S de ese modelo, así que dos modelos con distinta cantidad de fotos no se
  comparan sin avisar.
- `puntuar` con salidas de una foto que ya no está en `casos.json` (se descartó después): las ignora y lo avisa.

## 8. Decisiones y preguntas abiertas

- **Ningún renglón está asignado para tachar.** Asignarlo era artificial: las instrucciones dicen que, si se
  equivocan al escribir, tachen y sigan, como en un pedido real. Lo que aparezca tachado se anota al revisar la foto
  (`tachados` de `casos.json`) y cuenta para el guardarraíl de tachados pedidos.
- **Cada persona elige la lista.** Un solo link para todos, y cada uno elige en el desplegable. Puede pasar que
  varias personas elijan la misma lista y que otras no las elija nadie: dos personas con la misma lista siguen siendo
  datos válidos (otra letra, mismo texto), solo cubren menos vocabulario. Como las 30 listas se sortearon igual de
  difíciles, elegir el número no sesga la dificultad. Se puede activar «Mezclar orden de las opciones» en el
  desplegable para que no gane siempre `L01`. Se probó dar un link personal por lista y se descartó.
- **La foto se sube a mano en el Form.** Ni Apps Script ni la API de Forms crean preguntas de subir archivo.
- **Correr y puntuar van separados.** Correr es lo que cuesta plata y tiempo; puntuar es determinístico. Con las
  salidas guardadas, un cambio en el puntaje, en la búsqueda o en el catálogo se mide otra vez sin llamar a ningún
  modelo, y dos personas pueden puntuar la misma corrida y obtener lo mismo.
- **El código va en `internal/`, no en una carpeta de experimentos.** Reusa `leer_imagen`, `renglones_desde_lectura`
  y `buscar_renglones` tal como corren en producción, y pasa por ruff y por tests que citan su CA, como
  `evaluar_busqueda` y `evaluar_agente`. Lo que es de cada experimento (fotos, salidas, resúmenes) vive en
  `data/benchmark_manuscritos/`, que no se versiona.
- **Un renglón cambiado se saca, no se corrige.** Reescribir el esperado de una foto con lo que la persona escribió
  pide transcribir y elegir el SKU a mano, que es justo lo que el benchmark evita. Con 15 renglones por hoja, perder
  uno o dos casi no cambia el tamaño.
- **El grupo lo decide la semilla, no quien revisa.** Elegir a mano qué personas van a reservado podría dejar las
  letras más fáciles de un lado.
- **`comparar_vision` sigue igual.** Sirve para las 8 imágenes de `tests/fixtures/` y para mirar rápido el nivel (a)
  de `casos.json`; el benchmark se mide con `evaluar_manuscritos`.
- **Abierta: el id exacto de GPT Terra.** Hace falta antes de correr; no cambia nada de esta spec.

## 9. Plan de implementación

1. ~~Esta spec y el pool.~~
2. ~~Tests de CA-218 a CA-223 en `tests/test_benchmark_manuscritos.py`.~~
3. ~~`internal/benchmark_manuscritos.py` con los comandos `listas` y `form`, y `listas.json` y `crear_form.gs`
   generados con la semilla 116.~~
4. ~~RF-130 a RF-140 y CA-224 a CA-238 en esta spec.~~
5. ~~Tests de CA-224 a CA-226 y el comando `casos` en `internal/benchmark_manuscritos.py`.~~
6. ~~Tests de CA-227 a CA-238 con fotos y modelos falsos, e `internal/evaluar_manuscritos.py` con `correr` y
   `puntuar`. Los tests no llaman a ningún modelo.~~
7. Con las fotos: armar `casos.json`, correr Qwen local sobre `desarrollo` y, con permiso de quien paga la clave,
   `gpt-6-luna` y GPT Terra con `--max-llamadas`. El reservado se corre al final, una vez, para medir.
