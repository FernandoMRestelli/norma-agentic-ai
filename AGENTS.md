# Reglas para asistentes de IA y agentes autónomos (Codex, Gemini, Claude)

1. **Protección de secretos y variables de entorno:**
   - **NUNCA** leas, envíes ni modifiques archivos `.env` o `.env.*` (únicamente `.env.example` está permitido para consultar variables de configuración).
   - **NUNCA** accedas a claves o credenciales dentro del directorio `credentials/`.

2. **Privacidad de clientes y datos sensibles:**
   - `data/` es la carpeta de archivos de trabajo locales del equipo y **no se versiona** (`.gitignore`). Está permitido leer los **CSV** que haya ahí (catálogos, pedidos) para desarrollo.
   - **NUNCA** leas ni proceses volcados de datos reales de clientes, aunque estén en `data/`: contactos `.vcf` ni las bases SQLite del agente (`*.db`, que guardan los mensajes de las conversaciones).
   - `data_example/` mantiene los datos ficticios **versionados** como referencia del formato; para pruebas automatizadas, usá esos o los datos sintéticos de `tests/`.

3. **Entorno local:**
   - Cada colaborador utiliza sus propias credenciales en local. No solicites ni almacenes secretos de otros desarrolladores.
