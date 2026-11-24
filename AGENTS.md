# Reglas para asistentes de IA y agentes autónomos (Codex, Gemini, Claude)

1. **Protección de secretos y variables de entorno:**
   - **NUNCA** leas, envíes ni modifiques archivos `.env` o `.env.*` (únicamente `.env.example` está permitido para consultar variables de configuración).
   - **NUNCA** accedas a claves o credenciales dentro del directorio `credentials/`.

2. **Privacidad de clientes y datos sensibles:**
   - `data/` es la carpeta de archivos de trabajo locales del equipo y **no se versiona** (`.gitignore`). Está permitido leer los **CSV** que haya ahí (catálogos, pedidos) para desarrollo.
   - **NUNCA** leas ni proceses volcados de datos reales de clientes, aunque estén en `data/`: contactos `.vcf`, exports de chats ni copias de bases de producción.
   - Las bases SQLite **locales** del entorno de desarrollo (`data/norma.db`, `data/checkpoints.db`, `data/mlflow/`) guardan las conversaciones de prueba del equipo con su propio bot. Se pueden **consultar en modo lectura** para depurar el agente (estado del grafo, propuestas, renglones, trazas). No se modifican a mano, y su contenido (textos de mensajes, ids de Telegram, nombres) no se copia a commits, PRs, issues, specs ni servicios externos: en esos lugares se describe el caso con datos ficticios.
   - `data_example/` mantiene los datos ficticios **versionados** como referencia del formato; para pruebas automatizadas, usá esos o los datos sintéticos de `tests/`.

3. **Entorno local:**
   - Cada colaborador utiliza sus propias credenciales en local. No solicites ni almacenes secretos de otros desarrolladores.
