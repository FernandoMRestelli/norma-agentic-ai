# Spec de mejoras: diseño agéntico de Norma

> **Estado:** propuesta para discutir. No hay nada implementado. Diagnóstico hecho sobre `develop` (commit
> `96943c7`, versión 0.2.0): las referencias `archivo:línea` son de ese commit.
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
| «Cada cita existe en el corpus» (verificador de citas) | «Cada SKU salió de una tool» (`guardrails.validar_salida`, `nota.validar`) |
| Acción irreversible → aprobación humana | Registrar la orden → solo si **el cliente** acepta la propuesta |
| Índice de normas = memoria semántica de solo lectura | Catálogo + `datos_negocio.md` |
| Skill `informe-comparado-normas` | Skills `tomar_pedido`, `confirmar_pedido`, `informar`, `derivar_o_cancelar` y sus BR |

### 1.2 Conclusión general, en una línea

Norma ya aplica la mayoría de las ideas de la clase (grafo con ruteo, validación antes de ejecutar, errores
útiles, verificadores deterministas, idempotencia, aprobación humana antes de escribir). Las brechas que quedan
son de **medición y de costo**: el tope de 50 iteraciones, el caché de prefijo que se rompe en cada turno, las
búsquedas repetidas del loop principal y, sobre todo, que todavía no hay una línea de base medida que permita
decidir con datos.

---

## 2. Estado actual: diagnóstico concepto por concepto

Leyenda: ✅ aplicado · 🟡 aplicado en parte · ❌ falta · ➖ no aplica (y está bien que no aplique).

### 2.1 Ubicación en el gradiente de autonomía (clase §1.2, §1.3, §4.8)

**Gradiente de arquitectura (quién decide el camino).** Norma no es un loop libre: es un **grafo** donde el
código decide la mayor parte del camino y el modelo decide solo en un nodo.

| Tramo | Quién decide | Dónde |
|---|---|---|
| Fuera de tema, ráfaga, mensaje largo, cancelación con borrador | Código (regex), sin LLM | `conversation_service.py:246-310`, `topic_filter.py:37`, `guardrails.py:812` |
| Aceptación exacta («dale», «ok») con propuesta vigente | Código, sin LLM | `graph.py:503-531` (`confirmar`, `decidir_tras_pausa`) |
| Qué skill usar cuando el mensaje no admite dudas | Router determinista | `graph.py:120-136` (`skill_clara`), `graph.py:152-179` (`encaminar`) |
| Buscar renglones inequívocos | Código, sin LLM | `graph.py:182-202` (`precarga_busqueda`) |
| Qué buscar, qué preguntar, cómo responder | **El modelo**, paso a paso (④ loop con tools) | `graph.py:352-396` (`llm`), `graph.py:398-475` (`tools`) |
| Con `AGENT_ORQUESTADOR=true`: a qué sub-loop darle qué renglones | El modelo orquestador (③/⑤) | `tool_registry.py:322-352`, `sub_loop.py:168-293` |

Con el flag apagado (default, `config.py:63`) está en **④ loop con tools dentro de un grafo con ruteo**. Con el
flag prendido se acerca a **⑤**: el orquestador decide «a quién le da trabajo», aunque hay un solo tipo de
trabajador.

**Gradiente de delegación (qué verifica la persona).** Usando los dos datos de la clase §1.3:

- *Costo del error:* medio. Una orden equivocada compromete stock y la tiene que cancelar un operador
  (`api/orders.py`). No es irreversible, pero tiene costo operativo.
- *Con qué se verifica:* cada respuesta pasa por verificadores deterministas (§2.4); la escritura irreversible
  (registrar la orden) la aprueba **el cliente**; el equipo mira agregados en el dashboard.

Queda en un **nivel 3–4 híbrido**: nivel 4 para «la respuesta cumple las reglas» (el agente itera hasta pasar el
validador) y nivel 3 con *gate* humano para la escritura. ✅ Es coherente con el árbol de la clase §4.13.

**Brecha:** 🟡 el nivel 5 («muestras y agregados») está a medio camino: MLflow guarda trazas
(`observabilidad.py`), pero no hay un muestreo sistemático de conversaciones ni métricas del agente en el
dashboard (rechazos por regla, cierres por tope, derivaciones por fallas). Ver M-13.

### 2.2 Las cinco piezas del loop y el tope (clase §2.3–§2.6)

| Pieza | Estado | Dónde |
|---|---|---|
| 1. Mensajes en una lista | ✅ Estado de LangGraph con checkpoints en SQLite | `state.py`, `graph.py:550-581` |
| 2. Llamada con lista + tools | ✅ `tool_choice="any"`: siempre pide una tool | `graph.py:357-359` |
| 3. Ejecutar (lo hace el programa) | ✅ Con permisos por skill y datos inyectados que el LLM no ve | `tool_registry.py:54-84`, `716-740` |
| 4. Devolver la observación | ✅ Sobre común `{ok, data | error}` enlazado por `tool_call_id` | `graph.py:432-434` |
| 5. Repetir con tope | 🟡 Tope de iteraciones y de tiempo, cierre «con lo encontrado»; **sin tope de gasto** | `graph.py:241-265`, `config.py:60-61` |

Lo bueno: al llegar al tope **no finge una respuesta** (clase §2.3): deja solo `respond_to_customer` y avisa en
el estado que responda con lo encontrado (`context.py:168-175`); si tampoco responde, mensaje fijo y suma una
falla (`graph.py:247-250`). Las vueltas de los sub-loops cuentan para el mismo tope (`graph.py:246`).

**Brecha 1: el tope es muy alto.** ❌ `AGENT_MAX_ITERATIONS=50` y `300 s` (`config.py:60-61`, `.env.example:55-56`).
Era 8 y se subió a 50 porque los pedidos largos cortaban con «Tuve un problema» (`CHANGELOG.md:67`). El problema
real era *cortar sin responder*, y eso ya lo resolvió el cierre con lo encontrado; el tope alto quedó como
efecto secundario. Con la cuenta de la clase §2.4:

| Pasos | p = 0,99 | p = 0,95 | p = 0,90 |
|---|---|---|---|
| 8 | 0,92 | 0,66 | 0,43 |
| 15 | 0,86 | 0,46 | 0,21 |
| 50 | 0,61 | 0,08 | 0,005 |

Un turno que llega a 50 vueltas casi seguro se desvió: seguir iterando gasta sin mejorar. Y por la clase §2.5
el costo crece cuadrático: con un prefijo de unos 4.000 tokens y 800 tokens nuevos por vuelta (números
**ilustrativos**), 4 vueltas envían unos 24.000 tokens y 50 vueltas, unos 1.220.000. Además, con el lock por
conversación (`conversation_service.py:300`), un turno de 300 s bloquea al cliente cinco minutos.

