from fastapi import FastAPI, Request, HTTPException, BackgroundTasks

# Replace the receive() signature and body structure:

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

        # Acknowledge Meta immediately, process in background
        background_tasks.add_task(process_message, msg, value)
        return {"status": "ok"}

    except Exception as e:
        logger.exception(f"Error procesando mensaje: {e}")
        return {"status": "error", "detail": str(e)}


def process_message(msg: dict, value: dict):
    try:
        telefono  = msg["from"]
        texto     = msg["text"]["body"]
        ahora     = datetime.now()
        fecha_str = ahora.strftime("%Y-%m-%d")
        hora_str  = ahora.strftime("%Y-%m-%d %H:%M:%S")
        contacts  = value.get("contacts", [])
        nombre    = contacts[0]["profile"]["name"] if contacts else "Usuario"

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
                "[LÍMITE ALCANZADO]", fecha_str, hora_str,
            ])
            return

        respuesta = ask_claude(historial, texto)
        sheets_append([
            str(int(ahora.timestamp() * 1000)),
            telefono, nombre, texto,
            respuesta[:500], fecha_str, hora_str,
        ])
        send_whatsapp(telefono, respuesta)

    except Exception as e:
        logger.exception(f"Error procesando mensaje: {e}")
