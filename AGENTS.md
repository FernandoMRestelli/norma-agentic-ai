# Reglas para asistentes de IA y agentes autónomos (Codex, Gemini, Claude)

1. **Protección de secretos y variables de entorno:**
   - **NUNCA** leas, envíes ni modifiques archivos `.env` o `.env.*` (únicamente `.env.example` está permitido para consultar variables de configuración).
   - **NUNCA** accedas a claves o credenciales dentro del directorio `credentials/` ni archivos `*service-account*.json` o `*service_account*.json`.

2. **Privacidad de clientes y datos sensibles:**
   - **NUNCA** leas ni proceses volcados de datos reales de clientes en `data/` (tales como exports de chats de WhatsApp, contactos `.vcf` o grabaciones multimedia).
   - Para desarrollo y pruebas automatizadas, utilizá exclusivamente los datos sintéticos de `tests/` o los catálogos y pedidos ficticios de `datos_ejemplo/`.

3. **Entorno local:**
   - Cada colaborador utiliza sus propias credenciales en local. No solicites ni almacenes secretos de otros desarrolladores.
