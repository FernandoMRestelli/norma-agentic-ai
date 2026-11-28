# Datos de ejemplo

Datos **inventados** para desarrollar y testear sin acceso a las planillas reales.
El repositorio es público: acá nunca van clientes, teléfonos, precios ni stock reales.

| Archivo | Representa | Uso |
|---|---|---|
| `catalogo.csv` | Google Sheet 1: catálogo, precios y stock | Las columnas son el formato que debe tener la planilla real |
| `pedidos.csv` | Google Sheet 2: seguimiento de pedidos (una fila por pedido) | Las columnas son el formato que debe tener la planilla real |
| `pedidos_lineas.csv` | Las líneas de esos pedidos (una fila por producto de cada pedido) | Lo que usa quien prepara el pedido |
| `mensajes.json` | Mensajes de clientes con el resultado esperado del agente | Casos de prueba para la interpretación |
| `catalogo_evaluacion.csv` | Un catálogo mayorista de unos 400 productos, con el formato del real | Catálogo de los casos de `pedidos_evaluacion.json` |
| `pedidos_evaluacion.json` | Pedidos largos con el formato de los reales y su resultado esperado | Evaluación por línea del agente (RF-59, RF-60) |
| `benchmark_manuscritos/` | El pool de renglones, las listas sorteadas y el script del Google Form del benchmark de pedidos escritos a mano | Ver [la spec](../docs/spec/benchmark-manuscritos.md) (RF-126 a RF-129) |

## `catalogo.csv`

| Columna | Campo de `Product` | Notas |
|---|---|---|
| `sku` | `sku` | Único |
| `nombre` | `name` | |
| `marca` | `brand` | |
| `presentacion` | `presentation` | Ej.: `400 ml`, `90 g`, `kit` |
| `precio` | `price` | Punto como separador decimal |
| `stock` | `stock` | Unidades disponibles |
| `stock_minimo` | `min_stock` | Debajo de este valor el dashboard lo marca como stock bajo |

Incluye casos difíciles a propósito: Pantene en 200 y 400 ml y como shampoo o
acondicionador, varios tonos de Koleston, un producto sin stock (`TIN-KOL-71`) y
otros con stock bajo (`CE-SED-340`, `ALG-EST-140`).

## `pedidos.csv`

| Columna | Campo de `Order` | Notas |
|---|---|---|
| `id_orden` | `order_id` | Único |
| `fecha_hora` | `created_at` | ISO 8601 con zona horaria |
| `cliente` | `customer_id` | |
| `canal` | `channel` | `web` o `whatsapp` |
| `productos` | `items` | `cantidad x nombre presentación (SKU)`, y ` [nota]` si la línea tiene nota; separados por `;` |
| `notas` | `notes` | Observación general del pedido |
| `estado` | `status` | `Recibido`, `Preparado`, `Retirado` o `Cancelado` |
| `nombre_cliente` | `customer_name` | Nombre del perfil del cliente, si el canal lo da |
| `total` | `total` | Suma de cantidad por precio, con punto y dos decimales |

| `lineas` | | Cantidad de líneas del pedido |
| `unidades` | | Suma de las cantidades |
| `fuera_del_pedido` | `leftovers` | Lo que el cliente pidió y no entró, con el motivo entre paréntesis; separados por `;` |
| `origen` | `source` | `texto`, `audio` o `imagen` |
| `draft_id` | `draft_id` | Propuesta de la que salió el pedido |
| `actualizado` | | Fecha y hora del último cambio de estado; sin cambios, la del alta |

Las columnas desde `nombre_cliente` se sumaron después de la 0.1.1 y van al final, para que un archivo viejo siga
alineado: al escribir el primer pedido nuevo, el sistema completa el encabezado. Un texto que empieza con `=`, `+`,
`-` o `@` se guarda con un apóstrofo adelante, para que la planilla no lo tome como una fórmula.

## `pedidos_lineas.csv`

