# Spec: mejoras del agente a partir de la Clase 8 (Software 2.0 y 3.0)

> Documento de diseño. Lee el agente con los conceptos de la Clase 8 de Inteligencia
> Artificial Agéntica («AI Training 2.0 and 3.0») y propone mejoras priorizadas. Cada mejora cita el concepto que
> la motiva y la evidencia en el código. Si se aprueba, cada mejora se pasa a la [plantilla](_plantilla.md) con
> sus IDs definitivos. M8-1 ya está implementado en esta rama (ver su sección).

| | |
|---|---|
| Estado | M8-1 implementado; M8-2 a M8-8 propuestos |
| Base | `develop` (`2b64fce`), con `feature/mejoras-del-agente` ya integrada (D-47 a D-52, grupo `reservado`, `evaluar_busqueda`, tope de tokens). Cita `feature/benchmark-pedidos-manuscritos` (`docs/benchmark-pedidos-manuscritos.md`), que todavía no está en `develop` |
| IDs | Provisorios: `M8-1` a `M8-8`. Los RF, CA y D se reservan cuando se apruebe, después de los que usa `feature/mejoras-del-agente` |
| Fuente de la clase | Resumen de la Clase 8, secciones 4 a 6, 10, 11.2, 11.3 y 13 |

**Aclaración de alcance.** Norma no es un agente de normativa. Es el agente que toma pedidos mayoristas de una
distribuidora de peluquería: interpreta el pedido contra el catálogo, propone y registra solo cuando el cliente
acepta. Los conceptos de la clase se aplican igual. Donde la consigna habla de «citas verificables» o de «normas
nuevas», acá corresponden a la **evidencia de cada SKU** y a **productos, precios o marcas que el modelo no vio**.

---

## 1. Estado actual

El principio rector ya es el de la clase: *«El LLM interpreta; el backend valida y registra»*
([`architecture.md`](../architecture.md)). Buena parte de lo que la clase recomienda ya está hecho. Esta spec lo
reconoce y apunta a lo que falta.

| Pieza | Qué hace hoy | Dónde |
|---|---|---|
| Modelo | Un solo `LLM_MODEL` para todo el texto: el loop, el orquestador y el sub-loop | `internal/config.py:16-18`, `internal/agent/llm.py:14` |
| Modelos aparte | La visión (`LLM_VISION_*`) y el audio tienen su proveedor y su modelo propios | `internal/config.py:22-33` |
| Workflow fijo | `armar_contexto → encaminar → precarga_busqueda → descubrir_tools → llm ⇄ tools → pausa` | `internal/agent/graph.py:551-581` |
| Ruteo sin LLM | `skill_clara` elige la skill si el mensaje no deja dudas; si devuelve `None`, la elige el modelo con `leer_skill` | `internal/agent/graph.py:120-136` |
| Confirmación sin LLM | Una aceptación exacta («dale», «listo») registra sin llamar al modelo | `internal/agent/graph.py:503-527`, `internal/services/palabras_clave.py:14` |
| Precarga | El backend busca sin LLM los renglones inequívocos antes de la primera llamada | `internal/agent/precarga.py:1`, `internal/services/interpretation_service.py:16-20` |
| Búsqueda | Determinística: normalización, abreviaturas, sinónimos, parecido por bigramas y por sonido. Sin embeddings | `internal/services/busqueda_service.py:18-66` |
| Agente | El LLM elige tools con `tool_choice="any"`; las tools tienen esquemas Pydantic | `internal/agent/graph.py:357-359`, `internal/schemas/agent_schema.py` |
| Composición | El orquestador delega a un sub-loop con `delegar_skill`, que devuelve una nota estructurada. Usa el mismo modelo (D-38, abierta) | `internal/agent/graph.py:329-350`, `internal/agent/nota.py` |
| Salida | El LLM escribe el mensaje; el código agrega líneas, precios y total | `internal/services/validation_service.py:113` |
| Guardrails | Unas 830 líneas de reglas, casi todas regex, que revisan el texto libre del modelo: stock, precios, nombres internos, pedidos dados por hechos | `internal/agent/guardrails.py:164-528` |
| Memoria | Las últimas 20 intervenciones de la charla; los resultados de tools de turnos anteriores se descartan. El contexto del cliente lo arma el backend sin LLM, una vez por día | `internal/agent/context.py:19`, `context.py:186-200`, `internal/agent/contexto_cliente.py:1` |
| Evaluación | `mensajes` (32 casos, catálogo de 15 productos con marcas reales) y `pedidos` (11 casos, catálogo de unos 400 productos con marcas inventadas). En `feature/mejoras-del-agente` se suman `adversariales` y `consultas_busqueda`, con grupo `desarrollo`/`reservado` (D-50) | `internal/evaluar_agente.py:31-41` |

