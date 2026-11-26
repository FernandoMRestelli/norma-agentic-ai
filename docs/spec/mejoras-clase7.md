# Spec de mejoras: diseño agéntico de Norma

> **Estado:** propuesta para discutir. No hay nada implementado. Diagnóstico hecho sobre `develop` en el commit
> `2b64fce` (la versión 0.2.0 más el PR #115): las referencias `archivo:línea` son de ese commit.
>
> **De dónde sale:** los conceptos de la clase 7 de IA Agéntica, *Diseño de sistemas agénticos*. Las referencias
> del tipo «clase §2.4» apuntan a las secciones del resumen de esa clase
> (`IAA - Teoria/7. Diseño de sistemas agénticos - Resumen.md`).
>
> **Convención del repo:** el comportamiento nuevo entra primero a la spec (`docs/spec/`, con RF y CA) y después al
> código. Este archivo reúne las propuestas; los RF y CA de las que se aprueben van a la spec que corresponda
> (ver pregunta abierta P-1).

---

## 1. Objetivo

Revisar a Norma con la lente de la clase y proponer mejoras **priorizadas por impacto y esfuerzo**. Cada mejora
tiene un criterio de aceptación verificable: un test o un comando que se pueda correr.

### 1.1 Cómo se traduce el ejemplo de la clase a este proyecto

La clase usa como ejemplo transversal un agente de **normativa** con la tool `buscar_normas`. Norma es otro
dominio (toma de pedidos de una distribuidora), pero las piezas son las mismas:

| En la clase (agente de normas) | En Norma |
|---|---|
| `buscar_normas` (BM25 + embeddings sobre 44 normas) | `search_products` / `buscar_renglones` (búsqueda tolerante sobre ~1.090 productos) |
| «Cada cita existe en el corpus» (verificador de citas) | «Cada SKU salió de lo que vio el modelo» (`evidencia.py`, `guardrails.validar_salida`, `nota.validar`) |
| Acción irreversible → aprobación humana | Registrar la orden → solo si **el cliente** acepta la propuesta, y el backend lo comprueba (RF-111) |
| Índice de normas = memoria semántica de solo lectura | Catálogo + `datos_negocio.md` |
| Skill `informe-comparado-normas` | Skills `tomar_pedido`, `confirmar_pedido`, `informar`, `derivar_o_cancelar` y sus BR |

### 1.2 Lo que ya resolvió el PR #115

El PR #115 («El código impide lo que el agente no puede hacer, y hay con qué medirlo») entró a `develop` mientras se
escribía la primera versión de este documento, y cubre varias de las ideas de la clase. Esta versión parte de ahí:

| Idea de la clase | Qué trajo el PR #115 |
|---|---|
| Tope de pasos **y de gasto** (§2.6) | `AGENT_MAX_TOKENS_PER_TURN` (200.000), que cuenta los sub-loops y también lo controla el sub-loop (RF-122, CA-209) |
| Loop sin progreso (§2.7) | Una llamada de lectura igual a una anterior del turno devuelve `llamada_repetida`, y a la tercera el turno cierra con lo encontrado (RF-122, CA-210) |
| Verificador de evidencia (§3.6, §4.3) | `sku_no_buscado`, `opcion_sin_elegir`, `cantidad_distinta` (RF-112 a RF-114), precios y medidas vistos (RF-120, RF-121) y aceptación comprobada en `armar_pedido` (RF-111) |
| Nivel 5: muestras y agregados (§1.3) | `sensor_calidad` sobre las trazas y la base, con `rescates` por turno (RF-123) |
| «El éxito se puede medir» (§4.13) | Set `adversariales` con un grupo `reservado` (RF-116, D-50), tope de llamadas y costo de la corrida (RF-117) y `comparar_corridas` con intervalo de Wilson (RF-124) |
| Archivo de progreso al día (§1.4) | `MEJORAS.md` y el README actualizados, con los primeros números de trazas locales |

### 1.3 Conclusión general, en una línea

Norma ya aplica casi todas las ideas de la clase (grafo con ruteo, validación antes de ejecutar, errores útiles,
verificadores deterministas, idempotencia, aprobación humana con control del backend, topes de pasos, tiempo, tokens y
repeticiones). Lo que falta es **correr la medición y decidir con ella**: no hay una línea de base completa, el tope de
iteraciones sigue en 50 (D-51, abierta), las métricas no cuentan las vueltas de los sub-loops ni el caché, y el
prefijo que se manda en cada llamada no aprovecha el caché del proveedor.

---

## 2. Estado actual: diagnóstico concepto por concepto

Leyenda: ✅ aplicado · 🟡 aplicado en parte · ❌ falta · ➖ no aplica (y está bien que no aplique).

### 2.1 Ubicación en el gradiente de autonomía (clase §1.2, §1.3, §4.8)

**Gradiente de arquitectura (quién decide el camino).** Norma no es un loop libre: es un **grafo** donde el
código decide la mayor parte del camino y el modelo decide solo en un nodo.

| Tramo | Quién decide | Dónde |
|---|---|---|
| Fuera de tema, ráfaga, mensaje largo, cancelación con borrador | Código (regex), sin LLM | `conversation_service.py:246-314`, `topic_filter.py:37`, `guardrails.py:897` |
| Aceptación exacta («dale», «ok») con propuesta vigente | Código, sin LLM | `graph.py:598-626` (`confirmar`, `decidir_tras_pausa`) |
| Qué skill usar cuando el mensaje no admite dudas | Router determinista | `graph.py:123-139` (`skill_clara`), `graph.py:155-182` (`encaminar`) |
| Buscar renglones inequívocos | Código, sin LLM | `graph.py:185-205` (`precarga_busqueda`) |
| Qué buscar, qué preguntar, cómo responder | **El modelo**, paso a paso (④ loop con tools) | `graph.py:434-478` (`llm`), `graph.py:480-570` (`tools`) |
| Con `AGENT_ORQUESTADOR=true`: a qué sub-loop darle qué renglones | El modelo orquestador (③/⑤) | `tool_registry.py:473-505`, `sub_loop.py:168-303` |

Con el flag apagado (default, `config.py:65`) está en **④ loop con tools dentro de un grafo con ruteo**. Con el
flag prendido se acerca a **⑤**: el orquestador decide «a quién le da trabajo», aunque hay un solo tipo de
trabajador.

**Gradiente de delegación (qué verifica la persona).** Usando los dos datos de la clase §1.3:

- *Costo del error:* medio. Una orden equivocada compromete stock y la tiene que cancelar un operador
  (`api/orders.py:72`). No es irreversible, pero tiene costo operativo.
- *Con qué se verifica:* cada respuesta pasa por verificadores deterministas (§2.4); la escritura irreversible
  (registrar la orden) la aprueba **el cliente** y el backend comprueba que haya sido una aceptación (RF-111); el
  equipo mira agregados con `sensor_calidad` y el dashboard.

Queda en un **nivel 3–4 híbrido**: nivel 4 para «la respuesta cumple las reglas» (el agente itera hasta pasar el
validador) y nivel 3 con *gate* humano para la escritura. ✅ Es coherente con el árbol de la clase §4.13.

**Nivel 5 («muestras y agregados»).** 🟡 El sensor ya da los agregados por línea de comandos (RF-123). Falta un
muestreo de conversaciones para que el equipo revise casos y que esos agregados se vean en el dashboard. Ver M-13.

### 2.2 Las cinco piezas del loop y el tope (clase §2.3–§2.6)

| Pieza | Estado | Dónde |
|---|---|---|
| 1. Mensajes en una lista | ✅ Estado de LangGraph con checkpoints en SQLite | `state.py`, `graph.py:645-676` |
| 2. Llamada con lista + tools | ✅ `tool_choice="any"`: siempre pide una tool | `graph.py:439-441` |
| 3. Ejecutar (lo hace el programa) | ✅ Con permisos por skill y datos inyectados que el LLM no ve | `tool_registry.py:63`, `920-944` |
| 4. Devolver la observación | ✅ Sobre común `{ok, data | error}` enlazado por `tool_call_id` | `graph.py:527-529` |
| 5. Repetir con tope | ✅ Iteraciones, plazo, tokens y llamadas repetidas; al agotarse, cierre «con lo encontrado» | `graph.py:244-277`, `config.py:60-63` |

Lo bueno: al llegar a un tope **no finge una respuesta** (clase §2.3): deja solo `respond_to_customer` y avisa en
el estado que responda con lo encontrado (`context.py:168-175`); si tampoco responde, mensaje fijo y suma una falla
(`graph.py:250-253`). Las vueltas de los sub-loops cuentan para el mismo tope (`graph.py:249`).

**Brecha 1: el tope de iteraciones es muy alto.** 🟡 `AGENT_MAX_ITERATIONS=50` y `300 s` (`config.py:60-61`,
`.env.example:56-57`). Era 8 y se subió a 50 porque los pedidos largos cortaban con «Tuve un problema». El problema
real era *cortar sin responder*, y eso ya lo resolvió el cierre con lo encontrado. D-51 ya lo reconoce y deja la
decisión para la medición: en 28 turnos locales el loop principal dio p50 2, p95 5 y máximo 8. Con la cuenta de la
clase §2.4:

| Pasos | p = 0,99 | p = 0,95 | p = 0,90 |
|---|---|---|---|
| 8 | 0,92 | 0,66 | 0,43 |
| 15 | 0,86 | 0,46 | 0,21 |
| 50 | 0,61 | 0,08 | 0,005 |

Un turno que llega a 50 vueltas casi seguro se desvió: seguir iterando gasta sin mejorar. Hoy lo acota el tope de
tokens, pero 200.000 es unas cuatro veces el p95 de esos turnos (49.000, D-51), y con el lock por conversación
(`conversation_service.py:300`) un turno de 300 s deja al cliente cinco minutos sin poder escribir, cuando RNF-06 pide
un p95 de 10 s. Por la clase §2.5 el costo crece cuadrático: con un prefijo de unos 4.000 tokens y 800 tokens nuevos
por vuelta (números **ilustrativos**), 4 vueltas envían unos 24.000 tokens y 50 vueltas, unos 1.220.000.

**Brecha 2: lo que se mide no es lo que se topea.** 🟡 Para fijar el tope con datos hay que medir la misma cantidad
que controla `descubrir_tools`, y hoy no coincide:

- El tope cuenta `iteraciones + iteraciones_sub_loop` (`graph.py:249`), pero la evaluación y el sensor miden solo
  `outcome.iterations`, las del loop principal (`evaluar_agente.py:578`, `sensor_calidad.py:70-71`). Con el
  orquestador, un turno que delega un sub-loop de 8 vueltas aparece con 2.
- La evaluación guarda las iteraciones y los tokens del **último turno** de cada caso (`evaluar_agente.py:578-582`):
  en un caso de varios turnos, el turno caro puede no ser el último.
- El cierre queda en `rescates` como `cierre` (`observabilidad.py:89-90`), sin el motivo. El motivo (`iteraciones`,
  `plazo`, `tokens`, `sin_progreso`) solo está en el span de MLflow (`graph.py:269`): no se puede decir qué tope
  cierra los turnos sin leer cada traza.

**Caché de prefijo (clase §2.5).** ❌ El prefijo que se manda en cada llamada cambia más de lo necesario:

1. **El estado dinámico va dentro del system prompt.** `build_messages` arma un único `SystemMessage` con el
   prompt fijo, el índice, la skill activa, el contexto del cliente, el estado actual y la nota de búsqueda
   (`context.py:203-219`). Entre un turno y el siguiente cambia el estado (la propuesta, los renglones, las opciones)
   y se pierde el caché de todo lo que sigue, historial incluido. Dentro de un mismo turno el `SystemMessage` casi no
   cambia: el estado se actualiza recién con la tool terminal, y antes solo lo toca el aviso del cierre.
2. **La lista de tools puede cambiar a mitad del turno.** `descubrir_tools` recalcula `tools_disponibles` en cada
   vuelta (`graph.py:273-274`, `tool_registry.py:778-783`): si `encaminar` no preactivó la skill, después de
   `leer_skill` aparecen tools nuevas; y en el cierre queda una sola. Los proveedores serializan las definiciones de
   tools **antes** de los mensajes, así que cambiarlas invalida el caché desde el principio. Cuando la skill está
   preactivada (la ruta clara, `graph.py:163-165`), la lista ya es estable desde la primera vuelta.
3. **El historial se reescribe.** `historial_previo` descarta las tools de turnos anteriores y corta a 20 mensajes
   (`context.py:19`, `186-200`). Es una buena decisión de contexto, pero con la ventana corrediza el prefijo se corre
   en cada turno cuando la charla pasa de 20 mensajes.
4. **No se mide.** `observabilidad.py:70-71` y `165` guardan `input_tokens` y `output_tokens`, pero no los tokens
   leídos del caché (`usage_metadata["input_token_details"]["cache_read"]` en LangChain). Hoy no sabemos si el
   caché funciona.

Matiz: depende del proveedor. `.env.example` ahora trae OpenAI (`gpt-6-luna`), que cachea solos los prefijos largos
y lo informa en el uso; Gemini tiene caché implícito; los modelos `:free` de OpenRouter pueden no cachear nada.
Ver P-3.

**Loop sin progreso (clase §2.7, opción c).** ✅ con un hueco. El loop principal ya frena una llamada de lectura con
los mismos argumentos que otra exitosa del turno (`graph.py:294-310`, `499-509`), y el sub-loop devuelve el resultado
anterior con `ya_buscado: true` (`sub_loop.py:312-359`). Lo que no se detecta en el loop principal es volver a buscar,
dentro de otra llamada a `buscar_renglones`, un renglón que **ya buscó la precarga**: la clave compara la llamada
entera (`graph.py:308-309`) y la nota de búsqueda no es un `ToolMessage`. El prompt se lo pide («no los vuelvas a
buscar igual», `precarga.py:90-92`), pero no lo controla el código. El sub-loop sí lo hace, renglón por renglón y
contra la precarga. Ver M-3.

### 2.3 Diseño de tools (clase §3)

| Principio | Estado | Evidencia |
|---|---|---|
| La descripción es prompt (§3.1) | ✅ Descripciones largas: qué devuelve, cuándo usarla, qué hacer con cada campo | `tool_registry.py:641-762` (ej. `search_products`, `buscar_renglones`) |
| Schema con `required`, rangos y descripción por parámetro (§3.2) | ✅ Pydantic con `Field(description=…, ge=…, le=…)` | `agent_schema.py:51-147` |
| `enum` para los valores que existen (§3.2) | 🟡 Hay `enum`/`Literal` en `kind`, `motivo`, `reason`; **faltan** en nombres de skills y de BR | `agent_schema.py:21-27`, `33`, `321` vs `150-155`, `252` |
| Validar antes de ejecutar (§3.3) | ✅ `model_validate` antes de correr; permisos por skill | `tool_registry.py:920-930` |
| Error útil como observación (§3.4) | ✅ Código + qué hacer; el system prompt pide seguir `error.message` | `tool_registry.py:825-846`, `skill_tools.py:22-56`, `system_prompt.md:34` |
| Resultado vacío útil (§2.7) | ✅ `_hint` con el porqué y qué hacer | `tool_registry.py:155-160`, `206-221`, `249-258` |
| Datos verificables y que no se filtren (§3.4) | ✅ El LLM no ve el stock exacto salvo que no alcance y el cliente haya nombrado esa cantidad (RF-125) | `tool_registry.py:118`, `133-141`, `260` |
| Pocas tools, orientadas a tareas (§3.5) | 🟡 15 tools, 8 siempre disponibles; hay solapamientos | `tool_registry.py:765-768` |
| Buscar en vez de listar (§3.5) | ✅ Búsqueda con `limit`; `explorar_catalogo` resume por rubro | `agent_schema.py:59`, `147` |
| Idempotencia en las que escriben (§3.7) | ✅ (ver detalle abajo) | `order_service.py:274-355`, `models.py:56-63`, `159` |

**Solapamientos (clase §3.5).** `get_product` y `check_stock` repiten lo que ya trae la búsqueda:
`search_products` con `quantity` devuelve `alcanza` y `disponible` (`tool_registry.py:133-141`), y la propia skill
dice que `check_stock` «hace falta solo si…» (`skills/tomar_pedido/skill.md:10`). Cada tool de más ocupa contexto
en todas las llamadas y es una opción más para equivocarse (paso 1 de la clase §2.2). Antes de sacar nada hay que
medir cuánto se usan (M-9).

**Nombres.** Mezcla de inglés y castellano (`search_products`, `respond_to_customer` junto a `buscar_renglones`,
`armar_pedido`). Es menor y renombrar rompe trazas y tests: queda fuera de alcance (§4).

**Idempotencia, en detalle.**

- ✅ `confirm_draft` busca primero una orden con ese `draft_id` y, si otro proceso gana la carrera, la
  `IntegrityError` devuelve la existente (`order_service.py:284-286`, `349-351`); `draft_id` es único
  (`models.py:63`).
- ✅ Borradores únicos por `(draft_id, version)` (`models.py:159`).
- ✅ Deduplicación de mensajes entrantes de Telegram por `update_id`, en una tabla persistente
  (`telegram_service.py:166-178`).
- ❌ WhatsApp todavía no procesa mensajes: el webhook valida la firma y deja un `TODO` para deduplicar por id de
  mensaje (`whatsapp_webhook.py:44-45`). Cuando se implemente, necesita lo mismo que Telegram (M-12).
- ✅ La escritura en Sheet 2 no se repite en un reintento (`sheets_service.py:392-398`).
- ✅ Dos tools terminales en la misma respuesta: la segunda recibe `turno_cerrado` (`graph.py:523-526`).
- ✅ El nodo `pausa` no tiene efectos antes de `interrupt()` (`graph.py:573-574`), que es lo que exige LangGraph al
  retomar.
- 🟡 Preventivo: `escalate_to_human` hoy solo cambia el estado. Cuando avise al equipo (handoff, fuera del MVP según
  `docs/architecture.md`), va a mandar mensajes y necesita clave de idempotencia (M-12).

### 2.4 Verificadores y «terminé no prueba que terminó» (clase §3.6, §4.3, §4.7)

Es lo más fuerte del proyecto, y el PR #115 lo reforzó. La condición de salida del loop la decide el modelo (llama a
una tool terminal), pero **el backend no le cree**:

| Lo que el modelo «dice» | Quién lo verifica | Dónde |
|---|---|---|
| «Te propongo estos productos» | Cada SKU llegó al LLM en el turno (`sku_no_buscado`), entre varias opciones elige el cliente (`opcion_sin_elegir`), la cantidad es la del renglón (`cantidad_distinta`) y el stock alcanza | `evidencia.py`, `tool_registry.py:384-470`, RF-112 a RF-114 |
| «Pedido confirmado, número X» | Regla `pedido_hecho`: no puede decirlo; el mensaje de la orden lo arma el sistema | `guardrails.py:475`, `validation_service.build_order_message` |
| «El cliente aceptó» | El último mensaje tiene que ser una aceptación pura (`aceptacion_no_clara`), y se revalidan stock y precio con lectura fresca antes de registrar | RF-111, `order_service.py:288-307` |
| Precios, medidas, stock, promesas, formato | Reglas deterministas con crítica accionable y 2 reintentos (evaluador-optimizador); precios y medidas tienen que haberse visto | `guardrails.py:530`, `614`, `645`, `683`, `778-789`; `MAX_RECHAZOS=2` en `guardrails.py:28` |
| «Resolví estos renglones» (sub-loop) | `nota.validar` y `_sin_elegir`: cada renglón en un solo lugar, SKU vistos, dudas de lectura, sin elegir por el cliente | `nota.py:21`, `sub_loop.py:412-463` |
| «Cubrí todo el pedido» | `cubrir_renglones`: ningún renglón queda sin cuenta | `tool_registry.py:399` |