**Brecha 2: no hay tope de gasto.** ❌ La clase §2.6 pide «tope de pasos **y de gasto**». Hay un limitador de
mensajes por minuto (`guardrails.py:39`), pero ningún límite de tokens por turno ni por cliente y día. Los tokens
ya se miden (`observabilidad.py:54-79`, `sub_loop.py:241-243`): falta usarlos para cortar.

**Brecha 3: los documentos no coinciden.** 🟡 `docs/spec/README.md:584` (CA-20) todavía dice «tope de iteraciones
(8, RNF-16)», mientras D-07 y el código dicen 50.

**Caché de prefijo (clase §2.5).** ❌ El prefijo que se manda en cada llamada cambia más de lo necesario:

1. **La lista de tools cambia a mitad del turno.** `descubrir_tools` recalcula `tools_disponibles` en cada vuelta
   (`graph.py:264-265`, `tool_registry.py:582-587`): después de `leer_skill` aparecen tools nuevas, y en el
   cierre queda una sola. Los proveedores serializan las definiciones de tools **antes** de los mensajes, así que
   cambiarlas invalida el caché desde el principio. Es justo lo que la filmina dice que hay que evitar.
2. **El estado dinámico va dentro del system prompt.** `build_messages` arma un único `SystemMessage` con el
   prompt fijo, el índice, la skill activa, el contexto del cliente, el estado actual y la nota de búsqueda
   (`context.py:203-219`). Lo fijo y lo que cambia por turno quedan mezclados **antes** del historial: entre un
   turno y el siguiente cambia el estado y se pierde el caché de todo lo que sigue.
3. **El historial se reescribe.** `_previous_history` descarta las tools de turnos anteriores y corta a 20
   mensajes (`context.py:19`, `186-200`). Es una buena decisión de contexto, pero con la ventana corrediza el
   prefijo se corre en cada turno cuando la charla pasa de 20 mensajes.
4. **No se mide.** `observabilidad.py:60-61` y `148` guardan `input_tokens` y `output_tokens`, pero no los tokens
   leídos del caché (`usage_metadata["input_token_details"]["cache_read"]` en LangChain). Hoy no sabemos si el
   caché funciona.

Matiz: depende del proveedor (OpenAI cachea solo prefijos largos; Gemini tiene caché implícito; los modelos
`:free` de OpenRouter pueden no cachear nada). Ver P-3.

**Loop sin progreso (clase §2.7, opción c).** 🟡 El sub-loop ya detecta búsquedas repetidas y devuelve el
resultado anterior con `ya_buscado: true` (`sub_loop.py:302-349`). El **loop principal no**: si el modelo vuelve
a llamar `search_products` con la misma consulta, se ejecuta de nuevo y vuelve el mismo resultado. Ver M-3.

### 2.3 Diseño de tools (clase §3)

| Principio | Estado | Evidencia |
|---|---|---|
| La descripción es prompt (§3.1) | ✅ Descripciones largas: qué devuelve, cuándo usarla, qué hacer con cada campo | `tool_registry.py:446-566` (ej. `search_products`, `buscar_renglones`) |
| Schema con `required`, rangos y descripción por parámetro (§3.2) | ✅ Pydantic con `Field(description=…, ge=…, le=…)` | `agent_schema.py:51-147` |
| `enum` para los valores que existen (§3.2) | 🟡 Hay `enum`/`Literal` en `kind`, `motivo`, `reason`; **faltan** en nombres de skills y de BR | `agent_schema.py:21-27`, `33`, `321` vs `150-155`, `252` |
| Validar antes de ejecutar (§3.3) | ✅ `model_validate` antes de correr; permisos por skill | `tool_registry.py:716-730` |
| Error útil como observación (§3.4) | ✅ Código + qué hacer; el system prompt pide seguir `error.message` | `tool_registry.py:619-648`, `skill_tools.py:22-56`, `system_prompt.md:34` |
| Resultado vacío útil (§2.7) | ✅ `_hint` con el porqué y qué hacer | `tool_registry.py:125-148`, `176-187`, `211-219` |
| Datos verificables y que no se filtren (§3.4) | ✅ El LLM no ve el stock exacto salvo que no alcance | `tool_registry.py:95-111`, `222-225` |
| Pocas tools, orientadas a tareas (§3.5) | 🟡 15 tools, 8 siempre disponibles; hay solapamientos | `tool_registry.py:569-572` |
| Buscar en vez de listar (§3.5) | ✅ Búsqueda con `limit`; `explorar_catalogo` resume por rubro | `agent_schema.py:59`, `147` |
| Idempotencia en las que escriben (§3.7) | ✅ (ver detalle abajo) | `order_service.py:274-355`, `models.py:57-63`, `159` |

**Solapamientos (clase §3.5).** `get_product` y `check_stock` repiten lo que ya trae la búsqueda:
`search_products` con `quantity` devuelve `alcanza` y `disponible` (`tool_registry.py:103-111`), y la propia skill
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
- ✅ Deduplicación de mensajes entrantes: Telegram por `update_id` en tabla persistente
  (`telegram_service.py:167-178`), WhatsApp por id de mensaje (`whatsapp_webhook.py:45`).
- ✅ La escritura en Sheet 2 no se repite en un reintento (`sheets_service.py:396`).
- ✅ Dos tools terminales en la misma respuesta: la segunda recibe `turno_cerrado` (`graph.py:428-431`).
- ✅ El nodo `pausa` no tiene efectos antes de `interrupt()` (`graph.py:478`), que es lo que exige LangGraph al
  retomar.
- 🟡 Preventivo: `escalate_to_human` hoy solo cambia el estado. Cuando avise al equipo (handoff, fuera del MVP según
  `docs/architecture.md`), va a mandar mensajes y necesita clave de idempotencia (M-12).

### 2.4 Verificadores y «terminé no prueba que terminó» (clase §3.6, §4.3, §4.7)

Es lo más fuerte del proyecto. La condición de salida del loop la decide el modelo (llama a una tool terminal),
pero **el backend no le cree**:

| Lo que el modelo «dice» | Quién lo verifica | Dónde |
|---|---|---|
| «Te propongo estos productos» | Los SKU tienen que haber salido de una tool del turno; el stock tiene que alcanzar | `guardrails.py:651` (`validar_salida`), `tool_registry.py:246-319` |
| «Pedido confirmado, número X» | Regla `pedido_hecho`: no puede decirlo; el mensaje de la orden lo arma el sistema | `guardrails.py:434`, `validation_service.build_order_message` |
| Precios, stock, promesas, formato | Reglas deterministas con crítica accionable y 2 reintentos (evaluador-optimizador) | `guardrails.py:489`, `573`, `599`, `693-703`; `MAX_RECHAZOS=2` en `guardrails.py:28` |
| «Resolví estos renglones» (sub-loop) | `nota.validar`: cada renglón en un solo lugar, SKU vistos, dudas de lectura | `nota.py:21`, `sub_loop.py:402-442` |
| «Cubrí todo el pedido» | `cubrir_renglones`: ningún renglón queda sin cuenta | `tool_registry.py:254-256` |
| «El cliente aceptó» | Se revalida stock y precio con lectura fresca antes de registrar | `order_service.py:288-307` |

Es el patrón **evaluador-optimizador** de la clase §4.7 con evaluador **determinista**, que la clase recomienda
cuando el criterio se puede chequear en código: más barato y confiable que un LLM juez.

**Brechas:**

- ❌ **No hay una línea de base medida.** El arnés de evaluación existe y es bueno (`evaluar_agente.py`, con
  `recall`/`precision` por línea y umbrales de release en `evaluar_agente.py:41-43`), pero `MEJORAS.md` muestra que
  ninguna corrida terminó por cuotas de free tier. Sin medición, la condición «el éxito se puede medir» del árbol
  de la clase §4.13 está **diseñada pero no cumplida**, y no hay forma de decidir el tope, el orquestador ni
  ninguna de las mejoras de esta spec.
- 🟡 La evaluación no reporta lo que haría falta para decidir el diseño: p95 y máximo de iteraciones por turno,
  cuántos turnos cierran por tope, rechazos de guardrails por regla, tokens cacheados (`evaluar_agente.py:204-241`).
- 🟡 La evaluación corre fuera del CI (`evaluar_agente.py:3`). Es razonable porque usa el LLM real, pero no hay
  ningún chequeo automático que avise si un cambio de prompt empeora los casos. Hoy la persona es el lazo (clase
  §2.6).

### 2.5 Patrón adecuado: workflow vs agente (clase §4)

**Árbol de «cuándo no hace falta un agente» (clase §4.13), aplicado a los tipos de turno:**

| Tipo de turno | ¿Una llamada alcanza? | ¿Pasos escribibles? | Hoy | Comentario |
|---|---|---|---|---|
| «dale» con propuesta vigente | — | Sí | ✅ Sin LLM (`graph.py:503`) | Hoja «workflow» |
| Fuera de tema evidente, cancelación | — | Sí | ✅ Sin LLM (`conversation_service.py:295`, `305-313`) | Hoja «workflow» |
| Saludo | Sí | — | 🟡 Router elige `informar`, pero igual hay loop | Podría ser una sola llamada forzada |
| Lista donde la precarga resuelve **todos** los renglones como `unico` con stock | Casi: solo falta redactar | Sí | ❌ Va al loop agéntico igual | Ver M-5 |
| Lista con dudas, aproximados o faltantes | No | No: no se sabe cuántas búsquedas | ✅ Loop con tope | Acá sí se justifica el agente |
| Registrar la orden | — | — | ✅ Gate humano (el cliente acepta) | Hoja «persona que aprueba» |

**Ruteo (clase §4.4).** ✅ Por tipo de tarea: `skill_clara` + palabras clave. 🟡 **No hay ruteo por costo**: el
mismo modelo atiende un saludo y una lista de 40 renglones (`llm.py:14-68`). Ver M-11.

**ReAct, plan-and-execute, ADaPT (clase §4.9–§4.11).** El loop principal es ReAct. La precarga y
`buscar_renglones` le agregan un «plan» por lotes: todos los renglones se buscan juntos. El orquestador ya tiene
algo de **ADaPT**: solo delega lo que la precarga no resolvió y puede volver a delegar lo que quedó trabado
(`internal/agent/orquestador.md:5-7`). 🟡 Lo que falta es **medir** si el orquestador mejora al loop simple; hoy
está apagado sin evidencia en ningún sentido. La clase §4.11 muestra que el patrón puede mover el éxito de 43% a
72% con el mismo modelo, pero también que depende del entorno. Ver M-10.

**Reflexión (clase §4.10).** ➖ En tiempo de ejecución no hace falta: el validador ya devuelve la crítica y el
modelo corrige en el mismo turno. 🟡 Offline sí aplica: cada falla de la evaluación debería terminar en una
lección **curada por una persona** (una BR nueva o corregida y un caso de regresión). Hoy eso se hace a mano y sin
registro. Ver M-8.

**Graph engineering (clase §4.14).** ✅ Es el diseño del proyecto: nodos y aristas fijas del programador
(`graph.py:550-581`), aristas condicionales con funciones deterministas (`decidir`, `decidir_tras_pausa`,
`ruta_falla`) y ciclos con tope (`llm ⇄ tools`, rechazos del validador, sub-loops). La autonomía queda localizada
en el nodo `llm`.

### 2.6 System prompt vs skill vs tool vs MCP (clase §5.2, §5.3)

| Pieza | Estado | Dónde |
|---|---|---|
| System prompt solo con reglas de toda tarea | 🟡 Casi todo es de toda tarea; dos párrafos aplican solo a imagen o audio | `system_prompt.md:28-30` |
| Skill = nombre + descripción como clasificador | ✅ `funcion` + `cuando` en el índice, siempre en contexto | `context.py:42-50`, `skill_service.py:42-43` |
| Carga progresiva | ✅ Índice siempre; cuerpo con `leer_skill`; BR como archivos aparte con `leer_br` | `skill_tools.py:22-56` |
| Procedimiento con ramas explícitas | ✅ Pasos y casos | `skills/tomar_pedido/skill.md:9-26` |
| **Criterios de cada paso** (clase §5.2, parte 3) | 🟡 Están en el backend (guardrails, nota), no en la skill: el modelo se entera cuando lo rechazan | ver M-7 |
| Qué deja escrito | ✅ La tool terminal deja la propuesta o la orden en la base | `tool_registry.py:311-318` |
| «Skill que el agente no activa» → instrucción explícita | ✅ `encaminar` preactiva la skill cuando la ruta es clara | `graph.py:152-179`, `context.py:53-72` |
| Skills curadas, no auto-generadas (SkillsBench, clase §5.1) | ✅ Las escribe el equipo | `internal/skills/` |
| Cada skill define sus tools y sus BR | ✅ Y se valida al arrancar | `tool_registry.py:605-616` |

