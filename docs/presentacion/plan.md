# Plan de la presentación final del TP

> **Estado:** borrador para discutir en el grupo. Hecho sobre `develop` en `d4599a2` (0.2.0 + PR #115 + PR #117) y
> contra la consigna del TP de IA Agéntica 2026.
>
> **Qué es:** qué contamos, en qué orden, con qué evidencia del repo y qué falta producir antes de exponer. No es
> la presentación: es el guion para armarla.
>
> **Pendientes:** lo que falta decidir está marcado como **⏳ A-xx** y reunido en [§8](#8-pendientes-de-definir).
>
> **Diapositivas:** borrador en [`presentacion.html`](presentacion.html), con el formato de la propuesta. Lo que
> falta aparece en cajas punteadas violetas con el mismo ID, y el punto de esa diapositiva en la barra de abajo
> también es violeta.

---

## 1. Qué pide la consigna para la entrega final

**Exposición:** 15 minutos más preguntas, **con todos los integrantes** (6 personas, unos 2,5 min cada uno). La
defensa también es individual: cualquiera tiene que poder explicar el sistema completo y las alternativas que no
tomamos.

**Contenido obligatorio de la presentación:**

1. Problema y usuarios.
2. Casos de uso.
3. Hipótesis y resultados.
4. Arquitectura con diagrama.
5. Qué se hizo en cada área de profundización (mínimo 2, pesa la profundidad).
6. Evaluación sobre test.
7. Costos.
8. Limitaciones.
9. Qué haríamos distinto y próximos pasos.

Y una parte **central**: el relato del desarrollo (bloqueos, qué no funcionó y por qué, qué decisiones cambiaron el
rumbo, cómo usamos asistentes de IA y qué aprendimos).

**Criterios y peso:**

| Criterio | Peso | Qué nos van a mirar |
|---|---|---|
| Problema, utilidad e hipótesis | 15 % | Usuario real, tarea acotada, hipótesis medible |
| Diseño del sistema y uso de LLMs | 25 % | Contexto, contratos, manejo de errores, decisiones justificadas, áreas tratadas en serio |
| Evaluación, evidencia y costos | 25 % | Baseline simple medido, evaluación reproducible, comparación honesta sobre test, análisis de fallas, costo y latencia |
| Ingeniería y trabajo en equipo | 10 % | Sistema y evaluación corren siguiendo el README; git prolijo y contribuciones distribuidas |
| Exposición y defensa | 25 % | Claridad, trade-offs, limitaciones y relato del desarrollo |

**Regla de oro:** todo lo que digamos tiene que ser verificable en el repositorio. Cada número de la presentación
sale de un comando que está en el README.

**Fechas:** la consigna dice entrega final el jueves 19/11 y exposición el 10/12 o el 11/12. **Hay que confirmar con
la cátedra** si la entrega del 19/11 se movió. **⏳ A-01, A-02**

---

## 2. Qué propusimos y qué hay hoy

La propuesta (presentación de 8 diapositivas, Grupo 10) y el sistema actual no coinciden del todo. No es un
problema: la consigna pide contar las decisiones que cambiaron el rumbo. Pero hay que decirlo explícitamente, porque
la cátedra va a comparar. La tabla es una propuesta para validar en el grupo. **⏳ A-16**

| Tema | En la propuesta | En `develop` hoy | Cómo lo contamos |
|---|---|---|---|
| Canal | WhatsApp, notas, fotos, correos | Telegram con texto, foto y audio. WhatsApp solo valida la firma | Cambio de rumbo: Telegram para la beta del equipo (sin aprobación de Meta) |
| Retrieval | BM25 + embeddings, Top-K SKUs | Búsqueda tolerante a errores de tipeo, sin embeddings (D-29 abierta) | Decisión con evidencia: recall@3 0,94 sin embeddings; los fallos que quedan son de necesidad («teñir»), no de tipeo |
| Analítica (BI) | Preguntas en lenguaje natural sobre ventas con DuckDB | No está. Hay un dashboard operativo | Recorte de alcance: se priorizó la toma de pedidos |
| Repregunta / HITL | Umbrales de confianza, repreguntar o derivar | Aclaraciones con opciones, derivación a una persona con 24 h de silencio, aceptación del cliente comprobada en el backend | Cumplido y ampliado |
| H1 (agente vs. prompting simple) | Exactitud de orden e intervención humana | Hay con qué medir (3 sets, grupo reservado) pero **no hay baseline implementado ni corrida completa** | Brecha a cerrar (§5) |
| H2 (historial del cliente) | Menos desambiguación manual | Existe el contexto del cliente (`contexto_cliente.py`, `pedidos_anteriores`) pero no se midió con y sin | Brecha a cerrar o reformular (§5) |
| Economía | Tokens por pedido, API vs. modelo local | Trazas en MLflow con tokens y latencia; `--precio` en la evaluación. Modelo local solo para visión (Qwen3-VL, PR #110) | Parcial: falta el costo por pedido con el modelo elegido |
| Datos | Catálogo, historial y pedidos reales sanitizados; partición fija | Catálogo real en producción; catálogo de evaluación inventado (~400 productos); `data_example/` versionado | Falta documentar volumen, actualización y licencia, y cómo se generaron los sintéticos |

---

## 3. Áreas de profundización

La consigna pide al menos 2, tratadas en serio. Tenemos material para más; conviene **elegir 3 como centrales** y
mencionar las demás en una línea. **⏳ A-13**

### Centrales (propuesta)

| Área | Qué tenemos | Evidencia en el repo |
|---|---|---|
| **Recuperación de información, evaluada aparte de la generación** | Búsqueda tolerante (tipeo, sonido, abreviaturas, medidas, tonos), renglón por renglón; benchmark propio de 124 consultas **sin LLM** con piso en el CI | `docs/spec/busqueda-tolerante.md`, `python -m internal.evaluar_busqueda`, `data_example/consultas_busqueda.json`. Antes/después de RF-118: recall@3 0,87 → 0,94, MRR 0,867 → 0,937 |
| **Tools con contratos y guardrails** | Tools con permisos por skill, errores que dicen qué hacer, verificadores de evidencia (SKU no buscado, opción sin elegir, cantidad distinta, precio y medida vistos), guardrails de salida con reintento, aceptación comprobada antes de registrar, idempotencia | `docs/spec/guardrails.md`, `internal/agent/guardrails.py`, `internal/agent/evidencia.py`, `internal/tools/`, RF-111 a RF-114, RF-120, RF-121 |
| **Evaluación** | Benchmark propio del dominio: `mensajes` (32), `pedidos` (11 listas largas), `adversariales` (24, 12 desarrollo / 12 reservados); tope de llamadas, costo, intervalo de Wilson, comparación de corridas | `internal/evaluar_agente.py`, `internal/comparar_corridas.py`, `data_example/*.json` |

### Complementarias (una diapositiva o menos)

- **Orquestación y skills:** grafo LangGraph, 4 skills con reglas de negocio (BR) que se leen con tools, orquestador
  con sub-loops (apagado por defecto). Podría ser central en lugar de Evaluación si el grupo lo prefiere.
- **Observabilidad:** MLflow con una traza por turno (tokens, latencia, tools, skills), sin texto de clientes;
  `sensor_calidad` sobre uso real.
- **Human in the loop:** el cliente aprueba la propuesta; derivación a una persona. Falta medir su costo y su efecto,
  que es lo que pide la consigna para contarla como área. **⏳ A-14**
- **Memoria y estado:** checkpoints del grafo, opciones ofrecidas visibles en el turno siguiente, vencimiento de
  conversaciones.
- **Inferencia local:** Qwen3-VL para leer fotos de pedidos comparado contra la API (PR #110).

---

## 4. Guion de la exposición (15 minutos)

Formato de las diapositivas a definir: **⏳ A-23**. Quién presenta cada bloque: **⏳ A-03**.

| # | Bloque | Min | Contenido | Material que ya existe | Estado |
|---|---|---|---|---|---|
| 1 | Problema y usuario | 1,5 | Distribuidora mayorista de perfumería; el operador que tipea pedidos informales. Ejemplos reales de pedidos (ficticios en la diapo) | Diapos 1–2 de la propuesta | ✅ |
| 2 | Por qué un LLM + casos de uso + demo | 2 | Tabla LLM vs. software clásico; casos: pedido por texto, lista larga, foto, audio, aclaración, derivación. Demo **grabada** (no en vivo: cuotas y latencia) | Diapo 3 de la propuesta, bot de Telegram | 🟡 falta grabar la demo · ⏳ A-15 |
| 3 | Hipótesis | 1 | Hipótesis reformulada con su baseline y su métrica (§5.1) | — | ❌ · ⏳ A-05 a A-07 |
| 4 | Arquitectura | 2 | Diagrama: canal → filtro previo → grafo (skills, tools, sub-loop) → guardrails → propuesta → aceptación → registro → planillas y dashboard. Principio: «el LLM interpreta, el backend valida» | `docs/architecture.md`, `docs/Flujo completo de Norma Agentic AI.png` | 🟡 rehacer el diagrama con el estado actual · ⏳ A-17 |
| 5 | Áreas de profundización | 3,5 | Las 3 centrales de §3, una diapo cada una, con un ejemplo de falla que resuelven | Specs y tests | ✅ material, falta diapos · ⏳ A-13 |
| 6 | Evaluación sobre test y costos | 2,5 | Resultados en el grupo reservado contra el baseline, con intervalo; análisis de fallas; costo por pedido y latencia p50/p95; elección de modelo | Herramientas listas | ❌ falta correr · ⏳ A-08 a A-12 |
| 7 | Relato del desarrollo | 2 | Bloqueos, qué no funcionó, cambios de rumbo, uso de asistentes de IA, aprendizajes (§6) | `MEJORAS.md`, `CHANGELOG.md`, specs | 🟡 falta sintetizar · ⏳ A-18 |
| 8 | Limitaciones y próximos pasos | 0,5 | Ver §7 | `MEJORAS.md` | ✅ |

---

## 5. Brechas a cerrar antes de exponer

Ordenadas por peso en la nota. Las tres primeras son las que más arriesgan el 25 % de evaluación.

### 5.1 Hipótesis con baseline (15 % + 25 %)

Hoy no hay baseline para el agente. Propuesta de hipótesis medibles, con lo que ya tenemos:

- **H1. El agente arma la orden correcta más seguido que un baseline simple.**
  - *Métrica:* `aciertos_pct` y recall de renglones en el grupo **reservado** (test), con intervalo de Wilson.
  - *Baseline (elegir uno, **⏳ A-05**):* (a) **script determinístico**: el buscador solo, top-1 por renglón, sin LLM; es barato
    porque el buscador ya existe y ya se evalúa; (b) **prompt zero-shot**: el mismo modelo con el catálogo filtrado
    en el prompt, sin tools, skills ni guardrails.
- **H2. Los guardrails y verificadores en código bajan los errores graves** (**⏳ A-06**) (SKU inventado, pedido registrado sin
  aceptación, precio inventado) frente al mismo agente sin ellos, sobre el set `adversariales`.
- **H3 (retrieval, ya medido).** La búsqueda tolerante mejora recall@3 y MRR frente a la búsqueda exacta anterior:
  0,87 → 0,94 y 0,867 → 0,937 en 124 consultas. **Ojo:** las consultas son inventadas y no hay partición fija
  desarrollo/test en ese set; hay que separarla o decirlo como limitación. **⏳ A-10**

La H2 de la propuesta (historial del cliente) se puede medir con y sin `contexto_cliente`, o declararla como no
medida y explicar por qué. **⏳ A-07**

### 5.2 Corrida completa sobre test (25 %)

Es la M-2 de `docs/spec/mejoras-clase7.md`. Necesita:

1. **Fijar el modelo** (P-2 de esa spec). Sin eso, la línea de base no transfiere. **⏳ A-08**
2. Correr `mensajes`, `pedidos` y `adversariales` (**⏳ A-11**) con repeticiones (**⏳ A-12**), `--max-llamadas` y `--precio`, guardar con
   `--salida` y comparar con `comparar_corridas`.
3. Correr el baseline de §5.1 sobre los mismos casos.
4. **Análisis de fallas:** clasificar los casos que fallan (uso de tools, interpretación, búsqueda, guardrail).

Riesgo conocido: las corridas anteriores se cortaron por cuota (429 y 503 en free tier). Hace falta un modelo pago
barato o cuota suficiente, y presupuestarlo. **⏳ A-08**

### 5.3 Costos y elección de modelo (25 %)

- Costo por pedido (tokens y USD) y latencia p50/p95 con el modelo elegido, salidos de la corrida de §5.2.
- Justificar el modelo frente a alternativas: al menos un modelo de API más barato o uno abierto. Para la visión ya
  está la comparación con Qwen3-VL local. **⏳ A-09**
- Hoy solo hay números de trazas locales: 22.500 tokens por turno, p95 de 69 s, 21 % de turnos con error, con
  modelos mezclados. Sirven como orden de magnitud, no para la presentación.

### 5.4 Datos (requisito obligatorio)

Documentar en un lugar (puede ser `docs/data/fuentes.md`): volumen, actualización y licencia de cada fuente; que los
datos privados no se publican y que `data_example/` es la muestra sanitizada; **cómo se generaron los datos
sintéticos** (catálogo de evaluación, consultas, casos) y cómo se evalúa ese generador, porque la consigna lo pide.
**⏳ A-20, A-21**

### 5.5 Revisión de antecedentes (requisito obligatorio)

No encontré nada en el repo. Hace falta (**⏳ A-19**):

- **Mercado:** qué productos resuelven hoy la toma de pedidos por chat para distribuidoras (bots de WhatsApp de
  pedidos, módulos de ERP, plataformas B2B) y en qué nos diferenciamos.
- **Literatura:** trabajos sobre extracción de pedidos o entidades desde texto informal, entity linking o product
  matching contra catálogo, y agentes con tools. Con métodos, métricas y resultados de referencia.

### 5.6 Ingeniería y trabajo en equipo (10 %)

- **README replicable:** la consigna pide algo «símil Makefile». No hay `Makefile`: conviene sumar uno con
  `make test`, `make eval-busqueda`, `make eval-agente`, `make bot`. **⏳ A-22**
- **Contribuciones distribuidas:** en `develop` hay commits de 3 de los 6 integrantes. Antes de la entrega, cada
  integrante debería tener trabajo propio visible (por ejemplo, partes de esta presentación, la revisión de
  antecedentes, el baseline o el análisis de fallas, cada uno en su PR). **⏳ A-03, A-04**

---

## 6. Material para el relato del desarrollo

Salido de `MEJORAS.md`, `CHANGELOG.md` y las specs. Hay que validarlo y completarlo entre todos. **⏳ A-18**

**Bloqueos y lo que no funcionó**

- Cuotas de free tier: corridas enteras de evaluación cortadas por 429, 503 y 504; la medición se postergó.
- Modelos que no usan las tools: contestaban con texto suelto; uno inventó un número de orden y no registró nada.
- Cambios de prompt que rompieron el uso de tools (sacar «nunca respondas con texto suelto»).
- Bugs que solo aparecieron con uso real: renglones corridos por una línea de saludo, propuestas que se perdían, un
  «dale» que confirmaba cuando respondía otra pregunta, una conversación nueva que heredaba el estado de otra.

**Decisiones que cambiaron el rumbo**

- Telegram en lugar de WhatsApp para la beta.
- Sin BI con DuckDB: foco en la toma de pedidos.
- Sin embeddings: la búsqueda tolerante alcanzó y se midió sin LLM.
- Mover controles del prompt al código (guardrails, verificadores, aceptación en el backend).
- Skills con reglas de negocio leídas con tools en lugar de un system prompt gigante.
- Orquestador con sub-loops, implementado pero apagado hasta medirlo.

**Cómo usamos asistentes de IA**

- Desarrollo guiado por specs (SDD): spec con RF/CA → tests → código, con asistentes en cada paso.
- `AGENTS.md` con reglas para los agentes (secretos, datos de clientes).
- Revisiones de PR y diagnósticos (por ejemplo, la revisión del diseño con los conceptos de la clase 7).
- Completar con lo que usó cada integrante (Claude Code, Codex, Gemini, etc.). **⏳ A-18**

---

## 7. Limitaciones y próximos pasos

- Sin medición completa con un modelo fijo (se cierra con §5.2).
- WhatsApp y chat web sin implementar.
- Stock desde una planilla, sin reserva; SQLite para una sola instancia.
- Consultas de necesidad («algo para teñir») que la búsqueda no resuelve: candidato a embeddings.
- El control de la aceptación no cubre todos los casos (D-47, D-49).

---

## 8. Pendientes de definir

Todo lo que el grupo tiene que decidir antes de armar las diapositivas o de correr la evaluación. En el resto del
documento cada uno aparece marcado como **⏳ A-xx**. Cuando se decida, se completa la columna *Decisión* y se saca la
marca del texto.

**Orden sugerido:** primero A-01 y A-02 (sin ellas no se sabe cuánto tiempo hay), después A-05 a A-08 (bloquean la
corrida, que es lo que más tarda) y el resto en paralelo.

### Calendario y organización

| ID | Qué hay que definir | Opciones o propuesta | Bloquea | Decisión |
|---|---|---|---|---|
| A-01 | Fecha real de entrega del repo e informe | La consigna dice 19/11, ya pasó: confirmar con la cátedra por Discord | Todo el calendario | |
| A-02 | Día de la exposición | 10/12 u 11/12, a confirmar por la cátedra | Calendario | |
| A-03 | Reparto: qué bloque de §4 presenta cada integrante | Uno o dos bloques por persona, 6 integrantes | Ensayos, §5.6 | |
| A-04 | Reparto: qué brecha de §5 cierra cada integrante, cada una en su PR | Ver §5.1 a §5.6 | §5.6 | |

### Hipótesis y evaluación

| ID | Qué hay que definir | Opciones o propuesta | Bloquea | Decisión |
|---|---|---|---|---|
| A-05 | Baseline de H1 | (a) script determinístico: buscador top-1 por renglón, sin LLM; (b) prompt zero-shot sin tools ni guardrails; (c) los dos | Corrida (§5.2), bloque 3 y 6 | |
| A-06 | Adoptar H2 (guardrails vs. sin guardrails en `adversariales`) | Sí, como hipótesis; o solo mostrarlo como área sin hipótesis | Corrida, bloque 3 | |
| A-07 | Qué hacer con la H2 de la propuesta (historial del cliente) | Medir con y sin `contexto_cliente`, o declararla no medida y explicar por qué | Corrida, bloque 2 | |
| A-08 | Modelo para la corrida final y presupuesto en USD | Un modelo de API pago y barato que use bien las tools; fijar tope con `--max-llamadas` | Corrida, costos (§5.3) | |
| A-09 | Contra qué modelos alternativos se compara costo y calidad | Otro modelo de API más barato, uno abierto local, o solo el de visión ya comparado (Qwen3-VL) | §5.3 | |
| A-10 | Partición desarrollo/test del set de búsqueda (124 consultas) | Separar un grupo reservado como en `adversariales`, o declararlo como limitación | H3, bloque 6 | |
| A-11 | Qué sets entran en la evaluación sobre test | Solo el grupo `reservado` de `adversariales`, o sumar grupos reservados a `mensajes` y `pedidos` (hoy no tienen partición) | Corrida, bloque 6 | |
| A-12 | Cuántas repeticiones por caso | La consigna pide comparación honesta: al menos 3 para tener intervalo | Presupuesto (A-08) | |

### Contenido de la presentación

| ID | Qué hay que definir | Opciones o propuesta | Bloquea | Decisión |
|---|---|---|---|---|
| A-13 | Las 3 áreas de profundización centrales | Propuesta: retrieval, tools y guardrails, evaluación. Alternativa: skills y orquestación en lugar de alguna | Bloque 5 | |
| A-14 | Si HITL se cuenta como área | Medir su costo y su efecto (lo exige la consigna para contarla) o dejarla como complementaria | Bloque 5 | |
| A-15 | Qué casos muestra la demo y cómo | Propuesta: grabada (pedido por texto, lista larga, foto, audio, aclaración, derivación); en vivo solo como extra | Bloque 2 | |
| A-16 | Cómo contar los cambios respecto de la propuesta | Validar la tabla de §2: en especial si el BI con DuckDB es recorte definitivo o próximo paso | Bloques 2, 7 y 8 | |
| A-17 | Diagrama de arquitectura | Rehacer el de `docs/Flujo completo de Norma Agentic AI.png` o partir del mermaid de `docs/architecture.md` | Bloque 4 | |
| A-18 | Relato del desarrollo y uso de asistentes de IA | Cada integrante suma sus bloqueos, aprendizajes y herramientas a §6 | Bloque 7 | |

### Requisitos que faltan en el repo

| ID | Qué hay que definir | Opciones o propuesta | Bloquea | Decisión |
|---|---|---|---|---|
| A-19 | Alcance de la revisión de antecedentes | Cuántos productos y papers, y dónde se escribe (por ejemplo `docs/antecedentes.md`) | §5.5, informe | |
| A-20 | Dónde se documentan los datos | Ampliar `docs/data/fuentes.md` o un documento nuevo | §5.4 | |
| A-21 | Cómo se describe y evalúa el generador de datos sintéticos | Documentar cómo se armaron el catálogo de evaluación, las consultas y los casos, y qué control de calidad tuvieron | §5.4 | |
| A-22 | Sumar un `Makefile` (o scripts) para correr sistema y evaluación | Sí, con `make test`, `make eval-busqueda`, `make eval-agente`, `make bot`; o solo comandos en el README | §5.6 | |
| A-23 | Formato de las diapositivas | Seguir el HTML de la propuesta (mismo estilo, versionado en `docs/presentacion/`) u otra herramienta | Armado de diapositivas | |