Es el patrón **evaluador-optimizador** de la clase §4.7 con evaluador **determinista**, que la clase recomienda
cuando el criterio se puede chequear en código: más barato y confiable que un LLM juez. Cuando el sistema resuelve
en lugar del modelo (mensaje fijo, cierre, confirmación pedida, control tolerado), queda en `rescates` (RF-123).

**Brechas:**

- ❌ **La línea de base no se corrió.** Las herramientas ya están: tres sets (`mensajes`, `pedidos` y
  `adversariales`, con un grupo `reservado` que no se usa para ajustar, D-50), tope de llamadas y costo (RF-117),
  intervalo de Wilson (`evaluar_agente.py:225`) y `comparar_corridas` (RF-124). Pero `MEJORAS.md` sigue sin una
  corrida completa: la evaluación con Gemini quedó incompleta por cuota, y lo único medido son 28 turnos locales con
  modelos mezclados («sirve para ver el orden de magnitud, no para comparar»). Sin medición, la condición «el éxito
  se puede medir» del árbol de la clase §4.13 está **diseñada pero no cumplida**, y no hay forma de decidir el tope
  (D-51), el orquestador ni las mejoras de diseño de esta spec.
- 🟡 Lo que mide la evaluación no alcanza para decidir el tope ni el caché: ver la brecha 2 de §2.2.
- 🟡 La evaluación corre fuera del CI (`evaluar_agente.py:3`). Es razonable porque usa el LLM real. El buscador sí
  tiene un piso en el CI (`evaluar_busqueda`), pero no hay ningún chequeo automático que avise si un cambio de prompt
  empeora los casos del agente. Hoy la persona es el lazo (clase §2.6).

### 2.5 Patrón adecuado: workflow vs agente (clase §4)

**Árbol de «cuándo no hace falta un agente» (clase §4.13), aplicado a los tipos de turno:**

| Tipo de turno | ¿Una llamada alcanza? | ¿Pasos escribibles? | Hoy | Comentario |
|---|---|---|---|---|
| «dale» con propuesta vigente | — | Sí | ✅ Sin LLM (`graph.py:598`) | Hoja «workflow» |
| Fuera de tema evidente, cancelación | — | Sí | ✅ Sin LLM (`conversation_service.py:295`, `305-314`) | Hoja «workflow» |
| Saludo | Sí | — | 🟡 Router elige `informar`, pero igual hay loop | Podría ser una sola llamada forzada |
| Lista donde la precarga resuelve **todos** los renglones como `unico` | Casi: solo falta redactar | Sí | ❌ Va al loop agéntico igual | Ver M-5 |
| Lista con dudas, aproximados o faltantes | No | No: no se sabe cuántas búsquedas | ✅ Loop con topes | Acá sí se justifica el agente |
| Registrar la orden | — | — | ✅ Gate humano (el cliente acepta) y control del backend (RF-111) | Hoja «persona que aprueba» |

**Ruteo (clase §4.4).** ✅ Por tipo de tarea: `skill_clara` + palabras clave. 🟡 **No hay ruteo por costo**: el
mismo modelo atiende un saludo y una lista de 40 renglones (`llm.py:14-68`). Ver M-11.

**ReAct, plan-and-execute, ADaPT (clase §4.9–§4.11).** El loop principal es ReAct. La precarga y
`buscar_renglones` le agregan un «plan» por lotes: todos los renglones se buscan juntos. El orquestador ya tiene
algo de **ADaPT**: solo delega lo que la precarga no resolvió y puede volver a delegar lo que quedó trabado
(`internal/agent/orquestador.md:5-7`). 🟡 Lo que falta es **correr** la comparación contra el loop simple: la
herramienta ya existe (`evaluar_agente --orquestador si|no` y `comparar_corridas`, que dice si cumple RNF-17), pero
el orquestador sigue apagado sin evidencia en ningún sentido. La clase §4.11 muestra que el patrón puede mover el
éxito de 43% a 72% con el mismo modelo, pero también que depende del entorno. Ver M-10.

**Reflexión (clase §4.10).** ➖ En tiempo de ejecución no hace falta: el validador ya devuelve la crítica y el
modelo corrige en el mismo turno. 🟡 Offline sí aplica: cada falla de la evaluación debería terminar en una
lección **curada por una persona** (una BR nueva o corregida y un caso de regresión). Hoy eso se hace a mano y sin
registro. Ver M-8.

**Graph engineering (clase §4.14).** ✅ Es el diseño del proyecto: nodos y aristas fijas del programador
(`graph.py:645-676`), aristas condicionales con funciones deterministas (`decidir`, `decidir_tras_pausa`,
`ruta_falla`) y ciclos con tope (`llm ⇄ tools`, rechazos del validador, sub-loops). La autonomía queda localizada
en el nodo `llm`.

### 2.6 System prompt vs skill vs tool vs MCP (clase §5.2, §5.3)