### 2.7 Memoria (clase §5.4–§5.6)

| Tipo | Qué hay en Norma | Quién la escribe | Estado |
|---|---|---|---|
| Corto plazo | Mensajes del turno; las tools de turnos anteriores se descartan a propósito | El loop | ✅ `context.py:186-200`, `system_prompt.md:32` |
| De trabajo / progreso de la tarea | Renglones numerados con su estado (`resuelto`, `pendiente`, `afuera`) en el checkpoint | El backend | ✅ `context.py:94-134`, `state.py` |
| Episódica | Contexto del cliente: últimos 3 pedidos y SKU comprados, armado **sin LLM** una vez por día | El backend, desde la base | ✅ `contexto_cliente.py:1-40` |
| Semántica | Catálogo (por búsqueda) y `datos_negocio.md` | Otros (solo lectura) | ✅ «El buscador ya es memoria» (clase §5.5) |
| Procedural | Skills y BR | El equipo | ✅ |

Lo más valioso: **el agente no escribe memoria propia**. Todo lo que recuerda sale de la base (pedidos reales) o
lo curó una persona. Evita el riesgo de la clase §5.5 (anotar algo falso y leerlo como verdad la sesión
siguiente) y el de la clase §7, nota 12 (adoptar salidas corruptas).

**Brechas:**

- 🟡 **Archivo de progreso del repo desactualizado** (clase §1.4, «lo que no se anota no pasó»). `MEJORAS.md`
  cumple ese rol («Para retomar: leé MEJORAS.md…»), pero dice «Todavía no hay ninguna release publicada» y tiene
  como próximo paso la release `0.1.0`, cuando el `git log` ya muestra la `0.2.0` (`5ca242f`). También el README
  dice que interpretar el pedido «llega con el agente (etapa 03)».
- ➖ Preferencias del cliente escritas por el agente (estilo `anotar`): hoy no hacen falta y tienen el riesgo de
  arriba. Si el negocio las pide, conviene derivarlas de los pedidos o que las escriba un operador (P-5).

### 2.8 MCP vs HTTP directo (clase §6)

➖ Norma no usa MCP, y la clase §6.2 dice que está bien: **un agente, tools propias** → función local. Las otras
integraciones también siguen la tabla de la clase:

| Integración | Elección actual | Según la clase §6.2 |
|---|---|---|
| Tools del agente | Funciones locales en `internal/tools/` | ✅ Un agente, pocas tools propias |
| Google Sheets | `gspread` directo (`google_sheets_client.py`) | ✅ Backend sin modelo |
| Reintento de sincronización | Función del backend (`order_service.py:483`) | ✅ Cron sin modelo |
| Dashboard | API HTTP propia (`api/dashboard.py`) | ✅ |

MCP tendría sentido recién si el **equipo** (no el cliente) quisiera consultar pedidos o catálogo desde otro host,
como Claude Desktop: un servidor MCP de solo lectura que reutilice los mismos servicios. Queda fuera de alcance
(§4).

### 2.9 Tests y notebooks

- ✅ 639 tests (`tests/`, aislados de red y de `.env` por `tests/conftest.py`), con modelos falsos para el grafo
  (`test_agent_graph.py`, `test_orquestador.py`), tests de guardrails, tools, skills e invariantes.
- ➖ No hay notebooks en el repo.

---

## 3. Mejoras propuestas, priorizadas

### 3.1 Resumen

| # | Mejora | Concepto de la clase | Impacto | Esfuerzo | Fase |
|---|---|---|---|---|---|
| M-1 | Instrumentar lo que falta medir: iteraciones p95, cierres por tope, rechazos por regla, tokens cacheados | §2.4, §2.5, §3.6 | Alto | Bajo | 1 |
| M-2 | Correr la línea de base completa | §4.13 («el éxito se puede medir»), §1.3 | Alto | Medio (cuota) | 1 |
| M-3 | Detector de llamadas repetidas en el loop principal | §2.7 (opción c), §2.4 | Medio-alto | Bajo | 1 |
| M-4 | Tope de iteraciones basado en datos + tope de gasto por turno | §2.4, §2.5, §2.6 | Alto | Bajo | 2 |
| M-5 | Camino determinista para pedidos que la precarga resolvió entero | §4.13, §4.12 | Alto (costo y latencia) | Medio | 2 |
| M-6 | Prefijo estable para el caché | §2.5 | Medio-alto (costo) | Medio | 2 |
| M-7 | Sección «Criterios» en cada skill | §5.2 (parte 3), §4.2 (gates) | Medio | Bajo | 2 |
| M-8 | Registro de lecciones curadas desde la evaluación | §4.10, §5.1 | Medio | Bajo | 1 |
| M-9 | Consolidar tools redundantes (tras medir uso) | §3.5 | Medio | Medio | 3 |
| M-10 | A/B: orquestador vs loop simple | §4.11, §4.12 | Alto (para decidir) | Bajo (con M-2) | 3 |
| M-11 | Ruteo por costo: modelo barato para turnos simples | §4.4 | Medio | Medio | 3 |
| M-12 | Clave de idempotencia para avisos al equipo | §3.7 | Preventivo | Bajo | cuando llegue el handoff |
| M-13 | Muestreo estratificado de conversaciones y métricas del agente en el dashboard | §1.3 (nivel 5) | Medio | Medio | 3 |
| M-14 | `enum` dinámico en `leer_skill`, `leer_br` y `delegar_skill` | §3.2, §3.4 | Bajo-medio | Bajo | 2 |
| M-15 | Instrucciones de imagen y audio solo cuando hacen falta | §5.3 | Bajo-medio | Bajo | 2 |
| M-16 | Poner al día `MEJORAS.md`, el README y CA-20 | §1.4 | Bajo | Muy bajo | 1 |

Criterio de orden: primero lo que habilita decidir con datos (M-1, M-2), después lo que baja riesgo y costo sin
tocar el comportamiento visible (M-3, M-4), y al final lo que cambia el diseño y conviene comparar contra la línea
de base (M-5, M-6, M-9, M-10, M-11).

---

### M-1. Instrumentar lo que falta medir

**Concepto.** Clase §2.6: el lazo es tan bueno como su sensor. Para decidir el tope (§2.4) y el caché (§2.5) hay
que ver la distribución de iteraciones y los tokens cacheados, no solo los promedios.

**Cambio.**

