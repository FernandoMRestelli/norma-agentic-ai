# Datos de ejemplo

Datos **inventados** para desarrollar y testear sin acceso a las planillas reales.
El repositorio es público: acá nunca van clientes, teléfonos, precios ni stock reales.

| Archivo | Representa | Uso |
|---|---|---|
| `catalogo.csv` | Google Sheet 1: catálogo, precios y stock | Las columnas son el formato que debe tener la planilla real |
| `pedidos.csv` | Google Sheet 2: seguimiento de pedidos (una fila por pedido) | Las columnas son el formato que debe tener la planilla real |
| `mensajes.json` | Mensajes de clientes con el resultado esperado del agente | Casos de prueba para la interpretación |
| `catalogo_evaluacion.csv` | Un catálogo mayorista de unos 400 productos, con el formato del real | Catálogo de los casos de `pedidos_evaluacion.json` |
| `pedidos_evaluacion.json` | Pedidos largos con el formato de los reales y su resultado esperado | Evaluación por línea del agente (RF-59, RF-60) |

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

Las dos últimas columnas se sumaron después de la 0.1.1 y van al final, para que un archivo viejo siga alineado.

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

Los casos con `previo` tienen dos turnos: primero ese mensaje, que recibe una propuesta, y después `mensaje`.
Se corren con el LLM real con `python -m internal.evaluar_agente`.

Por ahora solo hay mensajes de texto; faltan ejemplos de audio e imagen.

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

Los tests de `tests/test_datos_ejemplo.py` verifican que estos archivos respeten
los esquemas y sean coherentes entre sí (por ejemplo, que todo SKU exista en el catálogo).