| Pieza | Estado | Dónde |
|---|---|---|
| System prompt solo con reglas de toda tarea | 🟡 Casi todo es de toda tarea; dos párrafos aplican solo a imagen o audio | `system_prompt.md:28-30` |
| Skill = nombre + descripción como clasificador | ✅ `funcion` + `cuando` en el índice, siempre en contexto | `context.py:42-50`, `skill_service.py:42-43` |
| Carga progresiva | ✅ Índice siempre; cuerpo con `leer_skill`; BR como archivos aparte con `leer_br` | `skill_tools.py:22-56` |
| Procedimiento con ramas explícitas | ✅ Pasos y casos | `skills/tomar_pedido/skill.md:9-26` |
| **Criterios de cada paso** (clase §5.2, parte 3) | 🟡 Están en el backend (guardrails, evidencia, nota), no en la skill: el modelo se entera cuando lo rechazan | ver M-7 |
| Qué deja escrito | ✅ La tool terminal deja la propuesta o la orden en la base | `tool_registry.py:458-466` |
| «Skill que el agente no activa» → instrucción explícita | ✅ `encaminar` preactiva la skill cuando la ruta es clara | `graph.py:155-182`, `context.py:53-72` |
| Skills curadas, no auto-generadas (SkillsBench, clase §5.1) | ✅ Las escribe el equipo | `internal/skills/` |
| Cada skill define sus tools y sus BR | ✅ Y se valida al arrancar | `tool_registry.py:801-812` |

`tomar_pedido` es `modo: sub_loop`: con el orquestador apagado el modelo lee `skill.md`; con el orquestador prendido
lee `respuesta.md` y el sub-loop lee `sub_loop.md` (`skill_service.py:112-116`). Lo que se agregue a la skill tiene
que estar en los tres lugares que correspondan.

### 2.7 Memoria (clase §5.4–§5.6)

| Tipo | Qué hay en Norma | Quién la escribe | Estado |
|---|---|---|---|
| Corto plazo | Mensajes del turno; las tools de turnos anteriores se descartan a propósito | El loop | ✅ `context.py:186-200`, `system_prompt.md:32` |
| De trabajo / progreso de la tarea | Renglones numerados con su estado (`resuelto`, `pendiente`, `afuera`) en el checkpoint | El backend | ✅ `context.py:94-134`, `state.py` |
| Episódica | Contexto del cliente: últimos 3 pedidos y SKU comprados, armado **sin LLM** una vez por día | El backend, desde la base | ✅ `contexto_cliente.py:1-22` |
| Semántica | Catálogo (por búsqueda) y `datos_negocio.md` | Otros (solo lectura) | ✅ «El buscador ya es memoria» (clase §5.5) |
| Procedural | Skills y BR | El equipo | ✅ |

Lo más valioso: **el agente no escribe memoria propia**. Todo lo que recuerda sale de la base (pedidos reales) o
lo curó una persona. Evita el riesgo de la clase §5.5 (anotar algo falso y leerlo como verdad la sesión
siguiente) y el de la clase §7, nota 12 (adoptar salidas corruptas).

➖ Preferencias del cliente escritas por el agente (estilo `anotar`): hoy no hacen falta y tienen el riesgo de
arriba. Si el negocio las pide, conviene derivarlas de los pedidos o que las escriba un operador (P-5).

El archivo de progreso del repo (`MEJORAS.md`, clase §1.4) está al día después del PR #115.

### 2.8 MCP vs HTTP directo (clase §6)

➖ Norma no usa MCP, y la clase §6.2 dice que está bien: **un agente, tools propias** → función local. Las otras
integraciones también siguen la tabla de la clase:

| Integración | Elección actual | Según la clase §6.2 |
|---|---|---|
| Tools del agente | Funciones locales en `internal/tools/` | ✅ Un agente, pocas tools propias |
| Google Sheets | `gspread` directo (`google_sheets_client.py`) | ✅ Backend sin modelo |
| Reintento de sincronización | Función del backend (`order_service.py:483`) | ✅ Cron sin modelo |
| Dashboard | API HTTP propia (`api/dashboard.py`) | ✅ |
| Sensor de calidad | Comando que lee trazas y base (`sensor_calidad.py`) | ✅ Cron sin modelo |

MCP tendría sentido recién si el **equipo** (no el cliente) quisiera consultar pedidos o catálogo desde otro host,
como Claude Desktop: un servidor MCP de solo lectura que reutilice los mismos servicios. Queda fuera de alcance
(§4).

### 2.9 Tests y notebooks

- ✅ 1.532 casos de test (754 funciones en `tests/`, aislados de red y de `.env` por `tests/conftest.py`), con modelos
  falsos para el grafo (`test_agent_graph.py`, `test_orquestador.py`), tests de guardrails, evidencia, tools, skills,
  sensor e invariantes.
- ➖ No hay notebooks en el repo.

---

## 3. Mejoras propuestas, priorizadas

### 3.1 Resumen

| # | Mejora | Concepto de la clase | Impacto | Esfuerzo | Fase |
|---|---|---|---|---|---|
| M-1 | Medir lo que se topea: iteraciones con sub-loops por turno, motivo del cierre, rechazos por regla y tokens cacheados | §2.4, §2.5, §2.6 | Alto | Bajo | 1 |
| M-2 | Correr la línea de base completa | §4.13 («el éxito se puede medir»), §1.3 | Alto | Medio (cuota) | 1 |
| M-3 | No volver a buscar en el loop principal lo que ya buscó la precarga | §2.7 (opción c), §2.4 | Medio | Bajo | 2 |
| M-4 | Topes del turno fijados con datos: iteraciones, tokens y plazo | §2.4, §2.5, §2.6 | Alto | Bajo | 2 |
| M-5 | Camino determinista para pedidos que la precarga resolvió entero | §4.13, §4.12 | Alto (costo y latencia) | Medio | 2 |
| M-6 | Prefijo estable para el caché | §2.5 | Medio-alto (costo) | Medio | 2 |
| M-7 | Sección «Criterios» en cada skill | §5.2 (parte 3), §4.2 (gates) | Medio | Bajo | 2 |
| M-8 | Registro de lecciones curadas desde la evaluación | §4.10, §5.1 | Medio | Bajo | 1 |
| M-9 | Consolidar tools redundantes (tras medir uso) | §3.5 | Medio | Medio | 3 |
| M-10 | A/B: orquestador vs loop simple | §4.11, §4.12 | Alto (para decidir) | Bajo (con M-2) | 1 |
| M-11 | Ruteo por costo: modelo barato para turnos simples | §4.4 | Medio | Medio | 3 |
| M-12 | Idempotencia para los avisos al equipo y los mensajes de WhatsApp | §3.7 | Preventivo | Bajo | cuando lleguen |
| M-13 | Muestreo de conversaciones y métricas del sensor en el dashboard | §1.3 (nivel 5) | Medio | Medio | 3 |
| M-14 | `enum` dinámico en `leer_skill`, `leer_br` y `delegar_skill` | §3.2, §3.4 | Bajo-medio | Bajo | 2 |
| M-15 | Instrucciones de imagen y audio solo cuando hacen falta | §5.3 | Bajo-medio | Bajo | 2 |
| M-16 | Corregir la referencia del test de CA-20 | §1.4 | Bajo | Muy bajo | 1 |

Criterio de orden: primero lo que habilita decidir con datos (M-1, M-2, M-10), después lo que baja riesgo y costo
sin tocar el comportamiento visible (M-3, M-4), y al final lo que cambia el diseño y conviene comparar contra la línea
de base (M-5, M-6, M-9, M-11).

---

### M-1. Medir lo que se topea

**Concepto.** Clase §2.6: el lazo es tan bueno como su sensor. Para decidir el tope (§2.4) y el caché (§2.5) hay
que medir la misma cantidad que se controla, por turno y no solo en promedio.

**Cambio.**

- **Iteraciones del turno con sus sub-loops.** `sensor_calidad.medir_turnos` (`sensor_calidad.py:70-71`) y la
  evaluación suman `llamadas_sub_loop` a `iteraciones`, que es lo que cuenta `descubrir_tools` (`graph.py:249`).
  Se mantiene también la cifra del loop principal solo, para comparar con D-51.