Una fila por línea de cada pedido de `pedidos.csv`. Se escribe al registrar el pedido y no cambia después.

| Columna | Campo de `OrderItem` | Notas |
|---|---|---|
| `id_orden` | | El pedido al que pertenece |
| `renglon` | | Posición de la línea en el pedido, desde 1 |
| `sku`, `producto`, `presentacion`, `marca`, `categoria` | `sku`, `product_name`, `presentation`, `brand`, `category` | Del catálogo, al momento de registrar |
| `cantidad` | `quantity` | |
| `precio_unitario`, `subtotal` | `unit_price` | Con punto y dos decimales, sin signo |
| `nota` | `note` | Tono, variante u observación de la línea |
| `coincidencia` | `match` | `exacta`, o `aproximada` si el cliente tuvo que confirmar el producto |
| `pedido_como` | `source_text` | El renglón como lo escribió el cliente, cuando el pedido llegó como lista |

## `mensajes.json`

Cada caso tiene el `mensaje` (un `IncomingMessage`) y lo que el agente debería
hacer en `esperado.resultado`:

- `borrador`: arma el borrador con los `items` indicados.
- `aclaracion`: pregunta antes de armar el borrador; `opciones` son los SKU posibles.
- `sin_stock`: avisa que no alcanza el stock y ofrece lo disponible o una alternativa.
- `fuera_de_catalogo`: avisa que no tiene el producto, sin inventar un SKU.
- `informacion`: responde sin proponer nada, por ejemplo a un saludo.
- `pedido`: registra el pedido con los `items` indicados, porque el cliente aceptó la propuesta.
- `sin_pedido`: no registra nada, porque el cliente dudó o rechazó la propuesta.
- `derivada`: pasa la conversación a una persona del equipo.

Además, `esperado` puede exigir (RF-115): `kinds`, los tipos de respuesta que valen; `propuesta_final`, qué propuesta
tiene que quedar vigente (una lista vacía: ninguna), y `dice` y `no_dice`, expresiones regulares que el
mensaje tiene que tener y que no puede tener. Con un pedido numerado, `motivos` dice con qué motivo tiene que quedar
afuera cada renglón, escrito como en el mensaje (RF-157): `{"1 caja de tornillos": "fuera_del_rubro"}`.

Los casos con `previo` tienen dos turnos: primero ese mensaje, que recibe una propuesta, y después `mensaje`.
Se corren con el LLM real con `python -m internal.evaluar_agente`.

Por ahora solo hay mensajes de texto; faltan ejemplos de audio. Las imágenes de pedidos ficticias, para comparar
modelos de visión, están en `tests/fixtures/imagenes_pedidos/`: el hook de pre-commit solo acepta multimedia dentro
de `tests/`.

## `catalogo_evaluacion.csv` y `pedidos_evaluacion.json`

El catálogo de 15 productos alcanza para probar un mensaje, no un pedido real. Estos dos archivos son el conjunto
`pedidos` de la evaluación ([spec](../docs/spec/toma-de-pedidos-por-texto.md), M1):

- **`catalogo_evaluacion.csv`** tiene 12 marcas inventadas y copia las trampas del catálogo real: la medida va
  dentro del nombre (`X 900 ML`, `X 1 KG`), la columna `presentacion` está vacía, un mismo producto viene en dos o
  tres medidas, la coloración no trae el tono y no todas las marcas trabajan todas las categorías. El SKU se arma
  con marca, línea, variante y medida (`NOR-SH-KER-900`). Lo genera `catalogo_mayorista()` de
  `tests/datos_sinteticos.py`; para regenerarlo: `python -m tests.datos_sinteticos > data_example/catalogo_evaluacion.csv`.