- `observabilidad.py` (`resumir`, `Paso.respuesta_llm`): sumar `tokens_cacheados` desde
  `message.usage_metadata.get("input_token_details", {}).get("cache_read", 0)`, en el loop principal y en el
  sub-loop (`sub_loop.py:241-243`).
- `ResumenTurno`: sumar `cerrado_por_tope: bool` (hoy queda solo como paso `cierre` en la traza, `graph.py:261`) y
  `rechazos_por_regla: dict[str, int]`.
- `evaluar_agente._metricas` (`evaluar_agente.py:204-241`): sumar `iteraciones_p95`, `iteraciones_max`,
  `cierres_por_tope_pct`, `tokens_cacheados_pct` (cacheados / entrada) y los rechazos por regla.

```python
# observabilidad.py (boceto)
uso = message.usage_metadata or {}
resumen.tokens_entrada += uso.get("input_tokens", 0)
resumen.tokens_cacheados += (uso.get("input_token_details") or {}).get("cache_read", 0)
```

**Criterio de aceptación.**

- `pytest tests/test_observabilidad.py -k cacheados`: un modelo falso que devuelve
  `usage_metadata={"input_tokens": 1000, "output_tokens": 10, "input_token_details": {"cache_read": 800}}` deja
  `tokens_cacheados == 800` en el resumen del turno.
- `pytest tests/test_evaluacion.py -k percentil_iteraciones`: con filas sintéticas de 2, 3, 3, 4 y 40 iteraciones,
  `_metricas` devuelve `iteraciones_max == 40` y el p95 que corresponde.
- Un turno que llega al tope tiene `cerrado_por_tope=True` (`tests/test_agent_graph.py`, extendiendo
  `test_tope_de_iteraciones_cierra_con_lo_encontrado`).

**Riesgos.** Cada proveedor reporta el caché distinto, y algunos no lo reportan: si el campo no viene, registrar
`None` y no `0`, para no concluir «no cachea» cuando en realidad «no informa».

---

### M-2. Correr la línea de base completa

**Concepto.** Clase §4.13: un agente se justifica solo si el error se contiene **y el éxito se mide**. Clase §1.3:
para subir de nivel hace falta el verificador. Sin una corrida completa no se puede decidir ninguna de las
mejoras de diseño (M-4, M-5, M-6, M-9, M-10, M-11).

**Cambio.** Sin código nuevo, salvo M-1. Correr con un modelo que use tools de forma confiable y con cuota:

```bash
python -m internal.evaluar_agente --set pedidos --repeticiones 3 --mlflow --conversaciones data/evaluacion/pedidos.md
python -m internal.evaluar_agente --set mensajes --repeticiones 3 --mlflow
```

Guardar el resumen de métricas (sin textos de conversaciones, según `AGENTS.md`) en `docs/evaluacion/linea-de-base.md`
con: modelo, `prompt_version` (`context.py:27-39`), fecha y las métricas de M-1.

**Criterio de aceptación.** Existe `docs/evaluacion/linea-de-base.md` con `sin_evaluar == 0` en los dos conjuntos,
al menos 3 repeticiones por caso, y los valores de `recall_lineas_pct`, `precision_lineas_pct`,
`registros_indebidos`, `iteraciones_p95`, `cierres_por_tope_pct` y `tokens_cacheados_pct`.

**Riesgos.** Costo y cuota (D-21). Un modelo distinto al de producción da una línea de base que no transfiere: hay
que fijar primero qué modelo se va a usar (P-2).

---

### M-3. Detector de llamadas repetidas en el loop principal

**Concepto.** Clase §2.7: «repetir la misma búsqueda» es la falla típica de un loop sin progreso; hoy solo la corta
el tope. Clase §2.4: cada vuelta inútil es un paso más que se compone.

**Cambio.** En `graph.ejecutar_tools` (`graph.py:398-475`), antes de `tool_registry.execute`: si una tool de
**solo lectura** (`search_products`, `buscar_renglones`, `get_product`, `check_stock`, `explorar_catalogo`,
`pedidos_anteriores`) llega con los mismos argumentos normalizados que una llamada anterior **del mismo turno**,
devolver el resultado anterior con `ya_buscado: true` y un `_hint`, sin ejecutar. Es el mismo mecanismo del
sub-loop (`sub_loop.py:296-349`): conviene extraer `_clave` a un helper común.

```python
# boceto
SOLO_LECTURA = {"search_products", "buscar_renglones", "get_product", "check_stock",
                "explorar_catalogo", "pedidos_anteriores"}

def clave_llamada(name: str, args: dict) -> str:
    return name + json.dumps(normalizar(args), sort_keys=True, ensure_ascii=False)

previas = resultados_del_turno(del_turno)          # {clave: resultado} desde los ToolMessage del turno
if call["name"] in SOLO_LECTURA and (previo := previas.get(clave_llamada(call["name"], call["args"]))):
    result = {**previo, "ya_buscado": True,
              "_hint": "Ya hiciste esta misma búsqueda en este turno y el resultado es el de arriba. "
                       "Probá con menos palabras o con otras, o respondé con lo que tenés."}
```

Las tools que escriben o terminan el turno quedan afuera: su repetición ya la manejan la idempotencia y
`turno_cerrado`.

**Criterio de aceptación.** `pytest tests/test_agent_graph.py -k busqueda_repetida`: un modelo falso que llama dos
veces `search_products({"query": "shampoo pantene 400"})` y después responde produce **una sola** llamada a
`catalog_tools.buscar_productos` (con un spy) y el segundo `ToolMessage` trae `ya_buscado: true`.

**Riesgos.** El catálogo puede cambiar dentro de un turno (stock). Es improbable en segundos, y la revalidación al
confirmar (`order_service.py:288-307`) cubre el caso. Normalizar de más (por ejemplo, ignorar `quantity`)
devolvería un resultado que no corresponde: la clave tiene que incluir todos los argumentos.

---

### M-4. Tope de iteraciones basado en datos y tope de gasto por turno

**Concepto.** Clase §2.4 (pⁿ), §2.5 (costo cuadrático) y §2.6 («tope de pasos y de gasto»).

**Cambio.**

1. Con la línea de base (M-2), fijar `AGENT_MAX_ITERATIONS` en un valor apenas por encima del **p95** de
   iteraciones de los casos que salen bien. Si el p95 es 6, un tope de 10–12 deja margen sin pagar 50. El cierre
   con lo encontrado (`graph.py:247-263`) ya garantiza que el cliente recibe respuesta.
2. Sumar `AGENT_MAX_INPUT_TOKENS_PER_TURN` (`config.py`) y controlarlo en `descubrir_tools` igual que el tope de
   iteraciones, con `motivo="gasto"`, sumando los tokens del loop principal y de los sub-loops.