- **Por turno en la evaluación.** `_evaluar` (`evaluar_agente.py:520`) guarda en cada fila la lista de iteraciones y
  de tokens de **todos** los turnos del caso, y `_metricas` (`evaluar_agente.py:246`) calcula `iteraciones_p95` e
  `iteraciones_max` sobre esos turnos, no sobre el último.
- **Motivo del cierre.** `descubrir_tools` guarda `motivo_cierre` en el estado (`graph.py:272`) y `resumir` deja el
  rescate como `cierre:<motivo>` (`observabilidad.py:89-90`). El sensor ya cuenta por prefijo (`con_rescate("cierre")`,
  `sensor_calidad.py:55-56`), así que `cierre_pct` sigue igual y se suma el desglose por motivo.
- **Rechazos por regla.** `resumir` cuenta las reglas que vienen en `error.details.reglas` de cada
  `respuesta_rechazada` (`guardrails.py:782-788`) y en la marca del aviso de texto suelto (`guardrails.py:791-800`).
- **Tokens cacheados.** `resumir`, `Paso.respuesta_llm` y el sub-loop (`sub_loop.py:251-253`) suman
  `input_token_details.cache_read`; el sensor y la evaluación reportan `tokens_cacheados_pct` (cacheados / entrada).

```python
# observabilidad.resumir (boceto)
uso = message.usage_metadata or {}
detalle = uso.get("input_token_details") or {}
if "cache_read" in detalle:
    resumen.tokens_cacheados = (resumen.tokens_cacheados or 0) + detalle["cache_read"]
```

**Criterio de aceptación.**

- `pytest tests/test_observabilidad.py -k cacheados`: un modelo falso con
  `usage_metadata={"input_tokens": 1000, "output_tokens": 10, "input_token_details": {"cache_read": 800}}` deja
  `tokens_cacheados == 800`; uno sin `input_token_details` deja `tokens_cacheados is None`.
- `pytest tests/test_evaluacion.py -k iteraciones_por_turno`: un caso de dos turnos con 7 y 2 iteraciones reporta
  `iteraciones_max == 7`, aunque el último turno tenga 2.
- `pytest tests/test_sensor_calidad.py -k sub_loop`: un turno con 2 iteraciones del orquestador y 8 de un sub-loop
  cuenta 10 en `iteraciones_p95`.
- `pytest tests/test_agent_graph.py -k tope_de_tokens`: el turno que cierra por tokens tiene el rescate
  `cierre:tokens`, extendiendo el test actual (`tests/test_agent_graph.py:748`).

**Riesgos.** Cada proveedor reporta el caché distinto, y algunos no lo reportan: si el campo no viene, registrar
`None` y no `0`, para no concluir «no cachea» cuando en realidad «no informa». Los umbrales del sensor (D-52) se
calcularon sin los sub-loops: revisarlos al cambiar la cuenta.

---

### M-2. Correr la línea de base completa

**Concepto.** Clase §4.13: un agente se justifica solo si el error se contiene **y el éxito se mide**. Clase §1.3:
para subir de nivel hace falta el verificador. Sin una corrida completa no se puede decidir el tope (D-51) ni ninguna
de las mejoras de diseño (M-4, M-5, M-6, M-9, M-10, M-11).

**Cambio.** Sin código nuevo, salvo M-1. Correr con un modelo que use tools de forma confiable y con cuota (el que
trae `.env.example`, salvo que P-2 diga otro), guardando las filas con `--salida` para poder compararlas después:

```bash
python -m internal.evaluar_agente --set pedidos --repeticiones 3 --salida data/evaluacion/pedidos.json --precio <entrada>,<salida>
python -m internal.evaluar_agente --set mensajes --repeticiones 3 --salida data/evaluacion/mensajes.json
python -m internal.evaluar_agente --set adversariales --grupo reservado --repeticiones 3 --salida data/evaluacion/reservado.json
```

Guardar el resumen de métricas (sin textos de conversaciones, según `AGENTS.md`) en
`docs/evaluacion/linea-de-base.md` con: modelo, `prompt_version` (`context.py:27-39`), fecha, costo y las métricas de
M-1. El grupo `reservado` es la medida honesta (D-50): no se ajusta nada mirando sus casos.

**Criterio de aceptación.** Existe `docs/evaluacion/linea-de-base.md` con `sin_evaluar == 0` en los tres conjuntos,
al menos 3 repeticiones por caso, y los valores de `aciertos_pct` con su intervalo, `casos_firmes_pct`,
`recall_lineas_pct`, `precision_lineas_pct`, `registros_indebidos`, `iteraciones_p95` (con sub-loops), el desglose de
`cierre:<motivo>`, `tokens_cacheados_pct` y el costo. D-51 se actualiza con esos números.

**Riesgos.** Costo y cuota (D-21): `--max-llamadas` acota la corrida. Un modelo distinto al de producción da una
línea de base que no transfiere: hay que fijar primero qué modelo se va a usar (P-2).

---

### M-3. No volver a buscar en el loop principal lo que ya buscó la precarga

**Concepto.** Clase §2.7: «repetir la misma búsqueda» es la falla típica de un loop sin progreso. Clase §2.4: cada
vuelta inútil es un paso más que se compone.

**Qué ya está.** Una llamada de lectura igual a otra exitosa del turno devuelve `llamada_repetida` sin ejecutarse, y
a la tercera el turno cierra (`graph.py:294-310`, `499-509`, CA-210).

**Qué falta.** Dos repeticiones que esa clave no ve:

- un renglón que buscó la **precarga** (está en la nota de búsqueda, no en un `ToolMessage`) y el modelo vuelve a
  pedir con `buscar_renglones`;
- un renglón que va de nuevo, con el mismo texto, dentro de una llamada a `buscar_renglones` que trae otros renglones:
  la llamada entera es distinta.

**Cambio.** Primero medir cuánto pasa: en las trazas de la línea de base (M-2), contar los `buscar_renglones` del
loop principal que incluyen renglones de la nota de búsqueda con el mismo texto, cantidad y tono. Si pasa, hacer en
`ejecutar_tools` (`graph.py:480`) lo mismo que el sub-loop: comparar renglón por renglón contra la precarga y las
búsquedas anteriores del turno con la clave del sub-loop (`sub_loop._clave`, `sub_loop.py:306-309`), buscar solo los
nuevos y devolver los repetidos con `ya_buscado: true` y un `_hint`. Conviene llevar `_clave` y `_buscar_renglones`
(`sub_loop.py:312-359`) a un módulo común y usarlos desde los dos loops.

**Criterio de aceptación.** `pytest tests/test_agent_graph.py -k busqueda_ya_precargada`: con la precarga que
resolvió el renglón 1, un modelo falso que llama `buscar_renglones` con los renglones 1 (mismo texto) y 2 produce
**una sola** búsqueda en `catalog_tools.buscar_renglones` (con un spy), solo del renglón 2, y el resultado trae el
renglón 1 con `ya_buscado: true`. `test_llamada_repetida_no_se_ejecuta` (`tests/test_agent_graph.py:780`) sigue
pasando.

**Riesgos.** Normalizar de más (por ejemplo, ignorar la cantidad) devolvería un resultado que no corresponde: la
clave tiene que incluir texto, cantidad y tono, como la del sub-loop. Un renglón servido de la precarga no tiene que
contar para `MAX_LLAMADAS_REPETIDAS`: no es una llamada repetida, es una llamada atendida.

---

### M-4. Topes del turno fijados con datos

**Concepto.** Clase §2.4 (pⁿ), §2.5 (costo cuadrático) y §2.6 («tope de pasos y de gasto»).

**Qué ya está.** El tope de tokens (`AGENT_MAX_TOKENS_PER_TURN`, RF-122) y el de llamadas repetidas. Lo que queda
abierto es el valor de cada tope (D-51).

**Cambio.**

1. Con la línea de base (M-2), fijar `AGENT_MAX_ITERATIONS` en un valor apenas por encima del **p95** de las
   iteraciones **con sub-loops** (M-1) de los turnos que salen bien. Si el p95 es 6, un tope de 10–12 deja margen sin
   pagar 50. El cierre con lo encontrado (`graph.py:250-272`) ya garantiza que el cliente recibe respuesta.
2. Revisar `AGENT_MAX_TOKENS_PER_TURN` con el mismo criterio: hoy es unas cuatro veces el p95 de las trazas locales
   (49.000, D-51).
3. Bajar el plazo de 300 s en proporción: con el lock por conversación, el plazo es tiempo en que el cliente no
   puede escribir, y RNF-06 pide un p95 de 10 s (P-4).
