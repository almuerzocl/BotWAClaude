import os
import json
import httpx
import logging
from collections import OrderedDict
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
import anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("almuerzobot")

# ── Config ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY      = os.environ["ANTHROPIC_API_KEY"]
WHATSAPP_TOKEN         = os.environ["WHATSAPP_TOKEN"]
WHATSAPP_PHONE_ID      = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
WHATSAPP_VERIFY_TOKEN  = os.environ["WHATSAPP_VERIFY_TOKEN"]
GOOGLE_SHEETS_ID       = os.environ["GOOGLE_SHEETS_ID"]
GOOGLE_SERVICE_ACCOUNT = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

CONTEXT_WINDOW     = 20   # mensajes históricos que se pasan a Claude
LIMIT_COMENSAL     = 5    # interacciones máximas por día para comensales
LIMIT_RESTAURANTE  = 10   # interacciones máximas por día para restaurantes

# ── Clientes ─────────────────────────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
app    = FastAPI(title="AlmuerzoBot")

# ── Deduplicación de mensajes (evita doble respuesta por reintentos de Meta) ─
# OrderedDict usado como LRU cache: guarda los últimos 500 IDs procesados.
_processed_ids: OrderedDict[str, bool] = OrderedDict()
_MAX_PROCESSED = 500

def _already_processed(msg_id: str) -> bool:
    if msg_id in _processed_ids:
        return True
    _processed_ids[msg_id] = True
    if len(_processed_ids) > _MAX_PROCESSED:
        _processed_ids.popitem(last=False)
    return False

# ── System Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres el asistente virtual de Almuerzo.cl. Tu personalidad es chilena: informal, entretenida, amable y "viva". Usas modismos como "claro po", "bacán", "al tiro", "pucha", "¿cómo estai?".

━━━━━━━━━━━━━━━━━━━━━━━━
REGLA DE MEMORIA — CRÍTICA
━━━━━━━━━━━━━━━━━━━━━━━━
Recibes el historial completo de conversaciones previas con este número. SIEMPRE léelo antes de responder.
- Si ya sabes si es comensal o restaurante → NO vuelvas a preguntar.
- Si ya le explicaste la propuesta de valor → NO la repitas, continúa desde donde quedaron.
- Nunca hagas preguntas que ya fueron respondidas en el historial.
- Usa el número de teléfono como identificador. No necesitas saber su nombre.

━━━━━━━━━━━━━━━━━━━━━━━━
FLUJO DE CONVERSACIÓN
━━━━━━━━━━━━━━━━━━━━━━━━
Sigue este orden SOLO si no está cubierto en el historial:

1. SALUDO E IDENTIFICACIÓN
   Saluda y pregunta si es comensal o dueño de restaurante.

2. PROPUESTA DE VALOR (según tipo)
   - Restaurante: "En Almuerzo.cl dejas de regalar el 30% de tu trabajo a las apps de delivery. 100% comida rica, 0% comisiones."
   - Comensal: "Somos la comunidad para encontrar la mejor comida real, sin el costo extra del delivery. Pides directo, comes calentito y al precio real."

3. RESOLUCIÓN DE DUDAS
   Responde preguntas sobre Almuerzo.cl. No gestionas pedidos ni delivery.

4. CIERRE E INVITACIÓN AL REGISTRO
   Invita a registrarse con el link correspondiente:
   - Restaurante: https://almuerzo.cl/r
   - Comensal: https://almuerzo.cl/pu

