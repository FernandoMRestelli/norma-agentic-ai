# Spec: Búsqueda tolerante a errores

> Frente A del [plan de mejora del asistente](../plan-mejora-asistente.md#a--búsqueda-tolerante-a-errores).
> Detalla RF-07 y RF-08 de la [spec maestra](README.md) y agrega RF-36 a RF-39 y CA-59 a CA-68.

| | |
|---|---|
| Etapa del roadmap | 02 · Datos del negocio |
| Estado | Implementado |
| Responsable | Frente A |
| Requisitos de la spec del MVP | RF-07, RF-08 |

## 1. Objetivo

Que un pedido con errores de ortografía, abreviaturas o medidas escritas de otra forma encuentre el producto que el
cliente pidió, y que cuando la coincidencia no es segura la tool le dé al agente candidatos para preguntar
«¿quisiste decir…?» en lugar de decir que el producto no existe. La precisión importa tanto como la tolerancia: un
falso positivo hace que el agente proponga algo que el cliente no pidió.

## 2. Alcance

| Dentro | Fuera |
|---|---|
| Normalización compartida entre la búsqueda y el catálogo: abreviaturas, sinónimos, plurales, unidades y tonos | La regla de negocio que usa las sugerencias (BR-07, frente E) |
| Coincidencia aproximada por tipeo y por sonido del español, sin aproximar números | Buscar por categoría: eso lo hace `explorar_catalogo` (RF-35) |
| Medida separada del nombre al indexar | Guardar el tono en la línea del pedido (frente C, V-01) |
| `sugerencias` y `corregido` en el resultado de `search_products` | Caché del catálogo leído de Sheet 1 (RNF-13, frente D) |
| Índice en memoria del catálogo normalizado, con el stock siempre fresco | Corregir los nombres del catálogo |

## 3. Comportamiento

### 3.1 Normalización (la misma para la búsqueda y para el catálogo)

1. Minúsculas, sin tildes (`ñ` queda como `n`) y sin apóstrofos (`L'Oréal` = `loreal`).
2. Números: la coma decimal es punto (`6,1` = `6.1`); un punto seguido de tres cifras es separador de miles
   (`1.000 ml` = `1000 ml`). Número y unidad pegados se separan (`400ml`, `1k`, `20vol`) y la `x` de «por» se
   descarta (`x900` = `X 900` = `900`).
3. Cada número seguido de una unidad conocida es una **medida** con su valor en la unidad base:

   | Dimensión | Unidades | Base |
   |---|---|---|
   | Volumen | `ml`, `cc`, `l`, `lt`, `litro` | ml (`1 l` = `1000 ml` = `1000 cc`) |
   | Peso | `g`, `gr`, `k`, `kg`, `kilo` | g (`1k` = `1 kg` = `1000 g`) |
   | Volúmenes de oxidante | `vol`, `volumen` | vol |
   | Unidades | `u`, `un`, `unid`, `unidad` | unid |
   | Largo | `mm`, `cm` | mm |

   Un número sin unidad queda como número suelto. Una unidad sin número se descarta.
4. Las palabras pasan a singular (`jabones` = `jabón`, `cremas` = `crema`), se descartan las de relleno («de»,
   «con», «para»…) y las de una sola letra, y se llevan a su forma canónica:
   - **Abreviaturas y variantes**, que valen lo mismo que la palabra: `sh`, `shp`, `champú` → `shampoo`;
     `acond`, `acond.` → `acondicionador`; `masc`, `mascarilla`, `mask`, `masque` → `mascara`; `oxi` → `oxidante`;
     `deco` → `decolorante`; `trat` → `tratamiento`; `keratin` → `keratina`.
   - **Sinónimos**, que coinciden con un puntaje un poco menor (0,85): `enjuague` ~ `acondicionador`;
     `tintura`, `tinte` ~ `coloracion`; `oxigenada`, `revelador` ~ `oxidante`; `oleo`, `oil` ~ `aceite`;
     `suero` ~ `serum`. Así, «acondicionador» trae primero lo que se llama acondicionador y no mezcla las cremas de
     enjuague, pero «enjuague fidelite» encuentra el acondicionador si no hay otra cosa.
   - **Frases** que en el catálogo se escriben con una palabra: «baño de crema» y «baño crema» son `baño`, como
     figuran esos productos (`… KERATINA BAÑO X 200 GR`, `… BAÑO ARGAN X 1KG`). Sin esto, «crema» buscaba cremas y
     «baño» se parecía a «manos».

### 3.2 Índice (RF-39)

Cada producto se indexa con las palabras de nombre, marca y presentación ya normalizadas, y por separado sus
medidas y números (RF-38). En el catálogo real la columna `presentacion` viene vacía y la medida está dentro del
nombre (`X 900 ML`, `X 1K`, `370CC`): da lo mismo, porque se extrae de los dos lugares.

El índice vive en memoria y se reconstruye solo cuando cambia el texto del catálogo (SKU, nombre, marca o
presentación). El stock **no** se guarda en el índice: cada búsqueda pide el catálogo a `inventory_service`, que
descuenta las órdenes activas, y el resultado sale con ese stock.

### 3.3 Puntaje

La búsqueda se divide en términos: cada palabra y cada número o medida. El puntaje de un producto es el promedio de
lo bien que aparece cada término en él (de 0 a 1).

| Término | Coincide con | Vale |
|---|---|---|
| Palabra | La misma forma canónica | 1 |
| Palabra | Un sinónimo | 0,85 |
| Palabra | Igual por sonido (`b/v`, `s/c/z`, `ll/y`, `h` muda, `k/c/qu`, `g/j`, letras dobles) | 0,95 |
| Palabra | Letras cambiadas, faltantes, sobrantes o transpuestas: `1 − errores / largo` | si es ≥ 0,8 |
| Palabra | El comienzo de una palabra del producto, con 5 letras o más (`hidrat` → `hidratante`) | 0,85 |
| Medida | Una medida igual en la unidad base, o un número sin unidad con ese valor (`X 900`) | 1 |
| Medida | La misma cantidad en la otra dimensión de peso y volumen (`1 kg` y `1000 ml`) | 0,9 |
| Número sin unidad | El mismo valor, solo o como medida (`400` = `400 ml`; `1000` = `1 kg`) | 1 |
| Número sin unidad | Un tono que empieza igual (`6` abarca `6.0` y `6.1`) | 1 |
| Número sin unidad | Un tono escrito sin punto (`821` = `8.21`, `71` = `7.1`), según V-01 | 1 |

Las palabras de hasta 4 letras y las que tienen cifras (`system3`) no admiten errores de tipeo, solo de sonido o
exactas: en una palabra corta, una letra de diferencia ya es otra palabra (`baño` no es `mano`, `pelo` no es
`palo`). Los números **nunca** se aproximan: `340` no es `400`, `6.1` no es `7.1`, `20 vol` no es `30 vol`.

### 3.4 Resultado

1. Si la búsqueda es exactamente un SKU del catálogo, ese producto sale solo con puntaje 1.
2. **Cantidad al principio:** un entero sin unidad al comienzo de la búsqueda y seguido de palabras («2 mascara
   argan x 1k») suele ser la cantidad del pedido. Primero se busca con ese número; si el mejor match lo tiene
   (un tono, una medida o un número del nombre: «6 koleston» trae los tonos 6), queda así. Si no, se busca de nuevo
   sin él, y el número no baja el puntaje ni marca `aproximado`. Así no se pierde precisión: un número que está en
   el catálogo sigue filtrando, y uno que no está no se toma como otra medida. En una coloración, si ese es el único
   número de la búsqueda, puede ser el tono y no se descarta: «9 tintura igora» sale `aproximado` si no hay tono 9,
   y «2 koleston 7.1» trae el 7.1.
3. **`matches`**: los productos con puntaje ≥ 0,6, ordenados de mayor a menor, quitando los que quedan a más de
   0,1 del primero (si «shampoo pantene 400» encuentra el de 400 ml, el de 200 ml no aparece). A igual puntaje va
   primero el producto con menos palabras de sobra en el nombre. Como mucho `limit` resultados.
4. **`aproximado`**: es `true` cuando algún término no coincidió exacto (tipeo, sonido, otra dimensión, o un número
   que no está). Las abreviaturas y los sinónimos no cuentan como aproximación. Un match aproximado es candidato a
   confirmar con el cliente.
5. **`sugerencias`**: solo si `matches` quedó vacío. Son los productos donde **todas** las palabras de la búsqueda
   aparecen, aunque sea con un parecido más flojo (≥ 0,7), aunque algún número no coincida: «pantene 340» sugiere
   los Pantene que hay, y «shampoo pntne» sugiere los shampoos Pantene. Si alguna palabra no aparece en el
   producto, no se sugiere: «shampoo dove» no sugiere shampoos de otra marca ni el jabón Dove, y «coca cola» no
   sugiere nada. Siempre con `aproximado: true`, con el mismo corte de 0,1 respecto de la primera.
6. **`corregido`**: qué palabras de la búsqueda se interpretaron como otra en el primer resultado (de `matches` o, si
   no hay, de `sugerencias`): abreviaturas (`sh` → `shampoo`), variantes (`champu` → `shampoo`), sinónimos
   (`tintura` → `coloracion`), errores de tipeo o de sonido (`pantne` → `pantene`) y tonos (`71` → `7.1`). La clave
   es la palabra como la escribió el cliente, en minúsculas y sin tildes.

## 4. Contratos

### `search_products` (resultado que ve el LLM)

```json
{
  "matches": [{"sku": "…", "name": "…", "brand": "…", "presentation": null, "category": "…", "price": "4500.00",
               "score": 0.93, "aproximado": false, "hay_stock": true}],
  "sugerencias": [],
  "corregido": {"champu": "shampoo"}
}
```

- Las tres claves vienen siempre: `sugerencias` es una lista vacía cuando hay `matches`, y `corregido` un objeto
  vacío si no se corrigió nada.
- Cada producto trae los campos de `Product` salvo `stock`, `min_stock` e `is_low_stock`, más `score`,
  `aproximado` y `hay_stock`. El LLM no ve cantidades de stock.
- Errores: `catalogo_no_disponible`, como antes.

**Qué pasa con lo que ya lo usa:** `matches` mantiene su forma y suma `aproximado`. Las skills y el system prompt
no cambian en esta PR; la regla que usa `sugerencias` es BR-07 (frente E).

### Código

- `internal/services/busqueda_service.py`: `normalizar(texto)`, el índice y `buscar(query, productos, limit)`.
- `internal/schemas/catalog_schema.py`: `ProductMatch.aproximado` (por defecto `false`) y `SearchResult`
  (`matches`, `sugerencias`, `corregido`).
- `internal/tools/catalog_tools.py`: `buscar_productos(query, limit) -> SearchResult`. `search_products(query,
  limit)` sigue devolviendo la lista de matches, para quien la use desde el código.

## 5. Requisitos

| ID | Requisito |
|---|---|
| RF-36 | La búsqueda y el catálogo se normalizan con las mismas reglas: abreviaturas y sinónimos del rubro, plurales, unidades (`1k` = `1 kg` = `1000 g`; `1 l` = `1000 ml` = `1000 cc`; `x900` = `900`) y tonos de coloración (`821` = `8.21`, V-01) |
| RF-37 | Una palabra coincide aunque tenga letras cambiadas, faltantes o transpuestas, o un error por sonido del español (`b/v`, `s/c/z`, `ll/y`, `h` muda, `k/qu`); los números nunca se aproximan |
| RF-38 | La medida de cada producto se separa del nombre al indexar y se compara medida con medida, venga del nombre o de la presentación |
| RF-39 | `search_products` devuelve coincidencias confiables, sugerencias cuando no las hay y las palabras corregidas, sobre un índice en memoria del catálogo normalizado con el stock siempre fresco |

## 6. Criterios de aceptación

Los tests usan un catálogo sintético escrito como el real (mayúsculas, medida dentro del nombre, abreviaturas) con
precios inventados.

| ID | Dado | Cuando | Entonces | Test |
|---|---|---|---|---|
| CA-59 | Un shampoo de keratina de 900 ml escrito `… KERATIN SHAMPOO X 900 ML`, y otros de keratina en 230 ml y 5 L | Se busca «sh keratina x900» | Ese shampoo sale primero y los de otra medida no aparecen | `tests/test_busqueda.py::test_abreviatura_y_medida_pegada_encuentran_el_producto` |
| CA-60 | Shampoos Fidelite y de otras marcas | Se busca «champu fidelite» o «champú fidelité» | Solo salen shampoos Fidelite y `corregido` dice `champu` → `shampoo` | `tests/test_busqueda.py::test_variante_de_shampoo_encuentra_la_marca` |
| CA-61 | Máscaras de argan en `X 1 KG`, `X 1K`, `X 250 G` y `X 1000ML` | Se busca «mascara argan 1k», «mascara argan 1 kg» o «mascara argan 1000 g», también con la cantidad adelante («2 mascara argan x 1k») | Primero salen las de 1 kg, las de 250 g no aparecen, y `1 l` = `1000 ml` = `1000 cc`. La cantidad no baja el puntaje ni marca `aproximado`, salvo que el número esté en el catálogo (`6 koleston` sigue trayendo los tonos 6) | `tests/test_busqueda.py::test_unidades_equivalentes_comparan_medida_con_medida`, `tests/test_busqueda.py::test_cantidad_al_principio_*` |
| CA-62 | Coloraciones Koleston en varios tonos escritos `7.1`, `8.21` | Se busca «tintura koleston 7.1», «tinte koleston 71» o «koleston 821» | Sale primero el tono pedido y `corregido` registra la interpretación del tono | `tests/test_busqueda.py::test_tintura_y_tono_sin_punto_encuentran_el_tono` |
| CA-63 | Productos con nombres en mayúsculas y abreviados (`ACOND.`) | Se busca con letras cambiadas, faltantes o transpuestas, o con errores por sonido | Sale primero el producto correcto y `corregido` dice qué palabra se interpretó como otra | `tests/test_busqueda.py::test_errores_de_tipeo_y_de_sonido` |
| CA-64 | El mismo catálogo | Se busca un número parecido a otro (340/400, 6.1/7.1, 20/30 vol, 250/1000 g), una palabra sin parecido real o una palabra corta con una letra cambiada («baño» y «manos») | No coincide nada parecido: ni en `matches` ni en `sugerencias` aparece un producto que el cliente no pidió | `tests/test_busqueda.py::test_precision_*` |
| CA-65 | Ninguna coincidencia confiable | Se busca «pantene 340», «koleston 9.1» o «shampoo pntne» | `matches` vacío y `sugerencias` con los candidatos, todos `aproximado: true` | `tests/test_busqueda.py::test_sin_coincidencia_confiable_devuelve_sugerencias` |
| CA-66 | El agente invoca `search_products` | Hay o no hay matches | Vienen `matches`, `sugerencias` y `corregido`; cada producto trae `score`, `aproximado` y `hay_stock`, sin `stock`, `min_stock` ni `is_low_stock` | `tests/test_busqueda.py::test_contrato_de_la_tool` |
| CA-67 | Un catálogo ya indexado | Cambia el stock o cambia un nombre | El índice no se reconstruye por el stock, que sale fresco en el resultado; un nombre nuevo sí reconstruye el índice | `tests/test_busqueda.py::test_indice_en_memoria_con_stock_fresco` |
| CA-68 | Un catálogo de 1.000 productos ya indexado | Se hacen búsquedas típicas | Cada búsqueda tarda menos de 50 ms | `tests/test_busqueda.py::test_busqueda_sobre_mil_productos_es_rapida` |

Siguen pasando CA-29 y CA-45 (`tests/test_tools.py`) y CA-58 (`tests/test_agent_graph.py`).

## 7. Errores y casos borde

| Situación | Qué hace el sistema |
|---|---|
| Búsqueda vacía o solo con palabras de relleno | Busca con las palabras de relleno; si no queda nada, resultado vacío |
| La búsqueda es solo un número que no está | Nada en `matches` ni en `sugerencias`: sin una palabra no hay a qué parecerse |
| La medida del producto no tiene unidad (`SHAMPOO X 900`) | Coincide con `900`, `900 ml` o `x900` |
| El cliente escribe una medida que no existe para ese producto | El producto puede salir, pero con `aproximado: true` y puntaje menor a 1 (CA-45) |
| La búsqueda empieza con la cantidad («2 mascara argan x 1k», «3 sh pantene 400») | Si ningún producto que coincide tiene ese número, se ignora: mismo resultado que sin la cantidad |
| La búsqueda es solo un número («6») o el número no está al principio («pantene 340») | Cuenta como siempre: `6` abarca los tonos 6.0 y 6.1, y `340` no es `400` |
| Dos productos empatan | Primero el que tiene menos palabras de sobra; si siguen empatados, el orden del catálogo |
| El catálogo no se puede leer | Error `catalogo_no_disponible`, como antes |

## 8. Decisiones y preguntas abiertas

| ID | Decisión o pregunta | Estado |
|---|---|---|
| D-A1 | Sin dependencias nuevas: la distancia de edición, la clave por sonido y el índice están hechos con la librería estándar. Con el índice y el parecido calculado por palabra del vocabulario, 1.000 productos se buscan en pocos milisegundos | Cerrada |
| D-A2 | `1 kg` y `1000 ml` coinciden con 0,9 y como aproximados: en el rubro «la de kilo» puede ser cualquiera de las dos, pero no son la misma medida | Cerrada |
| D-A3 | El tono escrito sin punto sigue el supuesto V-01 de [validaciones_pendientes.md](../validaciones_pendientes.md). Hoy las coloraciones del catálogo real no tienen tono en el nombre, así que solo aplica cuando lo tengan | Abierta (V-01) |
| D-A4 | Las listas de abreviaturas y sinónimos están en `busqueda_service.py`. Si el negocio usa otras, se suman ahí con su test | Cerrada |

## 9. Plan de implementación

1. `feature/busqueda-tolerante-a-errores`: esta spec, `tests/test_busqueda.py`, `busqueda_service.py` y el
   resultado nuevo de `search_products`. PR a `develop`, depende de la PR de documentación (#68).