4. Actualizar D-07 y RNF-12 (`docs/spec/README.md:677`, `529`), RNF-16 y D-51 (`toma-de-pedidos-por-texto.md:624`,
   `722`) y `agente.md:113`, `128`, `564`.

**Criterio de aceptación.**

- `test_presupuesto_por_defecto` (`tests/test_agent_graph.py:824`) actualizado a los valores nuevos.
- En la evaluación repetida con los topes nuevos, `recall_lineas_pct` no baja más de 1 punto contra la línea de base y
  los cierres por `iteraciones` o `tokens` no pasan del 5% de los turnos.

**Riesgos.** Un tope bajo vuelve a cortar pedidos largos legítimos: por eso se fija con el p95 medido y no a ojo.
Con el orquestador, los sub-loops tienen su propio `max_iteraciones` (8, `skills/tomar_pedido/skill.md:7`): el
tope del turno tiene que ser mayor que eso más las vueltas del orquestador. Los topes se controlan entre llamadas:
el cierre suma hasta `ITERACIONES_CIERRE` llamadas más (`graph.py:47`).

---

### M-5. Camino determinista para pedidos que la precarga resolvió entero

**Concepto.** Clase §4.13: primero se agota lo simple; clase §4.12: si el camino se puede escribir, workflow.
Cuando la precarga ya encontró cada renglón como `unico`, no queda nada que decidir paso a paso: solo redactar la
propuesta.

**Cambio.** Nuevo nodo o rama después de `precarga_busqueda` (`graph.py:185-205`). Si se cumplen **todas** estas
condiciones:

- todos los renglones nuevos salieron `unico` en la nota de búsqueda (`unico` ya implica que el stock alcanza,
  `catalog_tools.py:136-140`);
- no hay propuesta vigente, o el mensaje es una lista nueva;
- el mensaje no trae texto fuera de los renglones (notas, preguntas, «sin bolsa»), ni renglones con duda de lectura;
- no se detectó intento de cambiar las reglas (`guardrails.detectar_intento`);

entonces armar la propuesta en el backend (cada SKU sale de la nota, así que es evidencia para RF-112) y hacer
**una sola** llamada al LLM, solo para redactar `message`, con `tools_disponibles=[respond_to_customer]`, igual que el
cierre (`graph.py:254`). La salida pasa por el mismo `validar_salida`. Variante más barata, a medir: un mensaje de
plantilla sin LLM.

La precarga solo numera mensajes de 2 renglones o más (`interpretation_service.py:15`): un pedido de un solo producto
no toma este camino.

**Criterio de aceptación.**

- `pytest tests/test_agent_graph.py -k camino_determinista`: un pedido de 3 renglones inequívocos con stock
  (`tests/datos_sinteticos.py`) produce una propuesta con esas 3 líneas, `iteraciones == 1` y ninguna llamada a
  `search_products` ni `buscar_renglones` desde el LLM.
- El mismo test con un renglón `aproximado` o `varias` **no** toma el camino determinista.
- En la evaluación, la fracción de turnos por este camino se reporta, y `precision_lineas_pct` no baja contra la
  línea de base.

**Riesgos.** Perder matices que el LLM habría visto («lo mismo que la otra vez pero…»). Por eso las condiciones son
estrictas y ante la duda va al loop. Si el texto sale de una plantilla, la voz se puede volver repetitiva: hay un
chequeo de cierres repetidos en la evaluación (`evaluar_agente.py:112-115`).

---

### M-6. Prefijo estable para el caché

**Concepto.** Clase §2.5: con caché de prefijo exacto el costo pasa de cuadrático a lineal, pero solo si se agrega
al final y no se cambian las tools ni el principio de la lista.

**Cambio, en dos pasos que se miden por separado (con M-1):**

1. **Separar lo fijo de lo dinámico.** Es el de más impacto, porque es lo que cambia entre turnos.
   `build_messages` (`context.py:203-219`) arma el `SystemMessage` solo con lo fijo (prompt + índice + orquestador si
   corresponde). La skill activa, el contexto del cliente, el estado y la nota de búsqueda van en un mensaje al
   **final** de la lista. Ese mensaje lo arma `build_messages` en cada llamada y no se guarda en el estado, así que no
   hace falta que `historial_previo` lo descarte. Tiene que ser un `HumanMessage` marcado como aviso del sistema,
   como el de `guardrails.aviso_texto_suelto` (`guardrails.py:791-800`), y no un segundo `SystemMessage`: hay que
   confirmar que cada proveedor de `llm.py` acepte un mensaje de sistema después del primero, y es probable que
   algunos no lo hagan.
2. **Tools constantes dentro del turno.** Mandar desde la primera vuelta los schemas de todas las tools que el turno
   puede llegar a usar (base + las de las skills del índice que se pueden leer con o sin propuesta), y dejar el
   permiso en el código, que ya rechaza lo no permitido con un error útil (`tool_registry.py:825-846`, `922-923`).
   `_tool_no_permitida` hoy detecta el cierre porque falta `leer_skill` (`tool_registry.py:829`): con la lista fija
   tiene que usar `ctx.cierre`, que ya existe. El cierre puede seguir dejando solo `respond_to_customer`: es raro y
   perder el caché ahí no pesa.

```text
Hoy:     [tools que pueden cambiar] [system: fijo + estado del turno] [historial] [turno]
Después: [tools fijas] [system: fijo] [historial] [turno] [aviso: estado del turno]
         └──────────── se cachea entre vueltas y entre turnos ────────────┘
```

**Criterio de aceptación.**

- `pytest tests/test_agent.py -k prefijo_estable`: dos llamadas de `build_messages` con distinto estado dan un
  `SystemMessage` idéntico byte a byte, y el estado está en el último mensaje.
- `pytest tests/test_agent_graph.py -k tools_constantes`: en un turno sin skill preactivada, con `leer_skill` y
  después `respond_to_customer`, la lista de schemas que recibe el modelo falso es **idéntica** en todas las llamadas.
- Con el proveedor elegido, `tokens_cacheados_pct` sube contra la línea de base, y `aciertos_pct` y
  `precision_lineas_pct` no bajan más de 1 punto.

**Riesgos.**

- Mostrar todas las tools desde el inicio contradice en parte la clase §3.5 (más tools a la vista, más chances de
  elegir mal) y el diseño actual de «las tools aparecen al leer la skill», que tuvo motivos medidos en `MEJORAS.md`
  (el modelo respondía sin leer la skill). Por eso va segundo y con A/B; si la preactivación ya cubre la mayoría de
  los turnos, puede no hacer falta.
- Mover el estado al final cambia dónde mira el modelo: algunos modelos atienden mejor al system prompt. Medir
  antes de adoptarlo.
- Si el proveedor no cachea (P-3), este cambio no ahorra nada y solo agrega riesgo: en ese caso, no hacerlo.

---

### M-7. Sección «Criterios» en cada skill

**Concepto.** Clase §5.2: sin criterios por paso, la skill es «una nota». Hoy los criterios existen, pero en el
backend: el modelo los descubre cuando lo rechaza un control, y cada rechazo es una vuelta más (§2.4, §2.5). Desde
el PR #115 hay más controles (`sku_no_buscado`, `opcion_sin_elegir`, `cantidad_distinta`, `aceptacion_no_clara`,
precios y medidas vistos), así que hay más para descubrir a fuerza de rechazos.

**Cambio.** Agregar una sección corta que diga **qué va a verificar el sistema**, con las mismas palabras que el
mensaje de rechazo, en cada archivo que lee el modelo que responde: `skill.md` de cada skill y, en `tomar_pedido`,
también `respuesta.md` (orquestador) y `sub_loop.md` (los criterios de `nota.validar`). Por ejemplo, en
`tomar_pedido`:

```markdown
## Criterios (los verifica el sistema antes de mandar tu respuesta)
- Cada renglón del pedido está en un solo lugar: `propuesta`, `pendientes` o `afuera`.
- Cada SKU salió de una búsqueda de este turno o de lo que ya le ofreciste; si hubo varias opciones, elige el cliente.
- La cantidad es la que escribió el cliente en el renglón.
- El mensaje no dice el stock salvo que no alcance (BR-01), no repite los precios y no promete fechas.
```