3. Bajar el plazo de 300 s en proporción: con el lock por conversación, el plazo es tiempo en que el cliente no
   puede escribir.
4. Actualizar D-07, RNF-16 y CA-20 (`docs/spec/README.md:584`, `677`).

```python
# graph.descubrir_tools (boceto)
elif tokens_del_turno(state) > max_tokens_turno():
    motivo = "gasto"
```

**Criterio de aceptación.**

- `pytest tests/test_agent_graph.py -k tope_de_gasto`: un modelo falso que reporta 10.000 tokens de entrada por
  llamada, con `AGENT_MAX_INPUT_TOKENS_PER_TURN=25000`, entra al cierre en la tercera vuelta y responde con
  `respond_to_customer`.
- `test_presupuesto_por_defecto` (`tests/test_agent_graph.py:583`) actualizado al valor nuevo.
- En la evaluación repetida con el tope nuevo, `recall_lineas_pct` no baja más de 1 punto contra la línea de base y
  `cierres_por_tope_pct` no supera el 5%.

**Riesgos.** Un tope bajo vuelve a cortar pedidos largos legítimos: por eso se fija con el p95 medido y no a ojo.
Con el orquestador, los sub-loops tienen su propio `max_iteraciones` (8, `skills/tomar_pedido/skill.md:7`): el
tope del turno tiene que ser mayor que eso.

---

### M-5. Camino determinista para pedidos que la precarga resolvió entero

**Concepto.** Clase §4.13: primero se agota lo simple; clase §4.12: si el camino se puede escribir, workflow.
Cuando la precarga ya encontró cada renglón como `unico` y con stock, no queda nada que decidir paso a paso: solo
redactar la propuesta.

**Cambio.** Nuevo nodo o rama después de `precarga_busqueda` (`graph.py:182-202`). Si se cumplen **todas** estas
condiciones:

- todos los renglones nuevos son `unico` y `alcanza`;
- no hay propuesta vigente, o el mensaje es una lista nueva;
- el mensaje no trae texto fuera de los renglones (notas, preguntas, «sin bolsa»), ni renglones con duda de lectura;
- no se detectó intento de cambiar las reglas (`guardrails.detectar_intento`);

entonces armar la propuesta en el backend y hacer **una sola** llamada al LLM con
`tool_choice={"type": "function", "function": {"name": "respond_to_customer"}}` y la propuesta precargada en el
estado, solo para redactar `message`. La salida pasa por el mismo `validar_salida`. Variante más barata, a medir:
un mensaje de plantilla sin LLM.

**Criterio de aceptación.**

- `pytest tests/test_agent_graph.py -k camino_determinista`: un pedido de 3 renglones inequívocos con stock
  (`tests/datos_sinteticos.py`) produce una propuesta con esas 3 líneas, `iteraciones == 1` y ninguna llamada a
  `search_products` ni `buscar_renglones` desde el LLM.
- El mismo test con un renglón `aproximado` **no** toma el camino determinista.
- En la evaluación, la fracción de turnos por este camino se reporta, y `precision_lineas_pct` no baja contra la
  línea de base.

**Riesgos.** Perder matices que el LLM habría visto («lo mismo que la otra vez pero…»). Por eso las condiciones son
estrictas y ante la duda va al loop. Si el texto sale de una plantilla, la voz se puede volver repetitiva: hay un
chequeo de cierres repetidos en la evaluación (`evaluar_agente.py:102-105`).

---

### M-6. Prefijo estable para el caché

**Concepto.** Clase §2.5: con caché de prefijo exacto el costo pasa de cuadrático a lineal, pero solo si se agrega
al final y no se cambian las tools ni el principio de la lista.

**Cambio, en dos pasos que se miden por separado (con M-1):**

1. **Tools constantes dentro del turno.** Mandar siempre los schemas del conjunto completo que el turno puede
   llegar a usar (base + las de las skills del índice que se pueden leer con o sin propuesta), y dejar el permiso
   en el código, que ya rechaza lo no permitido con un error útil (`tool_registry.py:629-648`, `718-719`). En el
   cierre, en vez de quitar tools, forzar `tool_choice` a `respond_to_customer`.
2. **Separar lo fijo de lo dinámico.** `build_messages` (`context.py:203-219`) arma el `SystemMessage` solo con lo
   fijo (prompt + índice + orquestador si corresponde). El contexto del cliente, la skill activa, el estado y la
   nota de búsqueda van en un mensaje al **final** de la lista, marcado como del sistema (como los avisos de
   `guardrails.es_aviso`), para que `_previous_history` lo descarte en el turno siguiente.

```text
Hoy:     [tools que cambian] [system: fijo + estado del turno] [historial] [turno]
Después: [tools fijas] [system: fijo] [historial] [turno] [estado del turno]
         └───────────── se cachea entre vueltas y entre turnos ─────────────┘
```

**Criterio de aceptación.**

- `pytest tests/test_agent_graph.py -k tools_constantes`: en un turno con `leer_skill` y después
  `respond_to_customer`, la lista de schemas que recibe el modelo falso es **idéntica** en todas las llamadas.
- `pytest tests/test_agent.py -k prefijo_estable`: dos llamadas de `build_messages` con distinto estado dan un
  `SystemMessage` idéntico byte a byte.
- Con el proveedor elegido, `tokens_cacheados_pct` sube contra la línea de base, y `aciertos_pct` y
  `precision_lineas_pct` no bajan más de 1 punto.

**Riesgos.**

- Mostrar todas las tools desde el inicio contradice en parte la clase §3.5 (más tools a la vista, más chances de
  elegir mal) y el diseño actual de «las tools aparecen al leer la skill», que tuvo motivos medidos en `MEJORAS.md`
  (el modelo respondía sin leer la skill). Por eso va en dos pasos y con A/B.
- Mover el estado al final cambia dónde mira el modelo: algunos modelos atienden mejor al system prompt. Medir
  antes de adoptarlo.
- Si el proveedor no cachea (P-3), este cambio no ahorra nada y solo agrega riesgo: en ese caso, no hacerlo.

---

### M-7. Sección «Criterios» en cada skill

**Concepto.** Clase §5.2: sin criterios por paso, la skill es «una nota». Hoy los criterios existen, pero en el
backend: el modelo los descubre cuando lo rechaza el validador, y cada rechazo es una vuelta más (§2.4, §2.5).