---

## 2. Concepto de la clase → hallazgo → mejora

| Concepto (sección del resumen) | Hallazgo en el proyecto | Mejora |
|---|---|---|
| **Caution: leakage y contaminación** (10, 13.6) | Los ejemplos del prompt copian los casos de evaluación. `system_prompt.md:9` trae «sh keratina x900», «máscara argan x 1k», «7/3» y «821/2», y el caso `lista-por-marca-clara` de `pedidos_evaluacion.json` pide «2 sh keratina x900» y «1 mascara argan x 1k». «Pantene 400» aparece 17 veces en `mensajes.json` y es el ejemplo de `skill.md:32` y de `BR-09.md:5`. D-50 ya admite que `mensajes` y `pedidos` son optimistas, pero el umbral de release (RNF-15) se mide justo sobre `pedidos` | **M8-1** |
| **Descripción explícita vs. implícita** (5.4) y **sesgo inductivo** (11.2) | El código escribe precios y total, pero las opciones de una aclaración, los pendientes y lo que queda afuera los redacta el LLM. Después, 830 líneas de regex revisan ese texto. El issue #107 (`MEJORAS.md:81`) es un caso: el modelo deja «Tenés estas opciones:» sin opciones | **M8-2** |
| **Software 1.0 vs. 3.0** (5.4, 6.2) | La regla «tono/cantidad» está escrita en el prompt (`system_prompt.md:9`, `sub_loop.md:7`), pero el código la aplica solo con punto (`tinte 8.1/2`). «821/2» y «7/3» quedan para el LLM (`interpretation_service.py:19-20`) | **M8-3** |
| **Scaling y costo** (8.1, 11.4) | Un solo modelo de texto para todos los nodos. D-38 propone un modelo más barato para el sub-loop y sigue abierta. El benchmark de manuscritos (rama `feature/benchmark-pedidos-manuscritos`) ya arma la frontera puntaje/costo para la visión; para el texto no existe | **M8-4** |
| **Escalera `.model → node → workflow → agent`** (6.3, 6.4) | El ruteo ya bajó a código en los casos claros (`skill_clara`). Cuando devuelve `None`, el agente gasta una iteración completa, con todas las tools a la vista, para elegir la skill con `leer_skill` (`system_prompt.md:24`). Elegir entre cuatro skills es un nodo de clasificación, no una decisión de agente | **M8-5** |
| **Lo implícito no extrapola** (11.3, 13.4) | Los ejemplos usan marcas reales (Pantene, Koleston, Sedal, Kérastase) que el modelo conoce del preentrenamiento, y el catálogo real también tiene marcas reales. Un modelo puede «saber» que existe la Koleston 7.1 aunque el catálogo no la tenga. D-48 frena el SKU sin evidencia, pero nadie mide cuánto se apoya el modelo en lo que ya sabe | **M8-6** |
| **Regresión simbólica: de lo implícito a lo explícito** (4.3, 11.3) | Abreviaturas y sinónimos están escritos a mano (`busqueda_service.py:31-66`). Cuando un cliente usa una abreviatura nueva, hoy la resuelve el LLM o no la resuelve nadie, y lo que aprende el sistema no queda en ningún lado | **M8-7** |
| **Predecir vs. explicar** (4.5) | D-48 exige que cada SKU salga de algo que el LLM vio. Esa evidencia se usa para rechazar, pero no se guarda: no se puede auditar después por qué un renglón terminó en tal SKU | **M8-8** |
| **Compresión / world model** (1.3, 7) | El estado del dominio ya está comprimido **en código**: renglones numerados, nota del sub-loop con forma fija (D-32), contexto del cliente armado sin LLM. Las conversaciones vencen a las 24 horas (`config.py:70`) | Nada que agregar: un resumen hecho por el LLM sumaría error sin ganar contexto. Se revisa si las trazas muestran turnos cerca del tope de 20 intervenciones |
| **Prompting vs. fine-tuning** (6.2) | No hay embeddings, rerankers ni modelos propios. Los problemas medidos (`MEJORAS.md`) son de control (no usar la tool terminal, inventar un número de orden), no de conocimiento. No hay datos reales etiquetados, y RNF-03 no deja usarlos | **Descartado:** se recorre el espacio con prompts, ejemplos y código. Ver §4 |
| **Leyes de escala para entrenar** (8, 9) | El proyecto no entrena modelos | No aplica |

