# Datos de ejemplo

Datos **inventados** para desarrollar y testear sin acceso a las planillas reales.
El repositorio es público: acá nunca van clientes, teléfonos, precios ni stock reales.

| Archivo | Representa | Uso |
|---|---|---|
| `catalogo.csv` | Google Sheet 1: catálogo, precios y stock | Las columnas son el formato que debe tener la planilla real |
| `pedidos.csv` | Google Sheet 2: seguimiento de pedidos (una fila por pedido) | Las columnas son el formato que debe tener la planilla real |
| `mensajes.json` | Mensajes de clientes con el resultado esperado del agente | Casos de prueba para la interpretación |

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
| `productos` | `items` | `cantidad x nombre presentación (SKU)`, separados por `;` |
| `notas` | `notes` | |
| `estado` | `status` | `Recibido`, `Preparado`, `Retirado` o `Cancelado` |

## `mensajes.json`

Cada caso tiene el `mensaje` (un `IncomingMessage`) y lo que el agente debería
hacer en `esperado.resultado`:

- `borrador`: arma el borrador con los `items` indicados.
- `aclaracion`: pregunta antes de armar el borrador; `opciones` son los SKU posibles.
- `sin_stock`: avisa que no alcanza el stock y ofrece lo disponible o una alternativa.
- `fuera_de_catalogo`: avisa que no tiene el producto, sin inventar un SKU.

Por ahora solo hay mensajes de texto; faltan ejemplos de audio e imagen.

Los tests de `tests/test_datos_ejemplo.py` verifican que estos archivos respeten
los esquemas y sean coherentes entre sí (por ejemplo, que todo SKU exista en el catálogo).
