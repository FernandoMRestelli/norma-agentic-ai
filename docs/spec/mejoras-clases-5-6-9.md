# Spec: mejoras de búsqueda, registro y verificación (clases 5, 6 y 9)

> Documento de diseño. Lee el proyecto con los conceptos de tres clases de Inteligencia Artificial Agéntica:
> la 5 (*Information retrieval*), la 6 (*Introducción a agentic coding*) y la 9 (*AI benchmarks* y *LangGraph y
> observabilidad*). Las referencias del tipo «Clase 5, §2.1» apuntan a las secciones de los resúmenes de esas clases
> (`IAA - Teoria/<n>. … - Resumen.md`). Cada mejora cita el concepto que la motiva, la evidencia en el código y su
> criterio de aceptación. Si se aprueba, cada una se pasa a la [plantilla](_plantilla.md) con sus IDs definitivos.

| | |
|---|---|
| Estado | Propuesto: no hay nada implementado |
| Base | `develop` (`2b64fce`), con `feature/mejoras-del-agente` integrada. Las referencias `archivo:línea` son de ese commit |
| IDs | Provisorios: `MC-1` a `MC-6`. Los RF, CA y D se reservan al aprobar, después de los que usen las specs de las clases 7 y 8 |
| Complementa a | [`mejoras-clase7.md`](https://github.com/FernandoMRestelli/norma-agentic-ai/pull/117) y [`mejoras-clase8.md`](https://github.com/FernandoMRestelli/norma-agentic-ai/pull/118), todavía en PR |

**Aclaración de alcance.** Norma no es un agente de normativa. Es el agente que toma pedidos mayoristas de una
distribuidora de peluquería: interpreta el pedido contra el catálogo, propone y registra solo cuando el cliente
acepta. Los conceptos de las clases se aplican igual: donde la clase 5 habla de documentos, acá son productos del
catálogo; donde habla de consultas, son los renglones que escribe el cliente.

---

## 1. Objetivo

Juntar las mejoras de estas tres clases que **todavía no están hechas ni propuestas** en otra spec. Muchas de las
que surgían de la lectura ya entraron a `develop` o están en las specs de las clases 7 y 8 (ver §2). Lo que queda
es de ajuste fino: cómo pesa cada palabra en la búsqueda, qué lee el modelo para elegir una skill o una tool, cómo
se registran las decisiones y cómo se controla la calidad de los tests y del tono.

## 2. Alcance

| Dentro (esta spec) | Fuera: ya hecho o propuesto en otro lado |
|---|---|
| MC-1. Peso por rareza en el puntaje de la búsqueda | Medir el buscador solo, en el CI: hecho (RF-119, `evaluar_busqueda`) |
| MC-2. Descripciones de skills que no se pisan | Tope de tokens y freno a búsquedas repetidas: hecho (RF-122) |
| MC-3. Descripciones de tools sin referencias internas | Tope de iteraciones con datos, caché de prefijo, consolidar tools y `enum` en `leer_skill`: clase 7 (M-4, M-6, M-9, M-14) |
| MC-4. Decisiones estructurales como ADRs | Decidir si se queda el orquestador: hecho (RF-124) y clase 7 (M-10) |
| MC-5. Probar los tests con mutantes | Evaluación sin contaminación y grupo reservado: hecho y clase 8 (M8-1) |
| MC-6. Juez del tono validado contra personas | Agrandar el diccionario con lo que resuelve el LLM: clase 8 (M8-7) |
| | Embeddings, reranker y DSPy/GEPA: la clase 8 los descarta por ahora (§4 de esa spec); esta spec no los reabre |
| | Unificar los nombres de las tools en castellano: la clase 7 lo deja fuera de alcance; MC-3 toca solo las descripciones |
| | Permisos de Claude Code, `AGENTS.md` y `MEJORAS.md` al día: hecho en `feature/mejoras-del-agente` |

## 3. Resumen priorizado

| ID | Mejora | Clase | Impacto | Esfuerzo | Orden |
|---|---|---|---|---|---|
| MC-2 | Descripciones de skills que no se pisan | 7, §5.2 y §5.3 | Medio | Bajo | 1 |
| MC-3 | Descripciones de tools sin referencias internas | 7, §3.1 | Bajo-medio | Muy bajo | 1 |
| MC-4 | Decisiones estructurales como ADRs | 6, §8.1 a §8.4 | Medio | Bajo | 2 |
| MC-1 | Peso por rareza, suave, en el puntaje de la búsqueda | 5, §2.1 | Bajo (medido) | Bajo | 3 |
| MC-5 | Probar los tests de guardrails y búsqueda con mutantes | 6, §3.8 | Medio | Bajo-medio | 3 |
| MC-6 | Juez del tono, validado contra personas | 5, §9.7; 9 (benchmarks), §4.2 | Bajo-medio | Medio | 4 |

MC-2 y MC-3 cambian lo que lee el modelo (y la `prompt_version`), así que van juntas y con una corrida de
`evaluar_agente` antes y después.

---

## 4. Mejoras

### MC-1 · Peso por rareza, suave, en el puntaje de la búsqueda

**Concepto (Clase 5, §2.1).** BM25 no cuenta igual todas las palabras de la consulta: las que aparecen en pocos
documentos pesan más, porque son las que distinguen. Si buscás a alguien en un colegio, «se llama Juan» ayuda poco;
el apellido ayuda mucho.

**Hoy.** El puntaje de un producto es el promedio de cómo coincide cada palabra y cada número de la búsqueda, todos
con el mismo peso (`internal/services/busqueda_service.py:391`). En «sh keratina nordika 900», «shampoo», que está
en cientos de productos, vale lo mismo que «nordika», que define la marca.

**Lo que mostró una prueba.** Antes de proponerlo se probó, sin tocar el repo, con las 124 consultas de
`evaluar_busqueda` (todas, desarrollo y reservado) y tres formas de ponderar:

| Variante | Acierto en el 1.º | Únicos correctos | Falsos únicos | Negados |
|---|---|---|---|---|
| Hoy, todas pesan igual | 0,93 | 0,91 | 0 | 5 |
| Rareza fuerte; palabras fuera del catálogo y números, con el peso máximo | 0,90 | 0,90 | 0 | 8 |
| Rareza fuerte; palabras fuera del catálogo y números, con peso neutro | 0,94 | 0,78 | **2** | 4 |
| **Rareza suave** (el efecto multiplicado por 0,3); palabras fuera del catálogo y números, con peso neutro | **0,94** | **0,91** | **0** | **4** |

Lecturas:

- La versión clásica, aplicada sin más, **empeora**: las palabras con que el cliente pide («que tenés para…») no
  están en el catálogo, quedan como «rarísimas» y arrastran el puntaje.
- Bajarle el peso a los números es peligroso: aparecen **falsos únicos** (una medida que no existe queda como si
  fuera ese producto), que es el error caro porque la precarga lo propone sin preguntar.
- La versión suave gana una consulta de tipo `necesidad` y no pierde ninguna. La mejora es **chica**: no es una
  prioridad, pero muestra para qué sirve tener el buscador medido.

**Propuesta.** Ponderar cada palabra según lo rara que es en el catálogo, con un efecto suave, y dejar con peso
neutro tanto las palabras que el catálogo no tiene como los números. Los números siguen sin aproximarse (RF-38). La
rareza se calcula al armar el `Indice`, que ya se reconstruye solo cuando cambia el catálogo.

**Criterio de aceptación.**

- `evaluar_busqueda --grupo reservado`: `falsos_unicos` sigue en 0 y ningún `tipo` baja en `acierto_1` ni en
  `unicos_correctos`.
- El total de `negados` no sube.
- Un test con un catálogo sintético: entre dos productos que comparten la palabra común y difieren en la rara, la
  búsqueda que nombra la rara pone primero al que la tiene.

**Esfuerzo.** Bajo. **Riesgo.** Cambia el orden de candidatos que hoy ven los tests de `test_busqueda.py`; los que
fallen hay que revisarlos uno por uno, no ajustarlos a ciegas.

### MC-2 · Descripciones de skills que no se pisan

**Concepto (Clase 7, §5.2 y §5.3).** El nombre y la descripción de una skill son su clasificador: es lo único que
ve el modelo antes de decidir cuál abrir. Si dos descripciones prometen lo mismo, a veces se abre la equivocada.

**Hoy.** `informar` dice que responde «pedidos de hablar con una persona» (`internal/skills/informar/skill.md:3`), y
`derivar_o_cancelar` dice lo mismo en su `cuando` (`internal/skills/derivar_o_cancelar/skill.md:4`). El ruteo en
código atrapa las frases que conoce (`internal/agent/graph.py:125`), pero cuando no, el modelo elige entre dos
descripciones que se pisan. Si elige `informar`, la skill lo manda a leer la otra (`informar/skill.md:14`): una
vuelta más para llegar al mismo lugar.

**Propuesta.** Sacar «o pedidos de hablar con una persona» de la `funcion` de `informar` y dejar en su cuerpo solo
el aviso de que eso lo atiende `derivar_o_cancelar`. Revisar el resto de los pares de skills con el mismo criterio:
cada situación del cliente aparece en el `cuando` de una sola.

**Criterio de aceptación.**

- Un test lee el índice de skills y comprueba que «persona» no aparece en la descripción de `informar`.
- Un caso nuevo en `data_example/mensajes.json`, grupo `desarrollo`, con un pedido de hablar con alguien escrito de
  una forma que `es_pedido_humano` no reconoce (verificarlo en el mismo test): espera la derivación, sin pasar por
  `informar`.
- `evaluar_agente` antes y después: sin baja en `reservado`.

**Esfuerzo.** Bajo.

### MC-3 · Descripciones de tools sin referencias internas

**Concepto (Clase 7, §3.1).** La descripción de una tool es parte del prompt: el modelo no ve el código, ve eso. Lo
que no le sirve para decidir es ruido que ocupa contexto en todas las llamadas.

**Hoy.** Las descripciones de `discard_order_draft` y de `escalate_to_human` terminan con «(RF-26)» y con
«(RF-27, Issue #84)» (`internal/agent/tool_registry.py:749` y `:757`). Son referencias para el equipo, no para el
modelo. Además, le enseñan identificadores que los guardrails después le prohíben decir.

**Propuesta.** Sacar esas referencias de las descripciones; si hacen falta para el equipo, van en un comentario al
lado del `ToolSpec`. No se renombra ninguna tool (eso queda fuera de alcance en la spec de la clase 7).

**Criterio de aceptación.** Un test recorre `TOOLS` y falla si una descripción, o la de un campo de su esquema,
tiene un ID de requisito, de criterio o de decisión, o un número de issue.

**Esfuerzo.** Muy bajo.

### MC-4 · Decisiones estructurales como ADRs

**Concepto (Clase 6, §8.1 a §8.4).** El CHANGELOG responde *qué* cambió; un ADR, *por qué* se decidió así. Un ADR es
un archivo por decisión y no se edita: si la decisión cambia, uno nuevo reemplaza al anterior, y queda la historia,
incluidas las decisiones que se revirtieron. Para un agente de código que arranca cada sesión sin memoria, es el
registro del porqué.

**Hoy.** Las decisiones son filas `D-xx` repartidas en las tablas de ocho specs (56 filas). Se editan en el lugar:
D-07 dice que el tope «fue 15 y después 8» (`docs/spec/README.md:677`), sin el motivo de cada cambio. Para los
parámetros alcanza; para las decisiones de arquitectura se pierde el porqué.

**Propuesta.**

1. `docs/adr/NNNN-titulo.md`, con Título, Estado (Propuesto, Aceptado o Reemplazado por NNNN), Contexto, Decisión
   y Consecuencias. Las `D-xx` siguen para parámetros y decisiones chicas.
2. Los primeros ADRs, escritos a partir de lo que ya está decidido:
   - Grafo de LangGraph con un loop acotado y una tool terminal obligatoria.
   - Guardrails deterministas, sin un segundo LLM (D-17).
   - Búsqueda léxica tolerante, sin embeddings (spec de búsqueda tolerante y §4 de la clase 8).
   - SQLite con una sola instancia.
3. Un chequeo en el CI, como pide la Clase 6 §8.4 para lo que «lo hace un script»: formato del ADR y que un ADR en
   estado Aceptado no cambie, salvo la línea de Estado al reemplazarlo. Si una persona tiene que juzgar si lo
   anotado es fiel, eso lo hace la revisión de la PR.

**Criterio de aceptación.** Un test valida el formato de cada archivo de `docs/adr/`; el script del CI rechaza una
PR que edite el cuerpo de un ADR Aceptado. `CONTRIBUTING.md` y `AGENTS.md` dicen, en una línea, cuándo se escribe uno.

**Esfuerzo.** Bajo.

### MC-5 · Probar los tests de guardrails y búsqueda con mutantes

**Concepto (Clase 6, §3.8).** Que un test pase por una línea no quiere decir que controle lo que hace esa línea. Las
pruebas con mutantes cambian el código a propósito (un `>` por un `>=`, un 0,6 por un 0,59) y corren los tests: si
ninguno falla, falta un control en ese borde.

**Hoy.** Lo determinista y crítico tiene muchos tests: `guardrails.py` (913 líneas, 977 de tests) y
`busqueda_service.py` (599 líneas, 598 de tests). No hay forma de saber cuántos de esos tests controlan los bordes,
por ejemplo el puntaje mínimo de un match (`busqueda_service.py:18`) o el margen entre candidatos (`:19`).

**Propuesta.** Correr `mutmut` (u otra herramienta equivalente) **a mano, una vez y fuera del CI**, porque es lento,
solo sobre esos dos módulos. Cada mutante que sobrevive se resuelve con un test nuevo o se anota como equivalente
(un cambio que no altera el comportamiento). El resultado queda en una sección de `MEJORAS.md`.

**Criterio de aceptación.** Informe con mutantes generados, muertos y vivos por módulo. Ningún mutante vivo en las
constantes de puntaje, en la regla de que los números no se aproximan ni en las reglas de precio y de stock de los
guardrails.

**Esfuerzo.** Bajo a medio. **Pregunta abierta:** sumar la herramienta a `requirements-dev.txt` o instalarla solo
para la corrida (P-2).

### MC-6 · Juez del tono, validado contra personas

**Concepto (Clase 5, §9.7; Clase 9 benchmarks, §4.2).** Un LLM puede juzgar respuestas abiertas, pero antes hay que
medir cuánto coincide con personas que siguen la misma guía. Se usa el kappa de Cohen, que descuenta las
coincidencias que salen por azar, y recién si es alto se confía en el juez. Cuando la respuesta no es «sí o no», el
criterio tiene que estar escrito como rúbrica.

**Hoy.** `chequear_estilo` (`internal/evaluar_agente.py:78`) controla con expresiones regulares lo que se puede
contar: SKU, negritas, «presentación», más de una pregunta, el número de stock, nombres internos e inglés. Lo que no
se puede contar con una expresión regular no se mide: si suena a mostrador, si la pregunta es la que destraba el
pedido o si cierra con lo que falta para avanzar (las reglas de «Cómo hablás» en `system_prompt.md`).

**Propuesta.**

1. Una rúbrica corta, de 0 a 2 por criterio, con un ejemplo por nivel, a partir de «Cómo hablás».
2. Dos personas del equipo puntúan unas 60 respuestas de las corridas de evaluación, que tienen datos ficticios.
3. Un juez LLM, de **otra familia** que el modelo del agente para no premiar al que piensa como él (Clase 5, §9.7),
   puntúa las mismas.
4. Si el kappa entre el juez y las personas es al menos 0,6, el juez entra a `evaluar_agente` como métrica
   opcional (`--juez`); si no, se mejora la rúbrica y se repite.

El juez **solo evalúa**: nunca corre en el turno del cliente. Los guardrails de producción siguen deterministas
(D-17).

**Criterio de aceptación.** La rúbrica versionada, el kappa medido y anotado, y la métrica `tono_ok_pct` en el
`.json` de la corrida solo cuando el kappa pasa el umbral.

**Esfuerzo.** Medio, sobre todo por el tiempo de las personas.

---

## 5. Errores y casos borde

| Situación | Qué se hace |
|---|---|
| MC-1 mejora el total pero baja algún `tipo` en `reservado` | No entra: se ajusta la variante o se descarta. La métrica que manda es la del grupo reservado |
| MC-1 cambia el orden en un test de `test_busqueda.py` | Se revisa caso por caso si el orden nuevo es mejor o peor; no se cambia el test para que pase |
| MC-2 o MC-3 bajan la evaluación del agente | Se revierte y se anota en la PR: una descripción más corta no siempre ayuda (Clase 6, §3.5) |
| MC-5 encuentra un mutante vivo en una regla de guardrails | Es un bug potencial de seguridad: va primero, con su test |
| MC-6 da kappa bajo dos veces | Se deja el juez fuera de la evaluación y se anota por qué |

## 6. Preguntas abiertas

| ID | Pregunta | Propuesta |
|---|---|---|
| P-1 | ¿Las mejoras aprobadas van a esta spec o a la de su tema (búsqueda tolerante, skills y respuestas)? | A la de su tema, como proponen las specs de las clases 7 y 8 |
| P-2 | ¿`mutmut` entra a `requirements-dev.txt`? | No: se instala solo para la corrida, porque no corre en el CI |
| P-3 | ¿Vale MC-1 si gana una sola consulta de 124? | Sí, solo si `reservado` lo confirma; si no, se descarta y queda anotado |
| P-4 | ¿Quiénes puntúan para MC-6? | Dos integrantes del equipo, por separado, sin ver la nota del otro |

## 7. Plan de implementación

1. `feature/descripciones-skills-y-tools`: MC-2 y MC-3, con la corrida de `evaluar_agente` antes y después.
2. `feature/adr`: MC-4, con los cuatro ADRs iniciales y el chequeo del CI.
3. `feature/busqueda-rareza`: MC-1, medido con `evaluar_busqueda --grupo reservado`.
4. `feature/mutantes-guardrails-busqueda`: MC-5, con los tests nuevos que salgan.
5. `feature/juez-tono`: MC-6, después de que las specs de las clases 7 y 8 estén integradas.