---

## 3. Mejoras priorizadas

| # | Mejora | Impacto | Esfuerzo | Depende de |
|---|---|---|---|---|
| M8-1 | Evaluación sin contaminación | Alto | Bajo | `feature/mejoras-del-agente` (D-50) |
| M8-2 | Los hechos los escribe el código | Alto | Medio | — |
| M8-3 | Tono y cantidad en código | Medio | Bajo | Datos del negocio (D-M8-2) |
| M8-4 | Un modelo por nodo | Medio | Bajo | M8-1 |
| M8-5 | El ruteo como nodo | Medio | Medio | M8-4 |
| M8-6 | Cuánto se apoya el modelo en lo que ya sabe | Medio | Bajo | M8-1 |
| M8-7 | De lo que resuelve el LLM a reglas escritas | Medio | Medio | M8-1, `evaluar_busqueda` |
| M8-8 | Evidencia guardada por línea | Bajo-medio | Bajo | D-48 |

M8-1 va primero porque las demás se aceptan o se rechazan con su medición. Sin un grupo reservado, cualquier mejora
puede «pasar» por sobreajuste.

### M8-1 · Evaluación sin contaminación

**Motivación (clase §10 y nota 13.6).** «Priorizar la predicción solo funciona si la predicción se mide bien.» Las
formas de leakage de Kapoor y Narayanan tienen su versión acá:

- **El test dentro del entrenamiento:** el prompt es lo que «entrenamos» en Software 3.0 (§6.2), y hoy trae textos
  literales de los casos que lo miden.
- **Seleccionar con todo el dataset:** los prompts se iteraron mirando `mensajes` y `pedidos` (`MEJORAS.md`, cuarta
  corrida), y RNF-15 decide el release con ese mismo set.
- **Duplicados entre train y test:** nada impide que un caso `reservado` sea casi igual a uno de `desarrollo`, o a un
  ejemplo de una skill.

**Diseño.**

1. **Grupo reservado para `mensajes` y `pedidos`.** Sumar `grupo` a cada caso, como en los adversariales. Los casos
   actuales pasan a `desarrollo`, porque ya se miraron. Se escriben casos `reservado` nuevos: los escribe alguien que
   no tocó los prompts, o salen de un generador con una semilla aparte de `tests/datos_sinteticos.py`.
2. **RNF-15 se mide sobre `reservado`.** `desarrollo` sigue sirviendo para iterar. El release mira solo lo
   reservado, y la corrida muestra los dos números lado a lado: la diferencia entre ambos es la medida del
   sobreajuste.
3. **Ejemplos del prompt que no estén en ningún caso.** Reescribir los ejemplos de `system_prompt.md`, `sub_loop.md`,
   las skills y las BR con productos y formas que no aparezcan en los casos (por ejemplo, otra marca inventada y
   otras medidas). La regla queda escrita en `data_example/README.md`.