Un test valida que cada criterio nombrado tenga su control, para que no se desincronicen.

**Criterio de aceptación.**

- `pytest tests/test_skills.py -k criterios`: cada skill con tools terminales tiene la sección `## Criterios`, y
  `tomar_pedido` la tiene también en `respuesta.md` y `sub_loop.md`.
- En la evaluación, el promedio de rechazos por turno (M-1) baja contra la línea de base.

**Riesgos.** Más texto en la skill = más contexto cuando se lee. Mantenerla en 4–6 líneas. Si el modelo «aprende»
el criterio pero no la tarea (*reward hacking* leve, clase §4.3), los controles siguen igual: el criterio informa, no
reemplaza al verificador.

---

### M-8. Registro de lecciones curadas desde la evaluación

**Concepto.** Clase §4.10 (Reflexión: la lección después de un fallo medido) y §5.1 (SkillsBench: lo curado suma,
lo auto-generado resta). La combinación es: el fallo lo mide el evaluador, la lección la escribe **una persona**.

**Cambio.** Proceso, no runtime. Un archivo `docs/evaluacion/lecciones.md` con una fila por falla de la evaluación:

| Fecha | Caso | Qué falló (métrica) | Causa | Lección | Dónde quedó |
|---|---|---|---|---|---|
| 2026-11-11 | `acepta-dale` | Inventó el número de orden | Sin tool terminal disponible | La skill se lee antes de buscar | `system_prompt.md` paso 1, regla `pedido_hecho` |

Cada lección termina en un cambio curado (BR, skill, control o tool) **y** en un caso de regresión en
`data_example/mensajes.json`, `pedidos_evaluacion.json` o en el grupo `desarrollo` de `adversariales.json`. Una falla
del grupo `reservado` no se corrige mirando el caso: según D-50, ese caso pasa a `desarrollo` y se escribe otro para
`reservado`.

**Criterio de aceptación.** Chequeo de revisión del PR (en `.github/pull_request_template.md`): todo PR que cambie
`internal/skills/` o `system_prompt.md` por una falla de evaluación enlaza su fila en `lecciones.md` y el caso de
regresión. Verificable: `pytest tests/test_datos_ejemplo.py` y `pytest tests/test_evaluacion.py -k adversariales`
(CA-201) siguen pasando con los casos nuevos.

**Riesgos.** Burocracia. Mantenerlo en una tabla y solo para fallas medidas, no para cada ajuste de estilo.

---

### M-9. Consolidar tools redundantes, después de medir

**Concepto.** Clase §3.5: pocas tools, orientadas a tareas; cada tool ocupa contexto y es una opción más para
elegir mal.

**Cambio.**

1. Con la línea de base, contar en las trazas cuántas veces se llama cada tool y cuántas de esas llamadas aportaron
   algo que la búsqueda no traía (`resumen.tools` ya lo junta, `observabilidad.py:68`).
2. Si `check_stock` y `get_product` casi no se usan o se usan de forma redundante, sacarlas de `BASE_TOOLS`
   (`tool_registry.py:765-768`) y dejarlas solo para los casos que las necesitan (por ejemplo, `check_stock` en
   `confirmar_pedido` si cambió la cantidad).
3. Evaluar unificar `search_products` y `buscar_renglones` en una sola tool que reciba de 1 a N renglones. Es el
   cambio más grande: hacerlo solo si la medición muestra confusión entre las dos.

**Criterio de aceptación.** `pytest tests/test_tools.py -k base_tools` con la lista nueva, y en la evaluación
`aciertos_pct` igual o mejor y `tokens_entrada_por_llamada` menor que la línea de base.

**Riesgos.** Sacar una tool que sí resolvía casos raros. Por eso va primero la medición y después el cambio.
`evidencia.TOOLS` (`evidencia.py:19-21`) cuenta esas tools como fuente de evidencia: hay que actualizarlo junto.

---

### M-10. A/B: orquestador vs loop simple

**Concepto.** Clase §4.11: el patrón puede cambiar el éxito más que el modelo, pero depende del entorno; clase
§4.12: la menor autonomía que resuelva la tarea.

**Cambio.** Sin código: la herramienta ya está (RF-124). Correr la evaluación de M-2 con `--orquestador no` y
`--orquestador si`, mismo modelo y mismas repeticiones, y compararlas:

```bash
python -m internal.comparar_corridas data/evaluacion/pedidos.json data/evaluacion/pedidos-orquestador.json
```

**Criterio de aceptación.** La comparación queda en `docs/evaluacion/linea-de-base.md`, y la decisión en la spec del
orquestador: se prende si `comparar_corridas` dice que cumple RNF-17 y mejora `recall_lineas_pct` en pedidos largos
sin empeorar los cortos; si no, queda apagado y se documenta por qué.

**Riesgos.** Pocos casos dan diferencias que son ruido: `comparar_corridas` avisa cuando los intervalos se
superponen, y en ese caso no se decide con esa diferencia.

---

### M-11. Ruteo por costo: modelo barato para turnos simples

**Concepto.** Clase §4.4: «el caro, solo para los difíciles»; la cabeza de casos triviales la resuelve un modelo
chico.

**Cambio.** `llm.get_chat_model(nivel: Literal["simple", "completo"])` con `LLM_MODEL_SIMPLE` opcional en
`config.py`. `encaminar` ya sabe la skill del turno (`graph.py:163`): `informar` y `confirmar_pedido` con mensaje
corto van al modelo simple; `tomar_pedido`, al completo. Sin `LLM_MODEL_SIMPLE`, todo sigue como hoy.

**Criterio de aceptación.** `pytest tests/test_llm.py -k nivel`: con `LLM_MODEL_SIMPLE` configurado, un saludo usa
el modelo simple y una lista de 3 renglones el completo. En la evaluación, el conjunto `mensajes` no baja
`aciertos_pct` y el costo estimado (`--precio`) baja.

