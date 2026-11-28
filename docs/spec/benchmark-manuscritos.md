# Spec: Benchmark de pedidos escritos a mano

> Cómo se generan las listas que copian a mano los participantes del benchmark, el Google Form con el que se juntan
> las fotos, y cómo se pasa cada foto por los modelos de visión y se puntúa contra su lista. El diseño completo del
> benchmark (niveles, scoring, separación) está en
> [`docs/benchmark-pedidos-manuscritos.md`](../benchmark-pedidos-manuscritos.md). Ver
> [cómo trabajamos con SDD](README.md#1-cómo-trabajamos-con-sdd).

| | |
|---|---|
| Etapa del roadmap | 03 · Pedidos multimodales |
| Estado | Implementado (RF-126 a RF-152); falta validar la scoring con el orden a mano |
| Responsable | Federico Picado |
| Requisitos relacionados | RF-101 a RF-110 y RNF-18 ([lectura de imágenes](lectura-de-imagenes.md)), D-50 (desarrollo y reservado) |
| IDs que reserva | RF-126 a RF-152, CA-218 a CA-254 |

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
| Medir una señal de «puede faltar un renglón» que el bot podría usar sin conocer la hoja | Sumar esa señal al bot, o decirle al cliente cuántos productos se leyeron: se decide con lo que mida el benchmark, en otra spec |
| Correr los modelos de visión sobre las fotos y guardar cada salida | Cambiar el modelo de visión de producción: se decide con los resultados, en otra PR |
| Puntuar los niveles (a) lectura y (b) lectura + búsqueda, con costo y latencia | |
| Validar la scoring contra un orden hecho a mano, y congelar el reservado | |

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
   copió o cambió, lo que tachó, si no se lee, o si la hoja se descarta entera. Anota también, para cortar los
   resultados, si la letra es clara o difícil y si la foto es nítida o borrosa.

`python -m internal.benchmark_manuscritos casos` arma un caso por foto:

- La lista sale de «Elegí un pedido» («Pedido 07» → `L07`), y los esperados son sus 15 renglones menos los de
  `sin_copiar`. Un renglón cambiado se saca igual que uno salteado: no se le cuenta al modelo.
- `tachados`, `legible` y las notas salen de `revision.json`; `condiciones` junta la luz y el papel del Form con la
  letra y la nitidez de la revisión («sin dato» si no se anotaron). El `sexo` sale del Form.
- Una hoja con `descartar` no da caso, y el comando dice cuántas descartó y por qué.
- El grupo se asigna **por persona**, con semilla: se ordenan los `pNN`, se mezclan y el 40 % (redondeado para
  arriba) va a `desarrollo` y el resto a `reservado`. Las dos hojas de una persona van al mismo grupo, y la misma
  semilla con las mismas personas da los mismos grupos.
- **Un grupo no cambia.** Si ya hay un `casos.json`, cada persona que estaba conserva su grupo, y solo las nuevas se
  sortean, completando el 40 % de `desarrollo` sobre el total. Las fotos llegan de a poco, y alguien de `desarrollo`
  que pasara a `reservado` llevaría ahí letra que ya se miró.
- **El reservado se congela.** `casos --congelar` guarda en `congelado.json` la fecha y las personas del reservado,
  y se corre una vez, en la primera medición final. Desde ahí toda persona nueva va a `desarrollo`, también las que
  se sumen por traer errores reales: sirven para mejorar, pero no cambian el reservado con el que ya se midió.
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
  `VISION_MARCAS` está activo, el lado máximo de la imagen y los topes de tokens del modelo local (RF-148): una salida
  cortada por el tope no es comparable con una completa. Con `VISION_MARCAS`, las marcas salen de
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

Las reglas y sus razones están en el diseño, §6. Acá van como se calculan.

**Nivel (a), lectura.** Los renglones leídos son los de `ImagenLeida.renglones`. De cada esperado, sus `palabras` se
parten en dos: las que no tienen números (marca y palabra que distingue) y las que sí (la medida, o el «20» de
«20 vol»). Antes de comparar se normaliza: minúsculas, sin tildes, signos como espacios y espacios simples. Una
palabra está en el leído si aparece en `texto`, `producto`, `marca` o `presentacion`, también con los espacios y
guiones sacados («cocacola» en «Coca-Cola»), y una que es marca de `catalogo_evaluacion.csv` acepta además una letra
de diferencia («Nordica» por «nordika»). De cada leído:

- cantidad: la del renglón o, si tiene variantes, la suma de las suyas (como `comparar_vision`);
- tono: el de su variante, con punto o coma decimal («7,1» = «7.1»);
- unidad: la del renglón;
- medida: si tiene todas las palabras con números del esperado;
- duda: la del renglón o la de cualquiera de sus variantes.

**Nivel (b), lectura + búsqueda.** Los renglones leídos son los de `renglones_desde_lectura`, buscados con
`catalog_tools.buscar_renglones` contra `catalogo_evaluacion.csv` con stock suficiente en todo (como
`evaluar_busqueda`), sin Sheets ni la base. Se busca el `buscar.texto` del renglón y, si no lo tiene (le falta
producto o cantidad), su `texto`. Según el estado:

| Estado | SKU propuesto | Duda |
|---|---|---|
| `unico`, `sin_stock` | el del primer candidato | la de lectura, si tiene |
| `varias`, `aproximado` | cualquiera de los candidatos | sí: el agente pregunta o confirma |
| `no_encontrado` | ninguno: no empareja | sí: el agente dice que no lo encontró |

La medida no se puntúa aparte en (b): está en el SKU.

**Emparejar.** En los dos niveles cada esperado se empareja con, como mucho, un leído y al revés: se recorren los
esperados en el orden de la hoja y cada uno toma un leído libre que le corresponde. En (a) corresponde si tiene todas
las palabras sin números, y entre los que corresponden se prefiere el primero que también tiene la medida; en (b),
si su SKU propuesto es el esperado. Un esperado sin pareja es **omitido**; un leído sin pareja es **sobrante**. Un
sobrante que tiene todas las palabras de un renglón de `tachados` es además un **tachado pedido**.

**Puntaje de un par:**

```
s = 0,8                                    si el leído tiene duda o no tiene cantidad
s = máx(0,1 ; 1 − Σ penalizaciones)        si no
```

| Penalización | Cuánto | Cuándo |
|---|---|---|
| Variante | 0,3 | El esperado tiene tono y el leído tiene otro, o ninguno; en (a), también si falta la medida |
| Cantidad cerca | 0,4 | La cantidad no es la esperada, pero la mayor de las dos es menos del doble de la menor |
| Cantidad lejos | 0,6 | La mayor es el doble o más de la menor (o una de las dos es 0), o la clase de unidad no coincide |

Las clases de unidad son `cajas` y el resto (`unidades`, `cada_uno` o sin unidad). Se aplica una sola de las dos de
cantidad.

**Sobrantes.** Cada uno suma al denominador según su costo:

| Sobrante | Costo |
|---|---|
| Tiene las palabras de un renglón de `tachados`, con o sin duda | 1 |
| Silencioso: sin duda y, en (b), con la búsqueda en `unico` o `sin_stock` | 1 |
| Dudoso: con duda o, en (b), con la búsqueda en `varias`, `aproximado` o `no_encontrado` | 0,2 |

**Puntaje de una foto:** `S = Σ s / (esperados + Σ costo de los sobrantes)`. Una salida sin `resultado` o con
`legible: false` cuando la foto se lee da S = 0 con todos los esperados omitidos. Una foto que el caso marca
`legible: false` no entra al puntaje, y se reporta si el modelo la marcó ilegible.

**Errores graves y filtro.** Son errores graves los renglones omitidos, los sobrantes de costo 1 y los pares con
cantidad lejos sin duda. El omitido cuenta porque en un pedido real nadie avisa que faltó algo: el cliente no recibe
ese producto y no se entera hasta que llega el pedido.
Una configuración es **elegible** en un nivel y grupo si no tiene tachados pedidos y sus errores graves no pasan del
2 % de los renglones esperados. La que decide es la del nivel (b) en `reservado`; en los demás se muestra como
referencia.

**Resumen**, por modelo y nivel, y para `desarrollo` y `reservado` por separado:

| Métrica | Cómo |
|---|---|
| **S** (la principal) | Micro-promedio: suma de numeradores sobre suma de denominadores de todas las fotos, con su intervalo al 95 % por bootstrap sobre fotos (`intervalo_por_casos`, semilla fija) |
| S por hoja | El promedio de los S de cada foto |
| Peores hojas | Las 5 fotos de menor S, con su S, de menor a mayor (los empates, por id) |
| Peores renglones | Los 10 renglones del pool (por `id`) de menor s promedio, entre los que aparecieron en al menos dos fotos, con su texto, su s promedio y cuántas veces aparecieron. Un omitido cuenta s = 0 |
| Elegible | Sí o no, con el motivo si no |
| Errores graves | Omitidos, sobrantes de costo 1 y cantidades lejos sin duda: cantidad y porcentaje sobre los renglones esperados |
| Errores silenciosos | Pares con alguna penalización y sin duda, más los omitidos y los sobrantes de costo 1 |
| Tachados pedidos | Objetivo 0 (RNF-18) |
| Tachados inventados | Tachados leídos (`ImagenLeida.tachados`) que no tienen las palabras de ningún tachado de la revisión. Es una señal: si la foto no se revisó, puede ser un tachado real. No entra a S ni al filtro |
| Señal de faltante | Cuántas veces la señal de la §3.6 acertó, dio una falsa alarma o no detectó una salida con renglones de menos |
| Fotos perfectas | Porcentaje de fotos con todos los pares en s = 1 y sin sobrantes |
| Precision, recall, F1 | Un par es correcto si coinciden producto y cantidad, con o sin duda |
| JSON válido | Porcentaje de salidas con `resultado` |
| Segundos | p50 y p95 por foto |
| USD | Por foto y por cada 1000 renglones esperados, si el modelo tiene `--precio` o es local (USD 0) |
| Cortes | S por tipo de dificultad y por `origen` (sobre los pares y omitidos de ese tipo), y por persona, letra, nitidez, luz y papel (sobre las fotos) |

Cada modelo del resumen lleva la huella de su prompt. Si dos modelos de la corrida tienen huellas distintas,
`puntuar` avisa que no se comparan entre sí, y la tabla muestra los primeros 8 caracteres de cada huella.

El precio va por modelo: `--precio openai:gpt-6-luna=0.10,0.50` (USD por millón de tokens de entrada y salida), y se
puede repetir. Un modelo de API sin precio muestra el USD como desconocido, no como 0.

`puntuar` escribe `corridas/<corrida>/resumen.json` y muestra una tabla con una fila por modelo, nivel y grupo, con
la columna de elegible. Con `--detalle` muestra, por foto, cada esperado con lo que se leyó y su s.

### 3.6 Fotos para revisar y señal de faltante

Cada lista tiene 15 renglones, así que se sabe cuántos tendría que leer el modelo. Eso sirve para dos cosas.

**Fotos para revisar.** Sin revisar cada foto no se sabe si la persona copió todo. Pero si **todos los modelos** de
la corrida (al menos dos) leen menos renglones que los esperados de esa foto, lo más probable es que falten en la
hoja (la persona los salteó o la foto los corta) y no que todos los modelos fallen igual. `puntuar` lista esas fotos
en `para_revisar`, con los esperados y lo que leyó cada modelo. Quien revisa mira solo esas fotos y, si falta algo,
lo anota en `sin_copiar`. Al contar, un renglón leído cuyo texto empieza con «pedido» no cuenta: es el encabezado
leído como renglón.

**Señal de faltante.** En un pedido real no hay lista con la que comparar. La señal usa solo la salida del modelo,
como podría hacerlo el bot: dispara si la transcripción completa (`texto`) tiene más líneas de producto que
`renglones`. Una línea de producto es una línea no vacía de `texto` con al menos una letra y un número; así no cuentan
el encabezado, la fecha ni el membrete del papel. El benchmark sí conoce la verdad, y cuenta:

| | La salida tiene menos renglones que los esperados | La salida tiene todos |
|---|---|---|
| La señal dispara | Detectada | Falsa alarma |
| La señal no dispara | No detectada | Bien |

Las fotos de `para_revisar` no entran a esta cuenta hasta que se revisen: ahí el faltante puede ser de la hoja.

### 3.5 Validación de la scoring

Antes de comparar modelos u optimizar prompts, la scoring se valida contra el criterio de alguien que conoce el
negocio (diseño, §6.9):

1. `python -m internal.evaluar_manuscritos puntuar <corrida> --para-ordenar 15` elige al azar, con semilla, 15 pares
   modelo + foto de `desarrollo` y escribe dos archivos en la carpeta de la corrida:
   - `ordenar.md`: cada par con un id (`O01`…`O15`) en orden al azar, su lista y lo que quedó del nivel (b) para
     cada renglón (producto del catálogo, cantidad y si tiene duda), más los sobrantes. No dice el modelo ni el
     puntaje.
   - `ordenar_clave.json`: a qué modelo y foto corresponde cada id, y su S. No se abre hasta terminar de ordenar.
2. Quien revisa ordena los ids de mejor a peor mirando solo `ordenar.md`.
3. `python -m internal.evaluar_manuscritos validar <corrida> --orden O07,O02,…` compara ese orden con el de S: el
   porcentaje de pares en el mismo orden (dos con el mismo S cuentan como de acuerdo) y los pares invertidos, de
   mayor a menor diferencia de S.

La scoring es **válida** si coinciden al menos el 80 % de los pares y ningún par invertido tiene más de 0,1 de
diferencia en S. Si no, `validar` sale con error y lista los pares para revisar los pesos.

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
  "p03-A": {"sin_copiar": [11], "tachados": [["belkor", "rulos"]], "letra": "dificil", "nitidez": "nitida",
            "notas": "el 11 lo escribió como «2 acond»"},
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
| `letra` | `clara` o `dificil` | sin dato |
| `nitidez` | `nitida` o `borrosa` | sin dato |
| `notas` | Texto libre | — |

### 4.6 `data/benchmark_manuscritos/casos.json`

Compatible con el de `comparar_vision` (`id`, `archivo`, `legible`, `renglones`, `tachados`), así el nivel (a)
también corre con `comparar_vision --casos`. Es una lista con un caso por foto, como en el diseño (§4):

```json
[
  {
    "id": "p03-A", "archivo": "imagenes/p03-A.jpg", "persona": "p03", "hoja": "A", "lista": "L07",
    "grupo": "reservado",
    "condiciones": {"luz": "artificial", "papel": "cuaderno rayado", "letra": "dificil", "nitidez": "nitida"},
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
  "para_revisar": [{"foto": "p02-A", "esperados": 15, "leidos": {"openai:gpt-6-luna": 12, "huggingface:…": 12}}],
  "modelos": [
    {"modelo": "openai:gpt-6-luna", "huella": "3f9a…",
     "niveles": {
       "b": {"desarrollo": {"fotos": 8, "S": 0.84, "S_intervalo": [0.79, 0.88], "fotos_perfectas_pct": 25.0,
                            "precision": 0.8, "recall": 0.8, "f1": 0.8, "errores_silenciosos": 9,
                            "tachados_pedidos": 1, "tachados_inventados": 0, "errores_graves": 2,
                            "errores_graves_pct": 1.7,
                            "senal_de_faltante": {"detectada": 1, "falsa_alarma": 0, "no_detectada": 2, "bien": 5},
                            "elegible": false, "motivo": "pide 1 tachado", "S_por_hoja": 0.85,
                            "peores_hojas": [{"foto": "p03-A", "S": 0.825}],
                            "peores_renglones": [{"id": "N5", "texto": "Silkara protector térmico (7)", "s": 0.4,
                                                  "apariciones": 4}],
                            "json_valido_pct": 100.0, "segundos_p50": 4.1,
                            "segundos_p95": 6.3, "usd_por_foto": 0.0012, "usd_por_1000_renglones": 0.08,
                            "por_dificultad": {"tono": 0.9}, "por_origen": {"nucleo": 0.88},
                            "por_luz": {"natural": 0.86}, "por_papel": {"hoja lisa": 0.85},
                            "por_persona": {"p01": 0.9}, "por_letra": {"clara": 0.9, "dificil": 0.7},
                            "por_nitidez": {"nitida": 0.86}},
             "reservado": {}}}}
  ]
}
```

### 4.9 `data/benchmark_manuscritos/congelado.json`

```json
{"fecha": "2026-12-15", "reservado": ["p02", "p04", "p05", "p07", "p08", "p10"]}
```

Lo escribe `casos --congelar`. Mientras exista, las personas de `reservado` siguen ahí y toda persona que no está en
`casos.json` va a `desarrollo`.

### 4.10 `ordenar.md` y `ordenar_clave.json`

En la carpeta de la corrida (§3.5). La clave:

```json
{"semilla": 145, "nivel": "b", "items": {"O01": {"modelo": "openai:gpt-6-luna", "foto": "p03-A", "S": 0.825}}}
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
| RF-135 | Cada salida guarda la huella del prompt de visión y de los topes de tokens; si no coincide con la de las salidas ya guardadas de ese modelo en la corrida, `correr` se niega a seguir |
| RF-136 | `correr` corre solo el grupo `desarrollo` salvo que se pida `--grupo reservado` o `todos` |
| RF-137 | El nivel (a) empareja por las `palabras` del esperado que no tienen números, con distancia de edición 1 en las marcas del catálogo y también sin espacios ni guiones, uno a uno y en el orden de la hoja; entre los que corresponden prefiere el que también tiene las palabras con números |
| RF-138 | El nivel (b) empareja por SKU con la búsqueda contra `catalogo_evaluacion.csv`, sin red; `varias` y `aproximado` emparejan si el SKU esperado está entre los candidatos, y cuentan como duda |
| RF-139 | El puntaje de un par es 0,8 con duda o sin cantidad, y si no máx(0,1; 1 − Σ penalizaciones), con 0,3 por variante (tono, y en (a) la medida), 0,4 por cantidad cerca y 0,6 por cantidad lejos; el de una foto es Σ s / (esperados + Σ costo de los sobrantes), y una salida sin `resultado` da S = 0 |
| RF-140 | `puntuar` reporta por modelo, nivel y grupo el S micro con intervalo por bootstrap sobre fotos, fotos perfectas, precision, recall y F1, errores silenciosos, tachados pedidos, JSON válido, segundos p50 y p95, USD con precio por modelo y los cortes por dificultad, origen, luz y papel; las mismas salidas dan el mismo resumen |
| RF-141 | La cantidad está lejos si la mayor es el doble o más de la menor, si una es 0 o si la clase de unidad (cajas contra el resto) no coincide; si no coincide sin estar lejos, está cerca |
| RF-142 | Un sobrante cuesta 1 en el denominador si es un tachado o es silencioso, y 0,2 si es dudoso (con duda o, en (b), con la búsqueda en `varias`, `aproximado` o `no_encontrado`) |
| RF-143 | Los errores graves son los omitidos, los sobrantes de costo 1 y los pares con cantidad lejos sin duda; una configuración es elegible si no pide tachados y sus errores graves no pasan del 2 % de los renglones esperados |
| RF-144 | El resumen suma el S por hoja, las 5 peores hojas, los 10 peores renglones del pool con al menos dos apariciones, los cortes por persona, letra y nitidez, y la huella de cada modelo; avisa si dos modelos de la corrida tienen huellas distintas |
| RF-145 | `puntuar --para-ordenar N` escribe N pares modelo + foto de `desarrollo`, al azar con semilla, sin el modelo ni el puntaje, y su clave aparte |
| RF-146 | `validar --orden` da el porcentaje de pares en el mismo orden que S y los invertidos; la scoring es válida con al menos 80 % y ningún invertido de más de 0,1 de S, y si no sale con error |
| RF-147 | `casos --congelar` fija las personas del reservado; con el reservado congelado, toda persona nueva va a `desarrollo`. La letra y la nitidez de la revisión pasan a `condiciones`, y un valor que no es de la §4.5 es un problema de esa foto |
| RF-148 | El modelo local de visión (`vision_local`) genera hasta 20.000 tokens de salida, para que una hoja de 15 renglones no corte el JSON por la mitad, y no lee una imagen cuya entrada pase de 100.000 tokens: devuelve la lectura con el error, sin generar. Cambia también la lectura del bot, no solo el benchmark |
| RF-149 | `puntuar` lista en `para_revisar` las fotos en que todos los modelos de la corrida, si son al menos dos, leen menos renglones que los esperados, sin contar un renglón leído que empieza con «pedido» |
| RF-150 | Un renglón omitido cuenta como error silencioso y como error grave |
| RF-151 | El resumen cuenta los tachados inventados: tachados leídos que no tienen las palabras de ningún tachado de la revisión; no entran a S ni al filtro |
| RF-152 | El resumen cuenta cuántas veces la señal de faltante (más líneas de producto en `texto` que `renglones`) detectó, dio una falsa alarma o no detectó una salida con renglones de menos, sin las fotos de `para_revisar` |

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
| CA-230 | Una corrida con salidas de una huella y otras `INSTRUCCIONES`, o con otro tope de tokens de salida | Se corre otra vez | Se niega, no llama al modelo y pide otra `--corrida` | `tests/test_evaluar_manuscritos.py::test_correr_con_otro_prompt_se_niega`, `test_correr_con_otro_tope_de_tokens_se_niega` |
| CA-231 | Casos de los dos grupos | Se corre sin `--grupo` | Solo llama con las fotos de `desarrollo` | `tests/test_evaluar_manuscritos.py::test_correr_solo_desarrollo` |
| CA-232 | Un esperado «keratina nordika 900» × 2 y los leídos «2 keratna Nordika 900» y «2 sh keratina Nordica x300»; aparte, los mismos más «2 keratina Nordika 900» al final | Se puntúa el nivel (a) | En el primero se empareja con «Nordica x300» (una letra en la marca) con la medida mal, s = 0,7, y «keratna» sobra; en el segundo se prefiere el que tiene la medida, s = 1, y sobran los otros dos | `tests/test_evaluar_manuscritos.py::test_nivel_a_empareja_por_palabras` |
| CA-233 | Leídos que la búsqueda deja `unico`, `varias` con el SKU esperado entre los candidatos, `aproximado` y `no_encontrado` contra `catalogo_evaluacion.csv` | Se puntúa el nivel (b) | El `unico` empareja sin duda, `varias` y `aproximado` emparejan con duda y el `no_encontrado` queda sobrante con su esperado omitido | `tests/test_evaluar_manuscritos.py::test_nivel_b_empareja_por_sku` |
| CA-234 | La hoja `p03-A` del diseño (§6.10): 12 renglones bien, N5 con «1» por «7», uno con duda, uno omitido y un tachado pedido | Se puntúa | S = 13,2 / 16 = 0,825; precision y recall 0,80; errores silenciosos 3; errores graves 3; tachados pedidos 1; no es perfecta ni elegible | `tests/test_evaluar_manuscritos.py::test_ejemplo_del_diseno` |
| CA-235 | Una salida sin `resultado` para una foto legible de 15 renglones, y otra sin `resultado` ni `crudo` (el modelo no contestó) | Se puntúa | La primera da S = 0, 15 omitidos, y JSON válido baja; la segunda no entra al puntaje y se avisa que hay que `--reintentar` | `tests/test_evaluar_manuscritos.py::test_json_invalido_da_cero`, `test_sin_respuesta_no_cuenta` |
| CA-236 | Un par con tono «7,1» leído y «7.1» esperado, otro con `cajas` leído y sin unidad esperada, y otro con `cada_uno` esperado y sin unidad leída | Se puntúa | El primero da s = 1, el segundo s = 0,4 (cantidad lejos) y el tercero s = 1 | `tests/test_evaluar_manuscritos.py::test_detalle_normalizado` |
| CA-237 | Una corrida con dos modelos, uno de API con precio y otro local, y fotos de los dos grupos | Se puntúa dos veces | Las dos dan el mismo `resumen.json`, con desarrollo y reservado separados, el local con USD 0, el de API con su USD, y los cortes por dificultad, origen, luz y papel | `tests/test_evaluar_manuscritos.py::test_resumen_por_modelo_y_grupo` |
| CA-238 | Una corrida guardada | Se puntúa con la red cortada y sin claves | Termina sin llamar a ningún modelo | `tests/test_evaluar_manuscritos.py::test_puntuar_sin_red` |
| CA-239 | Pares sin duda con cantidad 3 por 2, 5 por 6, 20 por 2, 1 por 10 y 1 por 2; uno con el tono mal y cantidad 3 por 2; otro con el tono mal y 20 por 2; otro sin cantidad | Se puntúa | 0,6; 0,6; 0,4; 0,4; 0,4; 0,3; 0,1 y 0,8 | `tests/test_evaluar_manuscritos.py::test_penalizaciones_de_cantidad_y_variante` |
| CA-240 | Sobrantes: uno sin duda, uno con duda, uno que en (b) queda `no_encontrado` y un tachado con duda | Se puntúa | Suman 1, 0,2, 0,2 y 1 al denominador, y el tachado cuenta como tachado pedido | `tests/test_evaluar_manuscritos.py::test_costo_de_los_sobrantes` |
| CA-241 | Las salidas de la tabla de casos ambiguos del diseño (§6.5) para «2 sh keratina Nordika x900», contra `catalogo_evaluacion.csv` | Se puntúan los dos niveles | Cada fila da el puntaje de la tabla, en (a) y en (b) | `tests/test_evaluar_manuscritos.py::test_casos_ambiguos_del_diseno` |
| CA-242 | Tres configuraciones en el reservado, nivel (b): una que pide un tachado, una con 3 errores graves en 100 renglones y otra con 2 | Se resume | La primera y la segunda no son elegibles, con su motivo; la tercera sí | `tests/test_evaluar_manuscritos.py::test_filtro_de_elegibilidad` |
| CA-243 | Seis fotos de tres personas, con letra y nitidez anotadas, y un renglón del pool que aparece en tres | Se resume | Trae el S por hoja, las 5 peores hojas de menor a mayor, el renglón entre los peores con sus tres apariciones y los cortes por persona, letra y nitidez | `tests/test_evaluar_manuscritos.py::test_cortes_y_peores` |
| CA-244 | Una corrida con 20 fotos de desarrollo y 10 de reservado, de dos modelos | Se pide `--para-ordenar 15` dos veces | Las dos veces los mismos 15 pares, solo de desarrollo, con ids `O01`…`O15`; `ordenar.md` no nombra ningún modelo ni trae ningún S, y la clave sí | `tests/test_evaluar_manuscritos.py::test_para_ordenar` |
| CA-245 | La clave de cinco pares con S 0,9, 0,5, 0,45, 0,4 y 0,35 | Se valida el orden de S, uno con los dos primeros cambiados, uno con los dos últimos cambiados y uno al que le falta un id | El primero da 100 % y es válido; el segundo coincide en 90 % pero no es válido, por el par invertido de 0,4; el tercero coincide en 90 % y es válido (0,05 de diferencia); el cuarto es un error que nombra el id que falta | `tests/test_evaluar_manuscritos.py::test_validar_orden` |
| CA-246 | Un `casos.json` con diez personas y el reservado congelado | Se suman tres personas nuevas y se arman los casos | Las tres van a `desarrollo` y las del reservado siguen ahí | `tests/test_benchmark_manuscritos.py::test_reservado_congelado` |
| CA-247 | Una corrida con dos modelos cuyas salidas tienen huellas distintas | Se puntúa | El resumen trae la huella de cada uno y avisa que no se comparan entre sí | `tests/test_evaluar_manuscritos.py::test_huellas_distintas_se_avisan` |
| CA-248 | Una revisión con `letra: dificil` y `nitidez: nitida` en una foto, nada en otra y `letra: regular` en una tercera | Se arman los casos | La primera trae esos valores en `condiciones`, la segunda «sin dato», y la tercera es un problema que nombra su foto | `tests/test_benchmark_manuscritos.py::test_letra_y_nitidez` |
| CA-249 | El modelo local con una entrada de 3.900 tokens y otra de 100.001 | Se lee la imagen | La primera genera con `max_new_tokens=20000`; la segunda devuelve la lectura con el error y no genera | `tests/test_vision.py::test_topes_de_tokens_del_modelo_local` |
| CA-250 | `VISION_MARCAS=true` y un catálogo de producción con otra marca | Se corre una foto | El prompt que recibe el modelo trae las marcas de `catalogo_evaluacion.csv` y no la de producción, y el catálogo de producción vuelve a su lugar al terminar | `tests/test_evaluar_manuscritos.py::test_correr_manda_las_marcas_del_catalogo_de_evaluacion` |
| CA-251 | Una corrida con dos modelos: en una foto los dos leen 12 de 15 (uno, además, el encabezado «Pedido para Norma» como renglón) y en otra uno lee 15; aparte, una corrida con un solo modelo que lee 12 | Se puntúa | La primera foto está en `para_revisar` con 15 esperados y 12 leídos por cada modelo; la segunda no; con un solo modelo no hay fotos para revisar | `tests/test_evaluar_manuscritos.py::test_fotos_para_revisar` |
| CA-252 | Una hoja de 15 esperados con 14 renglones bien leídos y uno que no aparece | Se resume | Errores silenciosos 1, errores graves 1 (6,7 %), y no es elegible | `tests/test_evaluar_manuscritos.py::test_omitido_es_error_grave` |
| CA-253 | Una foto sin tachados en la revisión con 3 tachados leídos, y otra con un tachado en la revisión que el modelo lee | Se resume | 3 tachados inventados en la primera y 0 en la segunda; S y el filtro no cambian | `tests/test_evaluar_manuscritos.py::test_tachados_inventados` |
| CA-254 | Salidas con 15 líneas de producto en `texto` y 12 renglones, con 15 y 15, con 14 y 14 en una hoja de 15, y con encabezado y fecha sin números | Se cuenta la señal | Detectada, bien, no detectada, y el encabezado y la fecha no cuentan como líneas de producto | `tests/test_evaluar_manuscritos.py::test_senal_de_faltante` |

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
- Una hoja de 15 renglones leída con el modelo local: con el tope de salida de antes (1024 tokens) el JSON salía
  cortado a la mitad e inválido. Con RF-148 entra entero; puede tardar varios minutos por foto en una Mac.
- Una foto en `para_revisar` que, al mirarla, tiene todos los renglones: los modelos fallaron igual. Se deja como
  está y el omitido cuenta.
- Qwen copia en `tachados` casi todos los renglones de la hoja, además de ponerlos en `renglones` (primera prueba):
  lo mide `tachados_inventados`.
- Una corrida cortada por el tope de llamadas: `puntuar` puntúa lo que hay y avisa cuántas fotos de cada modelo no
  tienen salida; esas no entran al S de ese modelo, así que dos modelos con distinta cantidad de fotos no se
  comparan sin avisar.
- `puntuar` con salidas de una foto que ya no está en `casos.json` (se descartó después): las ignora y lo avisa.
- `--para-ordenar N` con menos de N pares en desarrollo: usa los que hay y lo avisa.
- `validar` con un id que falta, uno repetido o uno que no está en la clave: es un error que nombra cada id, y no da
  ningún porcentaje.
- `casos --congelar` sin `casos.json`, o sin nadie en el reservado: falla, porque no hay nada que congelar.
- Una persona que ya estaba en el reservado congelado y vuelve con una hoja nueva: la hoja nueva va al reservado,
  con su persona. Las dos hojas de una persona nunca se separan.

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
- **Los pesos salen de una regla: una pregunta al cliente cuesta 0,2, y un error silencioso cuesta más.** Si un
  error silencioso valiera más que una duda (0,8), al modelo le convendría equivocarse callado. Por eso la variante
  cuesta 0,3 y no 0,1, como en la primera versión, en la que un tono mal sin avisar (0,9) ganaba a preguntar.
- **La unidad es cantidad, no detalle.** «2 cajas» leído «2» puede ser 24 contra 2: es cantidad lejos.
- **La cantidad se puntúa por distancia.** «1» por «10» (un dígito perdido) cuesta más que 3 por 2. El corte en el
  doble es simple de explicar y separa el desliz del error que obliga a devolver mercadería.
- **Un sobrante dudoso cuesta como una pregunta.** El agente pregunta antes de pedirlo, así que no se manda: cuesta
  0,2, no 1. Lo tachado cuesta 1 aunque tenga duda: no se tendría que ni preguntar.
- **En (a) la medida es una variante; en (b) es otro SKU.** Leer «x300» por «x900» es un número mal leído (lectura),
  pero lleva a otro producto (interpretación). Cada nivel lo puntúa por lo que mide.
- **Un filtro, no un veto por hoja.** Poner en cero la hoja entera por un error grave deja un número ruidoso con
  pocas hojas y le saca señal al optimizador. Los errores graves se cuentan aparte y deciden si una configuración se
  puede elegir.
- **Un omitido es un error grave.** En un pedido real nadie avisa que faltó un renglón: es tan silencioso como mandar
  otro producto. Antes bajaba el S pero no contaba para el filtro, así que un modelo podía saltearse renglones y
  seguir siendo elegible.
- **Las fotos para revisar salen del acuerdo entre modelos.** No revisar cada foto es más barato, pero si dos modelos
  distintos leen los mismos renglones de menos, probablemente el problema es la hoja. Con un solo modelo no se puede
  saber, y no se marca nada.
- **La señal de faltante es de un experimento, no del bot.** El benchmark sabe cuántos renglones hay; el bot no. Por
  eso la señal usa solo la salida del modelo, y el benchmark mide si sirve antes de que se proponga para el bot.
- **La scoring se valida antes de usarla**, contra el orden de alguien que conoce el negocio (§3.5), y se valida en
  desarrollo: hacerlo en el reservado sería mirarlo.
- **El reservado se congela.** Sin congelarlo, cada foto nueva podría ir a parar al reservado después de que ya se
  midió con él, y el número final dejaría de ser comparable.
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
7. ~~RF-137 a RF-147 y CA-239 a CA-248 en esta spec, con CA-232, CA-234 y CA-236 actualizados a la scoring nueva.~~
8. ~~Adaptar `evaluar_manuscritos` y `benchmark_manuscritos` a la scoring nueva, con sus tests.~~
9. ~~RF-149 a RF-152 y CA-251 a CA-254 en esta spec, con CA-234 actualizado (los omitidos cuentan como errores).~~
10. ~~Implementarlos en `evaluar_manuscritos`, con sus tests.~~
11. Validar la scoring con el orden a mano (§3.5) sobre desarrollo.
12. Con las fotos: armar `casos.json`, correr Qwen local sobre `desarrollo` y, con permiso de quien paga la clave,
   `gpt-6-luna` y GPT Terra con `--max-llamadas`. El reservado se corre al final, una vez, para medir.