4. **Control automático en CI.** Un test en `tests/test_datos_ejemplo.py` falla si:
   - un n-grama de 3 palabras o más (normalizado como en `busqueda_service.normalizar`) de un caso `reservado`
     aparece en un prompt, una skill o una BR;
   - dos casos de grupos distintos tienen un parecido de texto mayor a un umbral (D-M8-1).
5. **Vocabulario no visto (opcional).** Partir los productos de `catalogo_evaluacion.csv`, como propone el
   benchmark de manuscritos (§7): lo reservado pide productos que ningún caso de desarrollo nombró.
6. **Intervalos por caso.** Las líneas de un mismo caso no son independientes: si el modelo entiende mal el
   encabezado de una marca, pierde todas sus líneas juntas. El recall por línea se reporta con un intervalo hecho por
   bootstrap **de casos**, no de líneas. Con 11 casos, un umbral de 95 % puede no distinguirse de 90 %, y la corrida
   tiene que decirlo.

**Archivos.** `data_example/mensajes.json`, `data_example/pedidos_evaluacion.json`, `data_example/README.md`,
`internal/evaluar_agente.py` (filtro por grupo, intervalo por caso), `internal/agent/system_prompt.md`,
`internal/agent/sub_loop.md`, `internal/skills/**`, `tests/test_datos_ejemplo.py`.

**Criterio de aceptación.**

- Ningún n-grama de 3 palabras o más de un caso `reservado` aparece en prompts, skills ni BR, y el test de CI lo
  comprueba.
- `evaluar_agente --set pedidos` reporta `desarrollo` y `reservado` por separado, con intervalo por caso, y
  `fuera_de_umbral` usa solo `reservado`.
- Hay al menos tantos casos `reservado` como de `desarrollo` en `pedidos`.

**Implementado.** Los puntos 1, 2, 3, 4 y 6; el 5 (partir el vocabulario) queda para cuando haya más casos.

| Punto | Cómo quedó |
|---|---|
| 1. Grupo reservado | Cada caso de `mensajes.json` y `pedidos_evaluacion.json` dice su `grupo`. Los de antes pasan a `desarrollo`. Los reservados (18 de `mensajes` y 13 de `pedidos`, contra 32 y 11 de desarrollo) salen de `tests/casos_reservados.py`, con una semilla propia: los mismos tipos de pedido con otras marcas, otros productos y formas de escribir sorteadas (abreviaturas, errores de tipeo, medidas). Un test exige que el archivo sea la salida del generador: si alguien ajusta algo mirando un reservado, se cambia la semilla y se regeneran todos con `python -m tests.casos_reservados` |
| 2. RNF-15 sobre lo reservado | `fuera_de_umbral` recibe las métricas de `reservado` (`GRUPO_DEL_UMBRAL`). Con los dos grupos en la corrida, una tabla los muestra lado a lado con la diferencia. Sin casos reservados en la corrida (`--grupo desarrollo`), RNF-15 no se mide y la corrida lo dice. La salida `.json` y MLflow traen las métricas de cada grupo (`reservado_recall_lineas_pct`, etc.) |
| 3. Ejemplos de los prompts | Los ejemplos de `system_prompt.md`, `sub_loop.md`, `tomar_pedido` (`skill.md`, `respuesta.md`, BR-05, BR-09, BR-10), BR-06, BR-08 y `derivar_o_cancelar` usan marcas inventadas que no están en ningún catálogo (Corvana, Tessaly, Marvane, Lazzia) y otras medidas, tonos y frases. Esto también cubre el punto 2 de M8-6 |
| 4. Control en CI | `tests/test_contaminacion.py`. Encontró dos filtraciones que ya estaban en los sets de `feature/mejoras-del-agente`: la consulta reservada `necesidad-02` («tenés algo para rulos?») era igual al adversarial de desarrollo `necesidad-sin-producto`, y el adversarial reservado `medida-que-no-existe` repetía la consulta de desarrollo `medida_inexistente-01`. Se reescribieron los dos reservados |
| 6. Intervalos por caso | `intervalo_por_casos` remuestrea casos enteros, con todas sus líneas y repeticiones (2000 veces, semilla fija). La corrida informa el intervalo del recall y de la precisión, y avisa si el intervalo de lo reservado incluye el umbral (`umbral_sin_decidir`) |

