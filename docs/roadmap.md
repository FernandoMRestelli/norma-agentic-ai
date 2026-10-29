# 🚀 Norma Agentic AI | Roadmap v1

> **De un pedido informal a una orden clara, validada y lista para preparar.**  
Guía de desarrollo del MVP · Equipo de 3 personas

## 🎯 La visión en 30 segundos

Un cliente envía su pedido por **texto, audio o imagen** (chat web o WhatsApp). Un asistente lo interpreta, busca los productos en el catálogo y muestra **qué entendió**. El cliente elige **✅ Confirmar** o **✏️ Corregir**. Solo después de confirmar se registra la orden en la planilla operativa y aparece en el dashboard.

```
flowchart LR  
    A\[💬 Cliente: texto / audio / imagen\] --\> B\[🤖 Interpretar y buscar productos\]  
    B --\> C\{✅ ¿Pedido correcto?\}  
    C --\>|Corregir| B  
    C --\>|Confirmar| D\[📋 Google Sheet: pedidos\]  
    D --\> E\[📊 Dashboard\]
```

## 🧭 Alcance de esta versión

| ✅ MVP v1 | ⏭️ Después, en v2 |
| - | - |
| Pedidos por texto, audio e imagen | Derivación a Cristian / atención humana |
| Catálogo, precios y stock desde Google Sheets | API del sistema de gestión real |
| Interpretación de productos y cantidades | Órdenes registradas en el sistema real |
| Tarjeta **Confirmar / Corregir** | Reposición sugerida a proveedores |
| Registro de pedidos en una segunda Google Sheet | Analítica avanzada y automatizaciones |
| Dashboard operativo básico y conexión a WhatsApp | Funcionalidades adicionales según uso real |


## 🛣️ Las 7 etapas del MVP

### 01 · 🧱 Base compartida

**Meta:** todos pueden ejecutar el proyecto.  
**Construimos:** repositorio, estructura, dependencias, configuración y API mínima.  
**Listo cuando:** los tres integrantes pueden levantar el backend y ver una respuesta de prueba.

### 02 · 🗂️ Datos del negocio

**Meta:** consultar catálogo, precios y stock.  
**Construimos:** Google Sheet de entrada, conexión autenticada y funciones de búsqueda.  
**Listo cuando:** consultamos un SKU y obtenemos producto, precio y stock sin editar el código.

### 03 · 🧠 Pedidos multimodales

**Meta:** entender lo mismo desde texto, audio o imagen.  
**Construimos:** recepción, transcripción/visión, interpretación con LLM y esquema único de pedido.  
**Listo cuando:** los tres formatos generan un borrador estructurado; si algo es ambiguo, el asistente pide aclaración.

### 04 · ✅ Confirmación del cliente

**Meta:** evitar órdenes incorrectas.  
**Construimos:** tarjeta con líneas, cantidades, presentación y opciones **Confirmar / Corregir**.  
**Listo cuando:** una corrección genera un nuevo resumen y **ningún pedido se registra sin confirmación explícita**.

### 05 · 📋 Registro y seguimiento

**Meta:** convertir el pedido confirmado en trabajo operativo.  
**Construimos:** ID único, registro confiable y Google Sheet de salida con fecha, cliente, productos, cantidades, observaciones y estado.  
**Listo cuando:** cada confirmación crea **una sola** orden y el equipo puede seguir `Recibido → Preparado → Retirado` (con opción `Cancelado`).

### 06 · 📊 Dashboard

**Meta:** ver qué está pasando en el negocio.  
**Construimos:** panel de pedidos por estado, pedidos recientes y vista del stock disponible.  
**Listo cuando:** el panel refleja los datos de las fuentes compartidas sin cargar información manualmente.

### 07 · 📱 WhatsApp de punta a punta

**Meta:** operar desde el canal cotidiano del cliente.  
**Construimos:** webhook, mensajes entrantes, multimedia y confirmación mediante botones interactivos cuando estén disponibles (o alternativa textual).  
**Listo cuando:** un pedido desde WhatsApp se interpreta, confirma y aparece en el seguimiento y dashboard.

## 👥 Cómo nos organizamos (3 personas)

| Foco | Responsabilidad principal |
| - | - |
| **Backend y APIs** | Rutas, lógica de pedidos, estados y persistencia |
| **IA multimodal** | Agente, extracción de texto/audio/imagen y normalización |
| **Datos e integraciones** | Google Sheets de entrada/salida, catálogo y stock |
| **Frontend y experiencia** | Tarjeta de confirmación y dashboard |


Cada integrante también documenta y prueba sus cambios. Trabajamos con **ramas por tarea + Pull Requests** hacia `develop`, y pasamos `develop` a `main` con cada release (ver [CONTRIBUTING.md](../CONTRIBUTING.md)).

## 🏁 ¿Cuándo está listo el MVP?

- [ ] Un pedido de prueba llega por texto, audio y foto.

- [ ] Se reconocen los productos del catálogo o se pide aclaración.

- [ ] El cliente ve y puede **confirmar o corregir** el pedido.

- [ ] Nunca se registra una orden antes de la confirmación.

- [ ] Una confirmación crea una sola orden identificable.

- [ ] La orden aparece en el **Google Sheet de seguimiento**.

- [ ] El dashboard muestra pedidos y stock de forma consistente.

- [ ] El flujo se puede ejecutar desde WhatsApp.