**Cambio.** Agregar al final de cada `skill.md` una sección corta que diga **qué va a verificar el sistema**, con las
mismas palabras que el mensaje de rechazo. Por ejemplo, en `tomar_pedido`:

```markdown
## Criterios (los verifica el sistema antes de mandar tu respuesta)
- Cada renglón del pedido está en un solo lugar: `propuesta`, `pendientes` o `afuera`.
- Cada SKU salió de una búsqueda de este turno o de las opciones que ya le ofreciste.
- El mensaje no dice el stock salvo que no alcance (BR-01), no repite los precios y no promete fechas.
- Con `pendientes`, cada renglón que preguntás tiene su pregunta en `message`, con una viñeta.
```

Un test valida que cada criterio nombrado tenga una regla en el validador, para que no se desincronicen.

**Criterio de aceptación.**

- `pytest tests/test_skills.py -k criterios`: cada skill con tools terminales tiene la sección `## Criterios`.
- En la evaluación, el promedio de rechazos por turno (M-1) baja contra la línea de base.

**Riesgos.** Más texto en la skill = más contexto cuando se lee. Mantenerla en 4–6 líneas. Si el modelo «aprende»
el criterio pero no la tarea (*reward hacking* leve, clase §4.3), el validador sigue igual: el criterio informa, no
reemplaza al verificador.

---

### M-8. Registro de lecciones curadas desde la evaluación

**Concepto.** Clase §4.10 (Reflexión: la lección después de un fallo medido) y §5.1 (SkillsBench: lo curado suma,
lo auto-generado resta). La combinación es: el fallo lo mide el evaluador, la lección la escribe **una persona**.

**Cambio.** Proceso, no runtime. Un archivo `docs/evaluacion/lecciones.md` con una fila por falla de la evaluación:

| Fecha | Caso | Qué falló (métrica) | Causa | Lección | Dónde quedó |
|---|---|---|---|---|---|
| 2026-11-11 | `acepta-dale` | Inventó el número de orden | Sin tool terminal disponible | La skill se lee antes de buscar | `system_prompt.md` paso 1, regla `pedido_hecho` |

Cada lección termina en un cambio curado (BR, skill, regla del validador o tool) **y** en un caso de regresión en
`data_example/pedidos_evaluacion.json` o `mensajes.json`.

**Criterio de aceptación.** Chequeo de revisión del PR (en `.github/pull_request_template.md`): todo PR que cambie
`internal/skills/` o `system_prompt.md` por una falla de evaluación enlaza su fila en `lecciones.md` y el caso de
regresión. Verificable: `pytest tests/test_datos_ejemplo.py` sigue pasando con los casos nuevos.

**Riesgos.** Burocracia. Mantenerlo en una tabla y solo para fallas medidas, no para cada ajuste de estilo.

---

### M-9. Consolidar tools redundantes, después de medir

**Concepto.** Clase §3.5: pocas tools, orientadas a tareas; cada tool ocupa contexto y es una opción más para
elegir mal.

**Cambio.**

1. Con la línea de base, contar en las trazas cuántas veces se llama cada tool y cuántas de esas llamadas aportaron
   algo que la búsqueda no traía (`resumen.tools` ya lo junta, `observabilidad.py:58`).
2. Si `check_stock` y `get_product` casi no se usan o se usan de forma redundante, sacarlas de `BASE_TOOLS`
   (`tool_registry.py:569-572`) y dejarlas solo para los casos que las necesitan (por ejemplo, `check_stock` en
   `confirmar_pedido` si cambió la cantidad).
3. Evaluar unificar `search_products` y `buscar_renglones` en una sola tool que reciba de 1 a N renglones. Es el
   cambio más grande: hacerlo solo si la medición muestra confusión entre las dos.

**Criterio de aceptación.** `pytest tests/test_tools.py -k base_tools` con la lista nueva, y en la evaluación
`aciertos_pct` igual o mejor y `tokens_entrada_por_llamada` menor que la línea de base.

**Riesgos.** Sacar una tool que sí resolvía casos raros. Por eso va primero la medición y después el cambio.

---

### M-10. A/B: orquestador vs loop simple

**Concepto.** Clase §4.11: el patrón puede cambiar el éxito más que el modelo, pero depende del entorno; clase
§4.12: la menor autonomía que resuelva la tarea.

**Cambio.** Sin código: correr la evaluación de M-2 dos veces, con `AGENT_ORQUESTADOR=false` y `true`, mismo modelo
y mismas repeticiones, y comparar por métrica y por tipo de caso (pedidos largos contra cortos).

**Criterio de aceptación.** Una tabla en `docs/evaluacion/linea-de-base.md` con las dos configuraciones, y una
decisión anotada en la spec del orquestador: se prende si mejora `recall_lineas_pct` en pedidos largos sin empeorar
los cortos ni duplicar el costo; si no, queda apagado y se documenta por qué.

**Riesgos.** Pocos casos dan diferencias que son ruido: hace falta un intervalo de confianza (el de Wilson llega a
`evaluar_agente` con `feature/mejoras-del-agente`, que todavía no está en `develop`) y no decidir con diferencias
dentro del intervalo.

---

### M-11. Ruteo por costo: modelo barato para turnos simples

**Concepto.** Clase §4.4: «el caro, solo para los difíciles»; la cabeza de casos triviales la resuelve un modelo
chico.

**Cambio.** `llm.get_chat_model(nivel: Literal["simple", "completo"])` con `LLM_MODEL_SIMPLE` opcional en
`config.py`. `encaminar` ya sabe la skill del turno (`graph.py:160`): `informar` y `confirmar_pedido` con mensaje
corto van al modelo simple; `tomar_pedido`, al completo. Sin `LLM_MODEL_SIMPLE`, todo sigue como hoy.

**Criterio de aceptación.** `pytest tests/test_llm.py -k nivel`: con `LLM_MODEL_SIMPLE` configurado, un saludo usa
el modelo simple y una lista de 3 renglones el completo. En la evaluación, el conjunto `mensajes` no baja
`aciertos_pct` y el costo estimado baja.

**Riesgos.** El modelo simple puede usar mal las tools (ya pasó con modelos `:free`, `MEJORAS.md`). Un error del
router manda un caso difícil al camino barato: el validador de salida sigue siendo la red.

---

### M-12. Clave de idempotencia para avisos al equipo

**Concepto.** Clase §3.7: las tools que escriben o envían necesitan clave de idempotencia generada por el programa,
no por el modelo.