### M8-2 · Los hechos los escribe el código

**Motivación (clase §5.4 y §11.2).** Cada dato del mensaje que escribe el LLM es una descripción implícita que
después hay que verificar. Si el código lo escribe, el dato es explícito y correcto por construcción. Esto achica el
espacio de salidas posibles del modelo (sesgo inductivo) en vez de corregirlo con regex después. El proyecto ya lo
hace con precios y total (`validation_service.py:113`: *«el LLM no los escribe, así no se equivoca»*). La mejora
extiende ese criterio a lo que sigue en manos del modelo.

**Diseño.**

1. `respond_to_customer` ya recibe `options`, `pendientes` y `afuera` como campos estructurados
   (`agent_schema.py:158`). El código **renderiza** esos bloques debajo del mensaje del modelo, igual que las
   líneas: cada duda con su viñeta y los nombres de sus opciones, y lo que quedó afuera con su motivo en castellano.
2. El modelo escribe solo el marco: qué armó, cómo cierra y la pregunta. `respuesta.md` y `skill.md` dejan de pedirle
   que enumere las opciones.
3. Las reglas de `guardrails.py` que existen solo porque el modelo redactaba esos datos (stock en el texto, opciones
   contadas, SKU en el mensaje) se revisan una por una. Las que dejan de tener sentido se borran.
4. El número de stock nunca le llega al modelo como algo para decir: si no alcanza, el bloque renderizado dice
   cuánto queda (BR-01).

**Archivos.** `internal/services/validation_service.py`, `internal/tools/reply_tools.py`,
`internal/skills/tomar_pedido/respuesta.md`, `internal/skills/tomar_pedido/skill.md`, `internal/agent/guardrails.py`,
`docs/spec/skills-y-respuestas.md`.

**Criterio de aceptación.**

