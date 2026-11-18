# AlmuerzoBot 🍽️

Webhook WhatsApp + Claude + Google Sheets. Reemplaza el flujo n8n original.

---

## Estructura

```
main.py           ← servidor completo
requirements.txt  ← dependencias Python
render.yaml       ← configuración deploy Render.com
```

---

## Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `ANTHROPIC_API_KEY` | Desde console.anthropic.com |
| `WHATSAPP_TOKEN` | Token de acceso permanente Meta |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número en Meta Business |
| `WHATSAPP_VERIFY_TOKEN` | String que tú defines (ej: `almuerzo2024`) |
| `GOOGLE_SHEETS_ID` | `1mYVizMR567guLlUAkzYyiTdasLL2j2feIyfY37rUTOs` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON completo de Service Account de Google |
| `INBOX_PASSWORD` | Contraseña de la bandeja manual `/inbox` (si no se define, la bandeja queda deshabilitada) |
| `SESSION_SECRET` | Secreto para firmar la cookie de sesión (Render lo genera solo) |

---

## Deploy en Render.com

1. Sube este proyecto a un repositorio GitHub
2. Entra a [render.com](https://render.com) → New Web Service
3. Conecta el repo → Render detecta `render.yaml` automáticamente
4. Agrega las variables de entorno en el dashboard de Render
5. Deploy → obtendrás una URL tipo `https://almuerzobot.onrender.com`

---

## Configurar Google Service Account

1. Ve a [console.cloud.google.com](https://console.cloud.google.com)
2. Crea un proyecto → habilita **Google Sheets API**
3. IAM → Service Accounts → Crear cuenta → Descargar JSON
4. Comparte el Google Sheet con el email de la service account (editor)
5. Pega el JSON completo en la variable `GOOGLE_SERVICE_ACCOUNT_JSON`

---

## Configurar Webhook en Meta (cuando tengas credenciales)

1. Meta Business → WhatsApp → Configuración → Webhooks
2. URL de callback: `https://TU-URL.onrender.com/webhook`
3. Verify token: el mismo que pusiste en `WHATSAPP_VERIFY_TOKEN`
4. Suscribir a: `messages`

---

## Estructura Google Sheet esperada

La hoja debe llamarse `AlmuerzoBot_DB` con estas columnas en fila 1:

```
id | telefono | nombre_usuario | mensaje_recibido | respuesta_bot | fecha_contacto | created_at
```

---

## Endpoints

| Método | Path | Descripción |
|---|---|---|
| `GET` | `/` | Health check |
| `GET` | `/webhook` | Verificación Meta |
| `POST` | `/webhook` | Recibe mensajes WhatsApp |

---

## Bandeja manual (`/inbox`)

Interfaz web para conversar a mano con quienes escriben al WhatsApp.

- **Tomar control** por contacto: el bot deja de responderle y sus mensajes quedan en la bandeja ("sin responder"). **Devolver al bot** reactiva la respuesta automática.
- **Todo en manual** (interruptor global): el bot no responde a nadie. Al cambiarlo se reinician los controles individuales.
- Las respuestas manuales se guardan en `AlmuerzoBot_DB` (columna `msg_id` = `manual`) y el bot las ve como contexto al retomar.
- El estado manual/automático se guarda en la hoja `AlmuerzoBot_Control` (se crea sola al iniciar; la service account necesita permiso de editor).
- WhatsApp solo permite texto libre dentro de las 24 h posteriores al último mensaje del cliente.

### Dominio `bot.almuerzo.cl`

1. Render → servicio → Settings → Custom Domains → agregar `bot.almuerzo.cl`.
2. En el DNS de almuerzo.cl crear un `CNAME` `bot` → `<servicio>.onrender.com`.
3. Esperar el certificado HTTPS automático y entrar a `https://bot.almuerzo.cl/inbox`.
4. (Opcional) Actualizar el webhook en Meta a `https://bot.almuerzo.cl/webhook`.