**Riesgos.** El modelo simple puede usar mal las tools (ya pasó con modelos `:free`, `MEJORAS.md`). Un error del
router manda un caso difícil al camino barato: los controles de salida siguen siendo la red. Se superpone con M8-4
(«un modelo por nodo») de la spec de la clase 8 (PR #118): conviene que una sola de las dos specs la lleve (P-8).

---

### M-12. Idempotencia para los avisos al equipo y los mensajes de WhatsApp

**Concepto.** Clase §3.7: las tools que escriben o envían necesitan clave de idempotencia generada por el programa,
no por el modelo; y un canal que reintenta necesita deduplicar.

**Cambio.**

- Cuando `escalate_to_human` empiece a notificar (handoff, v2), registrar cada aviso en una tabla con
  `UniqueConstraint` sobre una clave `sha256(conversation_id, tool_call_id)` (o `conversation_id` + motivo + día, si
  el mismo turno se puede reintentar con otro `tool_call_id`), igual que `processed_messages`
  (`telegram_service.py:166-178`).
- Cuando WhatsApp procese mensajes, deduplicar por id de mensaje en la misma tabla, como dice el `TODO` de
  `whatsapp_webhook.py:44-45`: Meta reintenta si no recibe un 200 rápido.

**Criterio de aceptación.** `pytest tests/test_resolucion_derivaciones.py -k aviso_idempotente`: ejecutar dos
veces la derivación con la misma clave manda un solo aviso (spy sobre el canal). Para WhatsApp, el mismo webhook
recibido dos veces llega una sola vez al agente.

**Riesgos.** Ninguno relevante; es preventivo.

---

### M-13. Muestreo de conversaciones y métricas del sensor en el dashboard

**Concepto.** Clase §1.3, nivel 5: cuando no se revisa cada caso, se revisan **muestras y agregados**.

**Qué ya está.** Los agregados, en `sensor_calidad` (RF-123): error, rescates, derivaciones, latencia, costo,
correcciones por pedido y cancelados, con umbrales (D-52).

**Cambio.**

- Un comando `python -m internal.muestrear_conversaciones --semana` que elija conversaciones estratificadas por
  resultado (`pedido`, `derivada`, `error`, con cierre, con rescates) a partir de los mismos datos del sensor, para
  que el equipo revise casos dentro del sistema.
- Un panel en el dashboard con lo que calcula `sensor_calidad.medir_turnos` por día, reutilizando esa función.

**Criterio de aceptación.** `pytest tests/test_dashboard.py -k metricas_agente` con datos sintéticos; el muestreo
devuelve al menos una conversación de cada estrato presente.

**Riesgos.** Privacidad: en producción las conversaciones son de clientes reales. La revisión se hace dentro del
sistema, con acceso restringido, y nada se copia a issues ni PRs (`AGENTS.md`, regla 2).

---

### M-14. `enum` dinámico en `leer_skill`, `leer_br` y `delegar_skill`

**Concepto.** Clase §3.2: `enum` acota los valores a los que existen; clase §3.4: el error útil es la red, no el
primer recurso.

**Cambio.** Hoy `LeerSkillInput.nombre`, `LeerBrInput.id` y `DelegarSkillInput.skill` son `str`
(`agent_schema.py:150-155`, `252`). Generar el schema con `enum` a partir de `get_skills()` al arrancar, en
`tool_registry.schemas` (`tool_registry.py:791-798`), sin cambiar los modelos Pydantic (la validación de permisos
sigue en `skill_tools.py`):

- `leer_skill`: todas las skills;
- `leer_br`: todas las BR de todas las skills;
- `delegar_skill`: solo las skills con `modo: sub_loop`.

```python
# tool_registry.schemas (boceto)
if name == "delegar_skill":
    skills = sorted(s.nombre for s in get_skills().values() if s.modo == "sub_loop")
    tool["function"]["parameters"]["properties"]["skill"]["enum"] = skills
```

**Criterio de aceptación.** `pytest tests/test_tools.py -k enum_skills`: el schema de `leer_skill` lista exactamente
las skills cargadas, el de `leer_br` todas las BR y el de `delegar_skill` solo `tomar_pedido`. Los errores
`skill_inexistente` y `br_inexistente` siguen funcionando si el proveedor ignora el `enum`.

**Riesgos.** El `enum` de `leer_skill` nombra `confirmar_pedido` aunque no haya propuesta, cuando el índice la
esconde. `skill_tools.leer_skill` ya la devuelve igual hoy si se la piden por nombre (`skill_tools.py:22-28`); lo que
protege es que `armar_pedido` sin propuesta no registra nada (`sin_propuesta`, CA-42). Si eso no alcanza, el `enum`
de `leer_skill` se arma sin las skills que requieren propuesta, al costo de que cambie entre turnos (M-6). El
`enum` de BR incluye las de skills no leídas: el permiso lo sigue dando `leer_br`. Si M-6 fija las tools, el `enum`
también tiene que ser estable dentro del turno (lo es: sale del índice cargado al arrancar).

---

### M-15. Instrucciones de imagen y audio solo cuando hacen falta

**Concepto.** Clase §5.3: el system prompt es para reglas de **toda** tarea, porque se paga en cada llamada.

**Cambio.** Mover los dos párrafos de `system_prompt.md:28-30` a `internal/agent/entrada_imagen.md` y
`entrada_audio.md`, y sumarlos al bloque dinámico solo si el mensaje del turno trae la marca. Las marcas ya existen:
`[audio transcripto]` (`message_service.py:14`) y la de imagen, que tiene dos formas, `[imagen leída]` y
`[imagen leída: <tipo>]` (`vision_schema.py:104-105`), así que hay que buscar por el comienzo, `[imagen leída`.

**Criterio de aceptación.** `pytest tests/test_agent.py -k instrucciones_imagen`: un turno de texto no las incluye y
uno con «[imagen leída]» o «[imagen leída: lista]» sí. `tests/test_lectura_al_agente.py` sigue pasando.

**Riesgos.** Si va dentro del `SystemMessage`, rompe el prefijo fijo de M-6: tiene que ir en el bloque dinámico del
final.

---

### M-16. Corregir la referencia del test de CA-20

**Concepto.** Clase §1.4: «lo que no se anota no pasó», y lo que se anota mal confunde.

**Cambio.** CA-20 (`docs/spec/README.md:584`) apunta a `test_agent_graph.py::test_tope_de_iteraciones`, que no
existe. El test que cubre ese caso (un modelo que tampoco responde en el cierre corta con el mensaje fijo y suma una
falla) es `test_tope_sin_respuesta_corta_con_mensaje_fijo`.

**Criterio de aceptación.** `git grep -n "::test_tope_de_iteraciones\`" docs/spec` no devuelve nada, y cada test que
nombra la tabla de CA de `docs/spec/README.md` existe en `tests/`.

**Riesgos.** Ninguno.

---

## 4. Fuera de alcance

- **MCP.** Un solo agente con tools propias: según la clase §6.2 sería un pasamanos. Se reconsidera si el equipo
  quiere consultar pedidos o catálogo desde otro host (Claude Desktop, VS Code).
- **Memoria escrita por el agente** (`anotar` / `leer_notas`). El riesgo de la clase §5.5 supera el beneficio hoy;
  ver P-5.
- **Multiagente más allá del orquestador actual.** Primero hay que medir si el orquestador aporta (M-10).
- **Renombrar tools** para unificar idioma: rompe trazas, tests y comparaciones con la línea de base por un
  beneficio menor.
- **LLM como juez** de las respuestas: el criterio se puede chequear en código y ya está en `guardrails` y
  `evidencia`; un juez LLM es más caro y menos confiable (clase §4.7).
- **Votación** (varias corridas por turno, clase §4.5): multiplica el costo y no protege contra errores
  sistemáticos.
- **Cambios de canal, Google Sheets, dashboard** que no tengan que ver con el diseño del agente.

## 5. Plan de implementación por fases

**Fase 1: medir (habilita todo lo demás).** M-1, M-16, M-8 (el archivo y el proceso), y las corridas de M-2 y M-10.
Ninguna cambia lo que ve el cliente. Salida: `linea-de-base.md` y D-51 con números.

**Fase 2: bajar riesgo y costo, comparando contra la línea de base.** M-4 (topes, con los números de la fase 1),
M-3 (si la medición muestra que pasa), M-14, M-15, M-7, después M-5 y por último M-6 (el de más riesgo de calidad, y
solo si el proveedor cachea). Cada una se mide con la misma corrida de evaluación antes de integrarse.

**Fase 3: decisiones de diseño.** M-9 (consolidar tools), M-11 (ruteo por costo) y M-13 (muestreo y panel). M-12
entra cuando se implemente el aviso al equipo o el canal de WhatsApp.

Regla para todas las fases: un cambio que baja `recall_lineas_pct` o `precision_lineas_pct` más de 1 punto, o que
sube `registros_indebidos` por encima de 0 (`evaluar_agente.py:51-52`), no se integra.

## 6. Preguntas abiertas

- **P-1.** Las mejoras que se aprueben, ¿se convierten en RF y CA dentro de las specs existentes (`agente.md`,
  `orquestador-y-sub-loops.md`, `toma-de-pedidos-por-texto.md`, `README.md`), o en una spec propia con la plantilla
  `_plantilla.md`?
- **P-2.** ¿Qué modelo y proveedor se va a usar en producción? La línea de base, el caché y el ruteo por costo
  dependen de eso. `.env.example` ahora trae `gpt-6-luna` de OpenAI.
- **P-3.** ¿El proveedor elegido hace caché de prefijo y lo informa en `usage_metadata`? Si no, M-6 no tiene sentido.
  M-1 lo responde con datos.
- **P-4.** ¿Por qué el plazo es de 300 s? Se explica el salto de 8 a 50 iteraciones, pero no si 300 s es aceptable
  para el negocio, sabiendo que la conversación queda bloqueada mientras tanto (RNF-06 pide p95 de 10 s, y las trazas
  locales dieron 69 s).
- **P-5.** ¿El negocio quiere que el bot recuerde preferencias del cliente («retira los martes», «factura A»)? Si sí,
  ¿las escribe un operador o se derivan de los pedidos?
- **P-6.** ¿Qué fracción de los pedidos reales son listas que la precarga resolvería enteras? Define cuánto vale
  M-5. Se puede estimar con `pedidos_evaluacion.json` sin tocar datos reales.
- **P-7.** El orquestador está apagado por defecto. ¿Hay alguna medición, aunque sea parcial, que motivó
  construirlo? Si existe, conviene sumarla a la línea de base de M-2.
- **P-8.** M-11 (ruteo por costo) y M8-4 de la spec de la clase 8 (PR #118, «un modelo por nodo») proponen lo mismo
  desde dos lados. ¿Cuál de las dos specs lo lleva?