**Cambio.** Cuando `escalate_to_human` empiece a notificar (handoff, v2), registrar cada aviso en una tabla con
`UniqueConstraint` sobre una clave `sha256(conversation_id, tool_call_id)` (o `conversation_id` + motivo + día, si el
mismo turno se puede reintentar con otro `tool_call_id`), igual que `processed_messages`
(`telegram_service.py:167-178`).

**Criterio de aceptación.** `pytest tests/test_resolucion_derivaciones.py -k aviso_idempotente`: ejecutar dos
veces la derivación con la misma clave manda un solo aviso (spy sobre el canal).

**Riesgos.** Ninguno relevante; es preventivo.

---

### M-13. Muestreo estratificado y métricas del agente para el equipo

**Concepto.** Clase §1.3, nivel 5: cuando no se revisa cada caso, se revisan **muestras y agregados**.

**Cambio.** Un comando `python -m internal.muestrear_conversaciones --semana` que elija conversaciones
estratificadas por resultado (`pedido`, `derivada`, `fallida`, cierre por tope, con rechazos del validador) desde la
base local, para revisión del equipo, y un panel en el dashboard con los agregados de M-1 por día.

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
`tool_registry.schemas` (`tool_registry.py:595-602`), sin cambiar los modelos Pydantic (la validación de permisos
sigue en `skill_tools.py`).

```python
# tool_registry.schemas (boceto)
if name == "leer_skill":
    tool["function"]["parameters"]["properties"]["nombre"]["enum"] = sorted(get_skills())
```

**Criterio de aceptación.** `pytest tests/test_tools.py -k enum_skills`: el schema de `leer_skill` lista
exactamente las skills cargadas y el de `leer_br` todas las BR. Los errores `skill_inexistente` y `br_inexistente`
siguen funcionando si el proveedor ignora el `enum`.

**Riesgos.** El `enum` de BR incluye las de skills no leídas: el permiso lo sigue dando `leer_br`. Si M-6 fija las
tools, el `enum` también tiene que ser estable dentro del turno (lo es: sale del índice cargado al arrancar).

---

### M-15. Instrucciones de imagen y audio solo cuando hacen falta

**Concepto.** Clase §5.3: el system prompt es para reglas de **toda** tarea, porque se paga en cada llamada.

**Cambio.** Mover los dos párrafos de `system_prompt.md:28-30` a `internal/agent/entrada_imagen.md` y
`entrada_audio.md`, y sumarlos al bloque dinámico solo si el mensaje del turno trae «[imagen leída]» o «[audio
transcripto]» (las marcas ya existen en `message_service`).

**Criterio de aceptación.** `pytest tests/test_agent.py -k instrucciones_imagen`: un turno de texto no las incluye y
uno con «[imagen leída]» sí. `tests/test_lectura_al_agente.py` sigue pasando.

**Riesgos.** Si va dentro del `SystemMessage`, rompe el prefijo fijo de M-6: tiene que ir en el bloque dinámico del
final.

---

### M-16. Poner al día el archivo de progreso y los documentos

**Concepto.** Clase §1.4: «changelog y tareas: lo que no se anota no pasó». `MEJORAS.md` es el archivo de progreso
del repo, y está atrasado.

**Cambio.** Actualizar en `MEJORAS.md` el estado de releases (ya existe la 0.2.0) y los próximos pasos; corregir el
README («interpretar el pedido llega con el agente (etapa 03)») y CA-20 (`docs/spec/README.md:584`, tope 8 → valor
actual).

**Criterio de aceptación.** `git grep -n "Todavía no hay ninguna release publicada"` y `git grep -n "tope de
iteraciones (8"` no devuelven nada.

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
- **LLM como juez** de las respuestas: el criterio se puede chequear en código y ya está en `guardrails`; un juez
  LLM es más caro y menos confiable (clase §4.7).
- **Votación** (varias corridas por turno, clase §4.5): multiplica el costo y no protege contra errores
  sistemáticos.
- **Cambios de canal, Google Sheets, dashboard** que no tengan que ver con el diseño del agente.

## 5. Plan de implementación por fases

**Fase 1: medir (habilita todo lo demás).** M-1, M-16, M-8 (el archivo y el proceso), M-3, y la corrida de M-2.
Ninguna cambia lo que ve el cliente, salvo M-3, que solo evita repetir búsquedas. Salida: `linea-de-base.md`.

**Fase 2: bajar riesgo y costo, comparando contra la línea de base.** M-4 (tope y gasto, con los números de la
fase 1), M-14, M-15, M-7, después M-5 y por último M-6 (el de más riesgo de calidad, y solo si el proveedor cachea).
Cada una se mide con la misma corrida de evaluación antes de integrarse.

**Fase 3: decisiones de diseño.** M-10 (orquestador sí o no), M-9 (consolidar tools), M-11 (ruteo por costo) y
M-13 (muestreo y métricas para el equipo). M-12 entra cuando se implemente el aviso al equipo.

Regla para todas las fases: un cambio que baja `recall_lineas_pct` o `precision_lineas_pct` más de 1 punto, o que
sube `registros_indebidos` por encima de 0 (`evaluar_agente.py:41-43`), no se integra.

## 6. Preguntas abiertas

- **P-1.** Las mejoras que se aprueben, ¿se convierten en RF y CA dentro de las specs existentes (`agente.md`,
  `orquestador-y-sub-loops.md`, `README.md`), o en una spec propia con la plantilla `_plantilla.md`?
- **P-2.** ¿Qué modelo y proveedor se va a usar en producción? La línea de base, el caché y el ruteo por costo
  dependen de eso. `.env.example` sugiere `gpt-6-luna` pago como alternativa al free tier.
- **P-3.** ¿El proveedor elegido hace caché de prefijo y lo informa en `usage_metadata`? Si no, M-6 no tiene sentido.
- **P-4.** ¿Por qué el plazo es de 300 s? `CHANGELOG.md:67` explica el salto de 8 a 50 iteraciones, pero no si 300 s
  es aceptable para el negocio, sabiendo que la conversación queda bloqueada mientras tanto (RNF-06 pide p95 de
  10 s).
- **P-5.** ¿El negocio quiere que el bot recuerde preferencias del cliente («retira los martes», «factura A»)? Si sí,
  ¿las escribe un operador o se derivan de los pedidos?
- **P-6.** ¿Qué fracción de los pedidos reales son listas que la precarga resolvería enteras? Define cuánto vale
  M-5. Se puede estimar con `pedidos_evaluacion.json` sin tocar datos reales.
- **P-7.** El orquestador está apagado por defecto. ¿Hay alguna medición, aunque sea parcial, que motivó
  construirlo? Si existe, conviene sumarla a la línea de base de M-2.