- Con `kind = aclaracion` y `options` cargadas, el mensaje que recibe el cliente muestra todas las opciones aunque
  el texto del modelo no las nombre (cierra el issue #107).
- En `evaluar_agente`, los chequeos de estilo `sin_sku` y los de stock no empeoran, y los rechazos del validador por
  turno bajan en `reservado`.
- `guardrails.py` no suma reglas nuevas para estos bloques.

### M8-3 · Tono y cantidad en código

**Motivación (clase §5.4).** La regla de coloración (`7/3` es tono 7, 3 unidades; `821/2` es tono 8.21, 2 unidades)
es una función explícita, conocida y chica: el caso ideal de Software 1.0. Hoy está escrita en lenguaje natural en
dos prompts y el LLM la ejecuta de forma implícita en cada turno, con la varianza que eso implica.

**Diseño.**

1. Ampliar `separar_renglon` (`interpretation_service.py:167`) para `tono/cantidad` sin punto, siempre que el
   renglón esté debajo de un encabezado de marca de coloración.
2. La ambigüedad de «821/2» (8.21 u 82.1) se resuelve con datos: con la carta de tonos de esa línea, si hay una sola
   lectura válida, se toma esa; si hay dos o ninguna, el renglón va a `pendientes` con la pregunta. Sin la carta
   (hoy el catálogo no trae el tono, `data_example/README.md`), se aplica la convención del negocio (D-M8-2), y si no
   hay convención, se pregunta.
3. Con el código haciendo esto, la regla sale de `system_prompt.md:9` y de `sub_loop.md:7`.

**Archivos.** `internal/services/interpretation_service.py`, `internal/agent/system_prompt.md`,
`internal/agent/sub_loop.md`, `tests/test_interpretation.py`.

**Criterio de aceptación.**

- «7/3» y «821/2» debajo de una marca de coloración salen de la precarga con tono y cantidad, sin llamar al LLM.
- Un caso ambiguo sin carta ni convención termina en `pendientes`, nunca en una línea propuesta.
- Los prompts ya no describen la regla.

### M8-4 · Un modelo por nodo

**Motivación (clase §8 y §11.4).** No todos los nodos necesitan el mismo modelo. Buscar renglones y llenar una nota
con forma fija es más fácil que hablar con el cliente y decidir qué preguntar. El trade-off entre calidad, costo y
latencia se decide por nodo y con medición, no con un modelo único para todo.

**Diseño.**

1. Variables opcionales `LLM_SUBLOOP_PROVIDER` y `LLM_SUBLOOP_MODEL` (y `LLM_ROUTER_*` para M8-5). Vacías usan
   `LLM_MODEL`, así nada cambia por defecto. `llm.get_chat_model()` recibe el nombre del nodo.
2. `graph.py:337` le pasa al sub-loop su modelo en vez del del orquestador. D-38 ya dice que la continuidad la da el
   contexto que arma el backend, así que el cambio no rompe nada.
3. `evaluar_agente` reporta tokens, USD (con `--precio`, como `comparar_vision`) y latencia p50/p95 **por nodo**, y
   arma la frontera puntaje/costo sobre `reservado`, como el benchmark de manuscritos (§7).

**Archivos.** `internal/config.py`, `internal/agent/llm.py`, `internal/agent/graph.py`, `internal/agent/sub_loop.py`,
`internal/agent/observabilidad.py`, `internal/evaluar_agente.py`, `.env.example`.

**Criterio de aceptación.**

- Con las variables vacías, la evaluación da lo mismo que antes, dentro del intervalo.
- Una corrida con modelos distintos por nodo muestra costo y latencia por nodo, y cierra D-38 con la frontera
  medida.

### M8-5 · El ruteo como nodo

**Motivación (clase §6.3 y §6.4).** La escalera dice qué escalón alcanza para cada parte. Elegir la skill es una
clasificación en cuatro clases: un `.model + prompt (node)` con salida estructurada, no una decisión de agente. Hoy,
cuando `skill_clara` no decide, el agente gasta una iteración con el prompt completo para llamar a `leer_skill`. La
Clase 7 llama a este patrón *routing*. Ninguna parte del sistema pide **subir** de escalón: lo que falta es bajar las
que no necesitan autonomía.

**Diseño.**

1. Primero medir: en las trazas, qué parte de los turnos llega con `skill_preactivada = "-"` y cuántas iteraciones
   gasta en elegir. Si es poco, la mejora se descarta.
2. Si vale la pena, un nodo `clasificar` después de `encaminar`, solo cuando `skill_clara` devuelve `None`. Hace una
   llamada con salida estructurada: `skill` (enum de `get_skills()`) y `confianza`. Usa el modelo de `LLM_ROUTER_*`.
3. Con confianza baja, sigue el camino de hoy: el agente elige con `leer_skill`. El nodo nunca le habla al cliente.
4. Queda la composición `skill_clara` (1.0) → `clasificar` (nodo) → agente: cada escalón se usa solo cuando el
   anterior no alcanza.

**Archivos.** `internal/agent/graph.py`, `internal/agent/context.py`, `internal/agent/system_prompt.md` (paso 1 de
«Cómo trabajás»), `internal/agent/observabilidad.py`.

**Criterio de aceptación.**

- En `reservado`, la tasa de skill correcta no baja y las iteraciones por turno bajan.
- Un turno con confianza baja del clasificador se comporta igual que hoy.

### M8-6 · Cuánto se apoya el modelo en lo que ya sabe

**Motivación (clase §11.3 y nota 13.4).** Lo implícito aproxima bien donde vio datos. El modelo «vio» las marcas
reales en su preentrenamiento, y ese conocimiento lo puede ayudar (entiende «Koleston») o lo puede engañar (da por
existente un tono o una medida que este catálogo no trabaja). El catálogo de evaluación, con marcas inventadas, ya es
una prueba fuera de esa distribución, pero no se compara contra nada.

**Diseño.**

1. Un set espejo dentro de `reservado`: los mismos casos escritos dos veces, una con marcas reales y otra con marcas
   inventadas, contra dos catálogos con la misma estructura. La diferencia de recall y de SKU sin evidencia entre las
   dos versiones mide cuánto se apoya el modelo en lo que ya sabe.
2. Los ejemplos de prompts y skills usan marcas inventadas (se hace junto con el punto 3 de M8-1), así el prompt no
   invita a completar con lo que el modelo sabe.

**Archivos.** `data_example/` (casos espejo y catálogo con marcas reales y datos ficticios), `tests/datos_sinteticos.py`,
`internal/evaluar_agente.py`.

**Criterio de aceptación.**

- La corrida reporta la diferencia entre las dos versiones con su intervalo.
- Si la versión con marcas reales tiene más SKU sin evidencia o más productos propuestos que el catálogo no tiene,
  queda registrado como riesgo de producción en `MEJORAS.md`.

### M8-7 · De lo que resuelve el LLM a reglas escritas

**Motivación (clase §4.3 y §11.3).** Cranmer y Φ-SO entrenan algo implícito y después **extraen** una fórmula
explícita, que se entiende y extrapola. Acá, el LLM resuelve abreviaturas y sinónimos que el buscador no conoce. Lo
que resuelve bien se puede pasar a una regla del diccionario de `busqueda_service`: deja de depender del modelo, y
lo resuelve la precarga sin gastar tokens.

**Diseño.**

1. Un script (fuera del CI) junta los renglones donde la búsqueda determinística falló y el agente terminó en un SKU
   con evidencia. Las fuentes son `consultas_busqueda.json` (`desarrollo`) y las corridas de `evaluar_agente`.
2. Un LLM propone el par «forma del cliente → palabra del catálogo» para cada uno.
3. Una persona revisa los candidatos, que entran a `ABREVIATURAS` o `_GRUPOS` por PR.
4. Un candidato se acepta solo si `evaluar_busqueda` sobre `reservado` no baja en ninguna categoría: una abreviatura
   nueva puede romper otra búsqueda.

**Archivos.** Un script nuevo en `internal/` (por ejemplo `proponer_sinonimos.py`), `internal/services/busqueda_service.py`,
`tests/test_busqueda.py`.

**Criterio de aceptación.**

- Cada regla nueva tiene su test y una consulta de `desarrollo` que la motiva.
- `evaluar_busqueda` en `reservado` mejora o queda igual en cada `tipo`.
- El script nunca lee texto de clientes reales (RNF-03, D-M8-3).

### M8-8 · Evidencia guardada por línea

**Motivación (clase §4.5).** El modelo ptolemaico predecía bien y no explicaba. Para operar el negocio alcanza con
predecir bien, pero cuando un pedido sale mal hay que poder reconstruir **por qué** el agente eligió ese SKU. D-48 ya
calcula la evidencia para rechazar; la mejora la guarda.

**Diseño.**

1. Cada línea de la propuesta lleva su procedencia: número de renglón, la tool que devolvió el SKU, el `score` y
   cuántas alternativas tenía. Va en el estado del grafo y en la traza, sin texto del cliente (RNF-03).
2. `evaluar_agente` suma una métrica: líneas con evidencia trazable sobre líneas propuestas, que tiene que ser
   100 %.
3. Para el cliente no cambia nada: ya ve cada línea con nombre y precio («Esto es lo que entendí»,
   `validation_service.py:121`).

**Archivos.** `internal/agent/tool_registry.py`, `internal/agent/state.py`, `internal/agent/observabilidad.py`,
`internal/evaluar_agente.py`.

**Criterio de aceptación.**

- Toda línea de una orden registrada tiene procedencia en la traza.
- Una línea sin procedencia hace fallar la evaluación.

---

## 4. Lo que se descarta

- **Fine-tuning del LLM.** La clase lo describe como *modificar* el espacio de programas, contra *recorrerlo*
  prompteando (§6.2). Los errores medidos son de control y se corrigen con código y tools. No hay datos reales
  etiquetados en volumen, RNF-03 no deja usar los de clientes, y un modelo ajustado ataría el proyecto a un
  proveedor (contra RNF-09).
- **Embeddings o reranker entrenados.** El catálogo tiene cientos de productos y la búsqueda léxica se mide sola. Si
  `evaluar_busqueda` muestra que fallan las consultas de tipo necesidad («algo para rulos»), se prueba primero un
  modelo de embeddings sin ajustar, como un nodo más, y se mide en `reservado`.
- **Resumir la conversación con el LLM.** El estado ya está comprimido en estructuras que arma el backend (renglones,
  nota, contexto del cliente). Un resumen hecho por el modelo sumaría una fuente de error.
- **Optimización automática de prompts (DSPy/GEPA) para el texto.** El benchmark de manuscritos la planea para la
  visión. Para el texto conviene esperar a M8-1: sin `reservado`, el optimizador sobreajusta más rápido que una
  persona.

---

## 5. Riesgos y preguntas abiertas

| ID | Riesgo o pregunta | Estado |
|---|---|---|
| D-M8-1 | Umbral de parecido entre casos de grupos distintos (M8-1). Con uno muy bajo, todo pedido de shampoo se parece a otro; arrancar con n-gramas de 3 palabras y ajustar | Decidida para arrancar: n-gramas de 3 palabras que no cruzan renglones ni signos; se ignoran los que tienen dos palabras de función o más («con una persona», «la cantidad a»), que no dicen de qué caso salieron. Dos casos del mismo catálogo y de grupos distintos son el mismo si el parecido de Jaccard de sus n-gramas pasa 0,5 (los textos de menos de tres palabras, solo si son iguales). Se revisa si el control frena casos que no se copian |
| D-M8-2 | ¿El negocio tiene carta de tonos por línea, o una convención para «821»? Sin eso, M8-3 solo resuelve los casos sin ambigüedad y pregunta el resto | Abierta, la responde el negocio |
| D-M8-3 | ¿Se pueden usar las conversaciones de prueba del equipo (`data/norma.db`) como fuente de M8-7? AGENTS.md permite leerlas para depurar, pero no copiar su contenido a commits. Una abreviatura genérica («acond») no es un dato personal; propuesta: solo de los sets ficticios hasta que el equipo decida | Abierta |
| R-1 | Escribir casos `reservado` cuesta, y cada vez que alguien ajusta mirando uno hay que reemplazarlo (D-50). Sin disciplina, lo reservado se contamina en pocas iteraciones | Riesgo |
| R-2 | Mover texto del modelo a plantillas (M8-2) puede hacer que los mensajes suenen más rígidos. Se mide con los chequeos de estilo y leyendo conversaciones (`--conversaciones`) | Riesgo |
| R-3 | Un modelo más barato en el sub-loop puede perder renglones de forma silenciosa. La nota se valida (`nota.py`), pero un renglón mal resuelto con un SKU válido pasa. Por eso M8-4 depende de M8-1 | Riesgo |
| R-4 | Con 11 casos de `pedidos`, ninguna comparación entre modelos o prompts va a ser concluyente. Hace falta escalar el set (por ejemplo, con el generador de `datos_sinteticos.py`) antes de cerrar D-38 o D-28 | Riesgo |
| R-5 | Esta spec se apoya en `feature/benchmark-pedidos-manuscritos`, que no está en `develop`. `feature/mejoras-del-agente` ya se integró | Riesgo |
| R-6 | Los reservados de `mensajes` y `pedidos` salen de plantillas escritas en la misma rama que reescribió los ejemplos de los prompts. El control de CI garantiza que no comparten frases, pero no que las plantillas no sigan, sin querer, el estilo de los prompts. La primera corrida con LLM real dice cuánto separa a los dos grupos | Riesgo |

---

## 6. Plan sugerido

Una rama y una PR por mejora, en este orden: **M8-1** (mide), **M8-2** y **M8-3** (bajan lógica a código y no
dependen del modelo), **M8-4** y **M8-5** (costo, que se deciden con la medición de M8-1), y después **M8-6**,
**M8-7** y **M8-8**.
