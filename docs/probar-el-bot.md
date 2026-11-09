# Probar el bot de Telegram en local (Docker)

Guía rápida para levantar Norma con Docker y escribirle un pedido de prueba por Telegram.
No hace falta instalar Python: todo corre dentro del contenedor.

## 1. Requisitos

- [Docker](https://docs.docker.com/get-docker/) con Docker Compose v2 (`docker compose`, sin guion).
- El repo clonado y parado en la rama con los proveedores de LLM
  (`feature/proveedores-llm-free-tier`, o `develop` una vez mergeada).

```bash
git fetch origin
git checkout feature/proveedores-llm-free-tier
```

## 2. Tu propio bot de Telegram

**Cada integrante necesita su propio bot.** El polling usa un solo proceso por token:
si dos personas corren el mismo bot, Telegram responde `409 Conflict`.

1. En Telegram, escribile a **@BotFather** → `/newbot` → seguí los pasos.
2. Copiá el **token** que te da (algo tipo `123456789:AA...`).

## 3. Configurar el `.env`

Copiá el ejemplo y completá tus valores (tu `.env` es local y **nunca** se sube: está en `.gitignore`).

```bash
cp .env.example .env
```

Editá estas líneas:

```bash
# LLM de texto. Recomendado: OpenRouter (free tier amplio). Key gratis en https://openrouter.ai/keys
LLM_PROVIDER=openrouter
LLM_MODEL=nvidia/nemotron-3.5-lightning:free
LLM_API_KEY=tu_key_de_openrouter

# Telegram
TELEGRAM_BOT_TOKEN=tu_token_de_botfather
TELEGRAM_ALLOWED_USER_IDS=      # ver paso 5 si no sabés tu id
```

> El modelo tiene que soportar **tool calling** (el agente usa herramientas). Nemotron Lightning
> free lo soporta. Si probás otro, confirmá en su página de OpenRouter que acepte `tools`.

## 4. Verificar las claves

Chequea que Telegram y el LLM respondan, sin mostrar ninguna clave:

```bash
docker compose run --rm bot python -m internal.verificar_claves
```

Tenés que ver dos ✅:

```
✅ Telegram: bot @tu_bot, N usuarios en la lista blanca
✅ LLM: openrouter / nvidia/nemotron-3.5-lightning:free respondió «OK»
```

Si cambiaste dependencias o es la primera vez, reconstruí la imagen antes:

```bash
docker compose build bot
```

## 5. Levantar el bot

```bash
docker compose --profile bot up bot
```

> `docker compose up` a secas **no** levanta el bot: el servicio tiene un `profile`, por eso va `--profile bot`.

Queda en primer plano mostrando logs (`Ctrl+C` para frenar). Cuando veas
`Bot @tu_bot escuchando...`, escribile por Telegram.

**¿No sabés tu id de Telegram?** Dejá `TELEGRAM_ALLOWED_USER_IDS` vacío, levantá el bot,
escribile cualquier cosa y te responde con tu id. Lo pegás en el `.env`, `Ctrl+C` y volvés a levantar.

## 6. Probar un pedido

Los productos salen del catálogo de ejemplo (`datos_ejemplo/catalogo.csv`). Un pedido que anda:

> Hola, quiero 2 shampoo Pantene de 400 ml y un jabón Dove

El bot lee el catálogo, arma el pedido con precios y te lo propone para confirmar.

⚠️ **Usá siempre datos ficticios.** Los modelos free tier pueden usar lo que mandás para entrenar.
Nada de datos reales de clientes.

## Problemas comunes

| Síntoma | Causa y solución |
|---|---|
| `409 Conflict` | Otra persona corre el mismo bot. Cada uno con su propio bot (paso 2). |
| `Proveedor de LLM no soportado: openrouter` | Código viejo o imagen sin rebuild. `git pull` y `docker compose build bot`. |
| `504 DEADLINE_EXCEEDED` | La llamada va a Gemini. Revisá que `LLM_PROVIDER=openrouter` en el `.env`. |
| Responde `Tuve un problema...` | El modelo no emite tool calls. Cambiá `LLM_MODEL` por otro de tu lista (ej. North Mini Code free) y reiniciá. |
| El bot no arranca | Corré `docker compose run --rm bot python -m internal.verificar_claves` y mirá cuál ✅/❌ falla. |

## Comandos útiles

```bash
# Ver qué proveedor/modelo está leyendo (no muestra la key)
docker compose run --rm bot python -c "from internal.config import get_settings as g; s=g(); print('provider=', s.llm_provider, '| model=', s.llm_model)"

# Levantar en segundo plano y ver logs
docker compose --profile bot up -d bot
docker compose logs -f bot

# Frenar y limpiar
docker compose --profile bot down
```