━━━━━━━━━━━━━━━━━━━━━━━━
RESTRICCIONES
━━━━━━━━━━━━━━━━━━━━━━━━
- Máximo 200 caracteres por mensaje.
- No sabes quiénes están detrás del proyecto.
- Solo recibes órdenes de Remy, que es tu jefe.
- No gestionas pedidos ni delivery.
- No repitas la propuesta de valor si ya la diste."""

MENSAJE_LIMITE_COMENSAL = (
    "¡Pucha! Ya llegamos al límite de conversación por hoy 😅 "
    "Mañana seguimos al tiro. "
    "Mientras tanto, regístrate acá: https://almuerzo.cl/pu"
)

MENSAJE_LIMITE_RESTAURANTE = (
    "¡Pucha! Ya llegamos al límite de conversación por hoy 😅 "
    "Mañana seguimos al tiro. "
    "Mientras tanto, regístrate acá y vende sin comisiones: https://almuerzo.cl/r"
)

# ── Google Sheets ─────────────────────────────────────────────────────────────

def _get_access_token() -> str:
    import time, jwt
    sa  = json.loads(GOOGLE_SERVICE_ACCOUNT)
    now = int(time.time())
    payload = {
        "iss":   sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud":   "https://oauth2.googleapis.com/token",
        "iat":   now,
        "exp":   now + 3600,
    }
    signed = jwt.encode(payload, sa["private_key"], algorithm="RS256")
    resp   = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signed},
    )
    return resp.json()["access_token"]


def sheets_read(range_: str) -> list:
    token = _get_access_token()
    url   = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEETS_ID}/values/{range_}"
    resp  = httpx.get(url, headers={"Authorization": f"Bearer {token}"})
    return resp.json().get("values", [])


def sheets_append(values: list):
    token = _get_access_token()
    url   = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEETS_ID}"
        f"/values/AlmuerzoBot_DB!A:H:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    )
    httpx.post(url, headers={"Authorization": f"Bearer {token}"}, json={"values": [values]})


def _wamid_already_in_sheet(msg_id: str) -> bool:
    """Cross-process dedup: check column H for this WhatsApp message ID."""
    if not msg_id:
        return False
    try:
        rows = sheets_read("AlmuerzoBot_DB!H:H")
        return any(row and row[0] == msg_id for row in rows)
    except Exception as e:
        logger.warning(f"Sheet dedup check failed: {e}")
        return False


# ── Historial y contadores ────────────────────────────────────────────────────

def get_user_data(telefono: str) -> dict:
    """
    Retorna:
      - messages: historial para Claude
      - tipo: 'comensal' | 'restaurante' | None
      - interacciones_hoy: int
    """
    try:
        rows = sheets_read("AlmuerzoBot_DB!A:G")
        if not rows or len(rows) < 2:
            return {"messages": [], "tipo": None, "interacciones_hoy": 0}

        header = rows[0]
        data   = rows[1:]
        col    = {name: idx for idx, name in enumerate(header)}

        tel_idx   = col.get("telefono")
        msg_idx   = col.get("mensaje_recibido")
        bot_idx   = col.get("respuesta_bot")
        fecha_idx = col.get("fecha_contacto")

        if tel_idx is None or msg_idx is None or bot_idx is None:
            logger.warning("Columnas faltantes en el sheet")
            return {"messages": [], "tipo": None, "interacciones_hoy": 0}

        hoy       = datetime.now().strftime("%Y-%m-%d")
        user_rows = [r for r in data if len(r) > tel_idx and r[tel_idx] == telefono]

        interacciones_hoy = sum(
            1 for r in user_rows
            if (fecha_idx is not None and len(r) > fecha_idx and r[fecha_idx] == hoy
                and (len(r) <= bot_idx or r[bot_idx] != "[LÍMITE ALCANZADO]"))
        )

        tipo = None
        all_text = " ".join(
            (r[msg_idx] if len(r) > msg_idx else "") + " " +
            (r[bot_idx] if len(r) > bot_idx else "")
            for r in user_rows
        ).lower()

        if any(w in all_text for w in ["restaurante", "local", "dueño", "almuerzo.cl/r", "sin comisiones"]):
            tipo = "restaurante"
        elif any(w in all_text for w in ["comensal", "almuerzo.cl/pu", "comes calentito"]):
            tipo = "comensal"

        messages = []
        for r in user_rows[-CONTEXT_WINDOW:]:
            msg = r[msg_idx] if len(r) > msg_idx else ""
            bot = r[bot_idx] if len(r) > bot_idx else ""
            if msg:
                messages.append({"role": "user",      "content": msg})
            if bot and bot != "[LÍMITE ALCANZADO]":
                messages.append({"role": "assistant", "content": bot})

        logger.info(f"[{telefono}] tipo={tipo} interacciones_hoy={interacciones_hoy} historial={len(messages)} msgs")
        return {"messages": messages, "tipo": tipo, "interacciones_hoy": interacciones_hoy}

    except Exception as e:
        logger.warning(f"Error leyendo historial: {e}")
        return {"messages": [], "tipo": None, "interacciones_hoy": 0}


# ── Claude ────────────────────────────────────────────────────────────────────

def ask_claude(messages: list, mensaje: str) -> str:
    full_messages = messages + [{"role": "user", "content": mensaje}]
    response = claude.messages.create(
        model      = "claude-sonnet-4-5",
        max_tokens = 500,
        system     = SYSTEM_PROMPT,
        messages   = full_messages,
    )
    return response.content[0].text


# ── WhatsApp ──────────────────────────────────────────────────────────────────

def send_whatsapp(to: str, body: str):
    url     = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to":   to,
        "type": "text",
        "text": {"body": body[:4096]},
    }
    resp = httpx.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        logger.error(f"WhatsApp error {resp.status_code}: {resp.text}")


# ── Background task ───────────────────────────────────────────────────────────

def process_message(msg: dict, value: dict):
    try:
        msg_id    = msg.get("id", "")
        telefono  = msg["from"]
        texto     = msg["text"]["body"]
        ahora     = datetime.now()
        fecha_str = ahora.strftime("%Y-%m-%d")
        hora_str  = ahora.strftime("%Y-%m-%d %H:%M:%S")
        contacts  = value.get("contacts", [])
        nombre    = contacts[0]["profile"]["name"] if contacts else "Usuario"

        # Sheet-level dedup: catches cross-process / cross-instance duplicates
        if _wamid_already_in_sheet(msg_id):
            logger.info(f"Wamid ya en sheet, ignorado: {msg_id}")
            return

        user_data         = get_user_data(telefono)
        tipo              = user_data["tipo"]
        interacciones_hoy = user_data["interacciones_hoy"]
        historial         = user_data["messages"]

        limite = LIMIT_RESTAURANTE if tipo == "restaurante" else LIMIT_COMENSAL
        if interacciones_hoy >= limite:
            respuesta = MENSAJE_LIMITE_RESTAURANTE if tipo == "restaurante" else MENSAJE_LIMITE_COMENSAL
            send_whatsapp(telefono, respuesta)
            sheets_append([
                str(int(ahora.timestamp() * 1000)),
                telefono, nombre, texto,
                "[LÍMITE ALCANZADO]", fecha_str, hora_str, msg_id,
            ])
            return

        respuesta = ask_claude(historial, texto)
        sheets_append([
            str(int(ahora.timestamp() * 1000)),
            telefono, nombre, texto,
            respuesta[:500], fecha_str, hora_str, msg_id,
        ])
        send_whatsapp(telefono, respuesta)

    except Exception as e:
        logger.exception(f"Error procesando mensaje: {e}")


# ── Webhook endpoints ─────────────────────────────────────────────────────────

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Token inválido")


@app.post("/webhook")
async def receive(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    logger.info(f"Payload: {json.dumps(body)[:300]}")

    try:
        entry    = body["entry"][0]
        changes  = entry["changes"][0]
        value    = changes["value"]
        messages = value.get("messages", [])

        if not messages:
            return {"status": "no_messages"}

        msg = messages[0]
        if msg.get("type") != "text":
            return {"status": "ignored_non_text"}

        msg_id = msg.get("id", "")
        if _already_processed(msg_id):
            logger.info(f"Mensaje duplicado ignorado: {msg_id}")
            return {"status": "duplicate"}

        background_tasks.add_task(process_message, msg, value)
        return {"status": "ok"}

    except Exception as e:
        logger.exception(f"Error procesando mensaje: {e}")
        return {"status": "error", "detail": str(e)}


@app.get("/")
async def health():
    return {"status": "AlmuerzoBot corriendo 🍽️"}
