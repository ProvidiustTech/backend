"""
backend/routers/telegram.py

Handles:
  - POST /api/v1/telegram/webhook  — receives updates from Telegram
  - GET  /api/v1/telegram/ws       — WebSocket for the Next.js frontend
  - POST /api/v1/telegram/send     — sends a reply back to a Telegram user

Install deps:
  pip install httpx python-telegram-bot websockets

Env vars needed (add to your .env):
  TELEGRAM_BOT_TOKEN=your_bot_token_here
  TELEGRAM_WEBHOOK_SECRET=any_random_string_you_choose
"""

import os
import json
import asyncio
import logging
from typing import Dict, Set
from datetime import datetime

import httpx
from fastapi import APIRouter, Request, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "changeme")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

print("🔍 TELEGRAM CONFIG DEBUG:")
print(f"   BOT_TOKEN loaded: {bool(BOT_TOKEN)}")
print(f"   BOT_TOKEN length: {len(BOT_TOKEN)}")
if BOT_TOKEN:
    print(f"   Token starts with: {BOT_TOKEN[:10]}...")
else:
    print("   ⚠️  BOT_TOKEN IS EMPTY!")
print("─" * 60)

# ── In-memory conversation store ─────────────────────────────────────────────
# Maps telegram chat_id → list of messages
# Replace with Redis/DB in production
conversations: Dict[str, list] = {}

# Active WebSocket connections from the frontend dashboard
active_ws_clients: Set[WebSocket] = set()


# ── WebSocket Manager ─────────────────────────────────────────────────────────
async def broadcast(payload: dict):
    """Push a JSON payload to all connected frontend dashboards."""
    dead = set()
    for ws in active_ws_clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    active_ws_clients.difference_update(dead)


# ── Models ────────────────────────────────────────────────────────────────────
class SendMessageRequest(BaseModel):
    chat_id: str
    text: str
    agent_name: str = "Support Agent"


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Telegram calls this URL every time a user sends a message.
    We validate the secret token, parse the update, store it,
    and push it to all connected frontend WebSocket clients.
    """
    # Validate Telegram secret header
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    body = await request.json()
    logger.info(f"Telegram update: {body}")

    message = body.get("message") or body.get("edited_message")
    if not message:
        return JSONResponse({"ok": True})  # Ignore non-message updates (e.g. polls)

    chat_id = str(message["chat"]["id"])
    chat = message["chat"]
    sender_name = " ".join(filter(None, [
        chat.get("first_name", ""),
        chat.get("last_name", ""),
    ])).strip() or chat.get("username", "Unknown")

    text = message.get("text", "[non-text message]")
    msg_id = str(message.get("message_id", ""))
    timestamp = datetime.utcfromtimestamp(
        message.get("date", datetime.utcnow().timestamp())
    ).isoformat()

    # Build the normalized message object (matches frontend ChatMessage interface)
    normalized = {
        "id": msg_id,
        "from": "user",
        "text": text,
        "time": datetime.utcfromtimestamp(
            message.get("date", datetime.utcnow().timestamp())
        ).strftime("%H:%M"),
        "timestamp": timestamp,
        "channel": "Telegram",
        "chat_id": chat_id,
        "sender_name": sender_name,
    }

    # Store in memory
    if chat_id not in conversations:
        conversations[chat_id] = []
    conversations[chat_id].append(normalized)

    # Push to all frontend dashboards via WebSocket
    await broadcast({
        "event": "new_message",
        "conversation_id": chat_id,
        "sender_name": sender_name,
        "channel": "Telegram",
        "preview": text[:60],
        "message": normalized,
    })

    return JSONResponse({"ok": True})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_ws_clients.add(websocket)
    logger.info(f"Frontend WebSocket connected. Total: {len(active_ws_clients)}")

    try:
        # Send initial data
        await websocket.send_json({
            "event": "init",
            "conversations": {
                chat_id: {
                    "messages": msgs,
                    "sender_name": msgs[-1]["sender_name"] if msgs else "Unknown",
                    "channel": "Telegram",
                    "preview": msgs[-1]["text"][:60] if msgs else "",
                }
                for chat_id, msgs in conversations.items()
            }
        })

        while True:
            data = await websocket.receive_json()
            if data.get("action") == "send_message":
                await _send_telegram_message(
                    chat_id=data["chat_id"],
                    text=data["text"],
                    agent_name=data.get("agent_name", "Support"),
                )
    except WebSocketDisconnect:
        active_ws_clients.discard(websocket)
        logger.info("Frontend WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        active_ws_clients.discard(websocket)
        
        
        
@router.get("/conversations")
async def get_conversations():
    """REST fallback: returns all active Telegram conversations."""
    return {
        "conversations": [
            {
                "id": chat_id,
                "channel": "Telegram",
                "sender_name": msgs[-1]["sender_name"] if msgs else "Unknown",
                "preview": msgs[-1]["text"][:60] if msgs else "",
                "messages": msgs,
                "time": msgs[-1]["time"] if msgs else "",
            }
            for chat_id, msgs in conversations.items()
        ]
    }


@router.post("/send")
async def send_message(body: SendMessageRequest):
    """Send a reply from the dashboard to a Telegram user."""
    result = await _send_telegram_message(body.chat_id, body.text, body.agent_name)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to send Telegram message")
    return {"ok": True}


@router.post("/set-webhook")
async def set_webhook(request: Request):
    try:
        body = await request.json()
        url = body.get("url")
        if not url:
            return {"ok": False, "error": "url required"}

        print(f"Setting webhook to: {url}")
        print(f"Using BOT_TOKEN: {bool(BOT_TOKEN)}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{TELEGRAM_API}/setWebhook",
                json={
                    "url": url,
                    "secret_token": WEBHOOK_SECRET,
                    "allowed_updates": ["message", "edited_message"],
                    "drop_pending_updates": True
                }
            )
            result = resp.json()
            print("Telegram Response:", result)
            return result
    except Exception as e:
        print("Set webhook error:", e)
        return {"ok": False, "error": str(e)}


# ── Internal helper ───────────────────────────────────────────────────────────
async def _send_telegram_message(chat_id: str, text: str, agent_name: str = "Support") -> bool:
    """Calls Telegram's sendMessage API and stores the outgoing message locally."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return False

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )

    if resp.status_code != 200:
        logger.error(f"Telegram sendMessage failed: {resp.text}")
        return False

    # Store outgoing message in local history
    outgoing = {
        "id": str(resp.json().get("result", {}).get("message_id", "")),
        "from": "human",
        "text": text,
        "time": datetime.utcnow().strftime("%H:%M"),
        "channel": "Telegram",
        "chat_id": chat_id,
        "agent": agent_name,
    }
    if chat_id in conversations:
        conversations[chat_id].append(outgoing)

    # Broadcast to other open dashboard tabs
    await broadcast({
        "event": "message_sent",
        "conversation_id": chat_id,
        "message": outgoing,
    })

    return True


@router.get("/debug-token")
async def debug_token():
    return {
        "bot_token_set": bool(BOT_TOKEN),
        "token_length": len(BOT_TOKEN),
        "telegram_api": TELEGRAM_API[:60] + "..." if BOT_TOKEN else "EMPTY"
    }