# Fuentes de datos preparadas

Estado al 4 de noviembre de 2026. Seguimiento: issue #2.

El responsable del proyecto confirmó que los archivos entregados son todo el material disponible.
Las imágenes y el audio son ejemplos de pedidos. La planilla es la fuente del catálogo.

## Inventario del material

| Tipo | Archivos recibidos | Contenidos únicos | Uso |
|---|---:|---:|---|
| Imagen | 5 | 3 | Pedidos manuscritos para evaluación posterior |
| Audio | 1 | 1 | Pedido hablado para evaluación posterior |
| Excel | 1 | 1 | Catálogo de productos y precios |
| **Total** | **7** | **5** | Incluye dos copias exactas de una imagen |

Se conservaron los siete originales en Drive privado. El inventario registra ID estable de fuente,
nombre, tipo, tamaño, SHA-256, duplicados y referencias de Drive. El archivo temporal de bloqueo del
editor de planillas se excluyó porque no contiene material adicional.

## Artefactos privados disponibles

| Artefacto | Estado y finalidad |
|---|---|
| Carpeta de originales | Siete archivos conservados sin anonimizar ni modificar |
| Inventario CSV | Siete registros con `drive_file_id`, `drive_url` y `drive_folder_id` |
| Base SQLite de preparación | Catálogo, inventario y cuatro ejemplos únicos; copia descargable, no base operativa del backend |
| Ejemplos JSON | Tres imágenes únicas y un audio, con referencias a Drive y anotación pendiente |
| Google Sheet 1 | Catálogo con 1.090 códigos únicos, descripción, grupo y precio; copia de valores originales en otra pestaña |
| Google Sheet 2 | Seguimiento vacío de pedidos confirmados, con estados y canales predefinidos |

La ubicación y los identificadores concretos se consultan en el inventario privado. No se publican
datos reales, rutas personales, identificadores de archivos privados ni credenciales en este repositorio.

## Catálogo para las primeras pruebas

- Se conservan los códigos de producto del Excel como SKU, sin generar una convención nueva.
- Se preservan las descripciones y los precios de la fuente.
- `stock` permanece vacío porque la fuente no informa disponibilidad real.
- Se agregó `stock_ficticio`: enteros de 0 a 100, generados con semilla fija y orden por SKU.
  Se usa exclusivamente para pruebas; la futura integración debe mapearlo explícitamente en ese entorno.
- Marca, presentación y alias no se completan manualmente en esta entrega. El responsable decidió
  usar el catálogo actual como punto de partida y revisar necesidades cuando funcione el agente.
- Las columnas originales sin encabezado se conservaron sin atribuirles significado de stock o cantidad.

Esto prepara una fuente de datos; no prueba aún su compatibilidad con el adaptador del backend ni
certifica precios o disponibilidad vigentes para operar con clientes.

## Seguimiento de órdenes

Una fila por orden, con los encabezados del ejemplo existente de Sheet 2:

`id_orden`, `fecha_hora`, `cliente`, `canal`, `productos`, `notas`, `estado`.

`productos` contiene el detalle de productos y cantidades. Los canales son `web` y `whatsapp`.
Los estados son `Recibido`, `Preparado`, `Retirado` y `Cancelado`. La hoja usa zona horaria argentina.
Se verificó que no tiene órdenes cargadas. El backend debe registrar exclusivamente órdenes con
confirmación explícita del cliente, como exige la spec del MVP; la planilla por sí sola no impone esa regla.

## Acceso y privacidad

- Los originales se conservan privados, sin anonimizar, por decisión del responsable.
- Se verificaron permisos sin acceso público, por enlace abierto ni para todo un dominio.
- El propietario conserva el acceso. Los otros dos colaboradores de GitHub aún deben indicar sus
  cuentas de Google para incorporarlos: la pertenencia al repositorio no concede acceso a Drive.
- La cuenta de servicio creada por el responsable tiene lectura de los siete originales, catálogo,
  inventario, base de preparación y ejemplos JSON, y escritura en Sheet 2.
- Los permisos se otorgaron por archivo. No habilitan listar la carpeta ni acceder automáticamente a
  nuevos archivos; cada incorporación requiere revisar permisos.
- La autenticación del backend con esa identidad todavía no se configuró ni se probó.
- Los permisos privados de Drive no controlan un eventual envío del contenido a un proveedor de IA.
  Ese procesamiento deberá definirse al implementar la evaluación.
- La copia local de trabajo excluye el directorio de originales de Git. Los hooks y reglas existentes
  del repositorio siguen aplicando. No se agrega material privado a los tests ni a artefactos públicos de CI.

## Evaluación pendiente

El responsable decidió conservar los pedidos tal como están y evaluar la interpretación cuando exista
una instancia funcional del agente. No hay transcripciones, SKU esperados ni cantidades verificadas.
El inventario técnico completo no equivale a una catalogación del contenido ni a un dataset validado.

Los mínimos del issue original (diez textos, cinco audios, cinco imágenes y tres casos ambiguos)
no se cumplen con este material. Se difieren para revisar el plan de evaluación; no se fabricaron
archivos, resultados esperados ni un glosario de términos todavía no verificados.

## Alcance de esta entrega

Preparación de fuentes y registro documental. No implementa el conector de Drive/Sheets, la
persistencia operativa, el agente ni pruebas de interpretación. Los datos sintéticos existentes en
`datos_ejemplo/` y `tests/` siguen siendo la fuente de las pruebas automatizadas.

### Pendientes

- Incorporar a los otros dos colaboradores cuando estén identificadas sus cuentas de Google.
- Configurar y probar la autenticación del backend, sin entregar claves a asistentes ni publicarlas.
- Implementar la lectura del catálogo y escritura idempotente de órdenes confirmadas en tareas posteriores.
- Evaluar los cuatro pedidos únicos con la instancia del agente; documentar correcciones, ambigüedades y glosario.
- Revisar la cobertura y los criterios originales de cierre del issue #2 con el equipo.

Este documento no declara completadas las etapas 02, 03 o 05 del roadmap ni cierra automáticamente el issue.