- **`pedidos_evaluacion.json`** tiene listas agrupadas por marca de hasta 30 renglones, con abreviaturas, errores
  de tipeo, coloración en `tono/cantidad`, productos que no están, dudas de medida, stock corto y pedidos en varios
  mensajes. Usa el formato de `mensajes.json` y suma en `esperado`:

  | Campo | Qué dice |
  |---|---|
  | `items` | Las líneas que tiene que tener la propuesta o el pedido. Un SKU puede repetirse, con una `nota` por línea (los tonos de una coloración): se compara la suma |
  | `propuesta` | Solo con `sin_stock`: lo que hay que proponer. Ahí `items` es lo que pidió el cliente, que no alcanza |
  | `dudas` | Renglones que el agente tiene que preguntar, con las `opciones` posibles |
  | `afuera` | Renglones que no se pueden armar: fuera de catálogo o agotados |

Se corre con `python -m internal.evaluar_agente --set pedidos`. La corrida reporta recall y precisión por línea,
líneas perdidas, registros indebidos y aclaraciones de más, y la compara con el umbral de release (RNF-15). Con
`--repeticiones 3` corre cada caso tres veces y muestra cuántas acertó.

## `adversariales.json`

Casos hechos para que el agente se equivoque, contra `catalogo_evaluacion.csv` (RF-116): aceptaciones con trampa
(`tema` `aceptacion`), pedidos sin datos suficientes (`evidencia`), intentos de cambiar las reglas (`inyeccion`) y
consultas que no se pueden contestar (`negocio`). El tema `rubro` mide lo que no está en el catálogo (RF-157):
comida o ferretería, del rubro pero sin cargar, y stock 0. Se corren con `--set adversariales`, y con `--tema` uno solo.

`grupo` separa los de `desarrollo`, que se miran para ajustar prompts, de los `reservado`, que solo miden: si se ajusta
algo mirando un caso reservado, pasa a `desarrollo` y se escribe otro (D-50). Los del tema `rubro` salen del
generador ([abajo](#grupos-y-casos-reservados)).

## `consultas_busqueda.json`

124 consultas para medir el buscador solo, sin LLM (RF-119), contra `catalogo_evaluacion.csv`. `relevantes` son los SKU
que responden la consulta, con comodines (`*-MA-ARG-1K`); vacío si el catálogo no tiene respuesta. Los `tipo` cubren
desde nombres exactos hasta abreviaturas, errores, pedidos ambiguos, necesidades («algo para rulos») y cosas que no
hay, y `grupo` funciona como en los adversariales. Se corre con `python -m internal.evaluar_busqueda --fallas`.

## Grupos y casos reservados

Los cuatro conjuntos de casos tienen un `grupo`. Los de `desarrollo` se miran para ajustar prompts; los `reservado`
solo miden, y el umbral de release (RNF-15) se calcula con ellos (D-50, M8-1 de
[mejoras-clase8](../docs/spec/mejoras-clase8.md)). Si se ajusta algo mirando un caso reservado, deja de medir:

- en `consultas_busqueda.json` y en `adversariales.json` (fuera del tema `rubro`), pasa a `desarrollo` y se escribe
  otro;
- en `mensajes.json`, `pedidos_evaluacion.json` y el tema `rubro` de `adversariales.json`, los reservados salen de
  `tests/casos_reservados.py`: se cambia
  `SEMILLA_RESERVADOS` y se regeneran todos con `python -m tests.casos_reservados`, que deja igual los de desarrollo.
  Un test exige que el archivo sea la salida del generador, así que no se editan a mano.

**Los ejemplos de los prompts no salen de los casos.** Un ejemplo en `internal/agent/*.md` o en `internal/skills/`
usa marcas inventadas que no están en ningún catálogo (Corvana, Tessaly…) y medidas, tonos y frases que no aparecen
en los casos. `tests/test_contaminacion.py` falla si un caso reservado comparte tres palabras seguidas con un prompt,
una skill o una BR, o si se parece demasiado a uno de desarrollo (D-M8-1). Si falla, se cambia el ejemplo o el caso,
nunca el control.

Los tests de `tests/test_datos_ejemplo.py` verifican que estos archivos respeten
los esquemas y sean coherentes entre sí (por ejemplo, que todo SKU exista en el catálogo).
