# Spec: Listas y formulario del benchmark de pedidos escritos a mano

> Cómo se generan las listas que copian a mano los participantes del benchmark y el Google Form con el que se juntan
> las fotos. El diseño completo del benchmark (niveles, scoring, separación) está en
> [`docs/benchmark-pedidos-manuscritos.md`](../benchmark-pedidos-manuscritos.md). Ver
> [cómo trabajamos con SDD](README.md#1-cómo-trabajamos-con-sdd).

| | |
|---|---|
| Etapa del roadmap | 03 · Pedidos multimodales |
| Estado | Implementado (listas y Form); falta armar `casos.json` desde las respuestas y el runner del nivel (b) |
| Responsable | Federico Picado |
| Requisitos relacionados | RF-101 a RF-110 y RNF-18 ([lectura de imágenes](lectura-de-imagenes.md)) |
| IDs que reserva | RF-126 a RF-129, CA-218 a CA-223 |

## 1. Objetivo

Que la respuesta correcta de cada foto del benchmark se conozca de antemano y salga de una planilla versionada, sin
transcribir nada. Cada participante copia a mano una lista numerada, ya sorteada, que ve en un Google Form, y sube
ahí la foto. Para el puntaje, lo único que importa de su respuesta es el número de lista.

## 2. Alcance

| Dentro | Fuera |
|---|---|
| El pool de renglones tipeados (`pool.csv`) | Armar `casos.json` desde las respuestas del Form (próxima PR) |
| El sorteo de las listas con semilla (`listas.json`) | El runner del nivel (b) y su puntaje |
| El Google Apps Script que crea el Form (`crear_form.gs`) | La pregunta de subir la foto: Apps Script no puede crearla y se agrega a mano |

## 3. Comportamiento

```
pool.csv ──► python -m internal.benchmark_manuscritos listas ──► listas.json
listas.json ──► python -m internal.benchmark_manuscritos form ──► crear_form.gs ──► script.google.com ──► Form
```

1. **Pool.** Cada fila es un renglón tal como se copia («Kapris tintura 7.1 x3»), con su respuesta: SKU de
   `catalogo_evaluacion.csv`, cantidad, tono y unidad si los tiene, las `palabras` del nivel (a) y sus tipos de
   dificultad. Las filas `nucleo` (N1 a N5) van en todas las listas; las `pool` (unas 100) se sortean.
2. **Listas.** Cada lista trae el núcleo en los renglones 1 a 5, en orden, y después 10 renglones sorteados del pool
   más uno para tachar, mezclados. El sorteo es estratificado: primero cubre cada tipo de dificultad que todavía no
   tiene la parte variable y después completa al azar. En una lista no se repite un SKU, tampoco con el tachado. Cada
   lista tiene un encabezado inventado (negocio y fecha). La misma semilla da las mismas listas.
3. **Form.** El script crea un Form con: una sección de inicio con las instrucciones, la casilla de consentimiento y
   el desplegable «¿Qué número de lista te tocó?», que lleva a la sección de esa lista; una sección por lista, solo
   con el texto a copiar, que sigue a la sección final; y la sección final con el control de lo copiado y las
   condiciones de la foto. No pide nombre ni mail y no recolecta mails.
4. **A mano**, después de correr el script: agregar en la sección final la pregunta «Subir archivo» (solo imágenes,
   un archivo, obligatoria). El script lo recuerda en el log, junto con el link para editar y el link para mandar.

Tipos de dificultad que tiene que cubrir la parte variable de cada lista: `abreviatura`, `x_medida`,
`numero_en_producto`, `cantidad_en_palabras`, `cantidad_al_final`, `numeros_confusos`, `tono`, `unidad` y
`casi_iguales`.

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
| `dificultad` | Los tipos de la §3, separados por espacio |

### 4.2 `data_example/benchmark_manuscritos/listas.json`

```json
{
  "semilla": 116,
  "listas": [
    {
      "id": "L01",
      "encabezado": {"cliente": "Peluquería Los Álamos", "fecha": "12/11"},
      "renglones": [
        {"n": 1, "id": "N1", "origen": "nucleo", "texto": "2 sh keratina Nordika x900", "sku": "NOR-SH-KER-900",
         "cantidad": 2, "tono": null, "unidad": null, "palabras": ["keratina", "nordika", "900"],
         "dificultad": ["abreviatura", "x_medida", "casi_iguales"]}
      ],
      "tachado": {"n": 9, "id": "P008", "texto": "2 Belkor crema de peinar rulos", "palabras": ["belkor", "rulos"]}
    }
  ]
}
```

`n` es la posición en la hoja (1 a 16); `renglones` son los 15 esperados y `tachado` ocupa la posición que falta.
Los campos de cada renglón son los de `casos.json` del diseño (§4), así armar los casos es copiar la lista.

### 4.3 `data_example/benchmark_manuscritos/crear_form.gs`

Un Google Apps Script con una función `crearForm()` y los textos y las listas embebidos como JSON. Sale solo en
ASCII (los acentos van como `\uXXXX`): el portapapeles de macOS y algunos editores leen el archivo como Mac Roman y
rompen los acentos al copiarlo («Copiá» → «Copi√°»). Se pega en un proyecto nuevo
de script.google.com y se corre una vez con la cuenta que va a recibir las fotos.

## 5. Requisitos

| ID | Requisito |
|---|---|
| RF-126 | El pool tiene 5 renglones de núcleo y al menos 100 sorteables, cada uno con un SKU de `catalogo_evaluacion.csv`, una cantidad mayor que cero y al menos 5 renglones sorteables por cada tipo de dificultad |
| RF-127 | Cada lista tiene el núcleo en los renglones 1 a 5, 10 renglones sorteados que cubren todos los tipos de dificultad, un renglón tachado fuera de los esperados y ningún SKU repetido; la misma semilla da las mismas listas |
| RF-128 | Si el pool no alcanza para cubrir un tipo de dificultad, el sorteo falla con un error que nombra el tipo, en vez de dar una lista incompleta |
| RF-129 | El script crea un Form con una sección por lista, a la que lleva el desplegable del inicio y que sigue a la sección final, y no pide ni recolecta nombre ni mail |

## 6. Criterios de aceptación

| ID | Dado | Cuando | Entonces | Test |
|---|---|---|---|---|
| CA-218 | `pool.csv` y `catalogo_evaluacion.csv` | Se valida el pool | No hay problemas: ids únicos, 5 de núcleo, ≥ 100 sorteables, SKU del catálogo, cantidades > 0, unidades válidas y ≥ 5 por tipo | `tests/test_benchmark_manuscritos.py::test_pool_coherente_con_el_catalogo` |
| CA-219 | El pool y la semilla 116 | Se sortean 30 listas dos veces, y otra vez con la semilla 117 | Las dos primeras son iguales y la tercera no | `tests/test_benchmark_manuscritos.py::test_listas_reproducibles` |
| CA-220 | 30 listas sorteadas | Se mira cada una | El núcleo está en 1 a 5, hay 10 sorteados que cubren todos los tipos, las posiciones son 1 a 16 sin repetir y no se repite ningún SKU | `tests/test_benchmark_manuscritos.py::test_lista_cubre_las_dificultades` |
| CA-221 | 30 listas sorteadas | Se mira el tachado | Es un renglón del pool, no está entre los esperados y su posición está entre la 6 y la 16 | `tests/test_benchmark_manuscritos.py::test_tachado_fuera_de_los_esperados` |
| CA-222 | Un pool sintético sin renglones de `tono` | Se sortea una lista | Falla con un error que dice `tono` | `tests/test_benchmark_manuscritos.py::test_pool_sin_un_tipo_falla` |
| CA-223 | Dos listas, una con comillas en el encabezado | Se genera el script | Trae una sección por lista con sus 16 renglones y el «tachar», el desplegable con las dos, el salto a la sección final, `setCollectEmail(false)` y las listas como JSON válido; el archivo es solo ASCII y los acentos vuelven al leer el JSON | `tests/test_benchmark_manuscritos.py::test_script_del_form` |

## 7. Errores y casos borde

- Un SKU del pool que no está en el catálogo: `listas` no sortea y muestra los problemas de `validar_pool`.
- Pedir más renglones variables que tipos se puedan cubrir sin repetir SKU: falla con RF-128.
- Una persona que salteó o cambió un renglón: se corrige el esperado de su foto al armar `casos.json` (diseño, §5).

## 8. Decisiones y preguntas abiertas

- **Todas las listas llevan un tachado.** El diseño decía «algunas»; con todas, la instrucción del Form es una sola.
  Si hace falta medir hojas sin tachado, se agrega un parámetro.
- **La foto se sube a mano en el Form.** Ni Apps Script ni la API de Forms crean preguntas de subir archivo.

## 9. Plan de implementación

1. Esta spec y el pool.
2. Tests de CA-218 a CA-223 en `tests/test_benchmark_manuscritos.py`.
3. `internal/benchmark_manuscritos.py` con los comandos `listas` y `form`, y `listas.json` y `crear_form.gs`
   generados con la semilla 116.
