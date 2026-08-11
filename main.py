"""
Astric Sender · MTProto backend (FastAPI + Telethon), deploy on Render.
api_id / api_hash / MT_SECRET уже вписаны по умолчанию — ничего не редактируй.
"""
import asyncio, os, sqlite3, uuid
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, FloodWaitError,
)

API_ID   = int(os.getenv("TG_API_ID", "38362745"))
API_HASH = os.getenv("TG_API_HASH", "8244e49d9e8758f6dfd8c64dadfab87d")
MT_SECRET  = os.getenv("MT_SECRET", "change-me-strong-secret")
DB_FILE = os.getenv("SESSIONS_DB", "sessions.db")

app = FastAPI(title="Astric Sender MTProto")

# CORS — разрешаем запросы с любых доменов (Mini App в Telegram WebView и т.д.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def require_secret(x_mt_secret: str = Header(default="")):
    if x_mt_secret != MT_SECRET:
        raise HTTPException(401, "bad secret")

def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE)
    c.execute("CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, session_string TEXT)")
    return c

def save_session(phone: str, ss: str):
    c = db(); c.execute("INSERT OR REPLACE INTO sessions (phone, session_string) VALUES (?,?)", (phone, ss)); c.commit(); c.close()

def get_client(session_string: Optional[str] = None) -> TelegramClient:
    if session_string:
        return TelegramClient(StringSession(session_string), API_ID, API_HASH)
    return TelegramClient(StringSession(), API_ID, API_HASH)

class SendCodeBody(BaseModel):
    phone: str
class VerifyBody(BaseModel):
    phone: str
    code: str
    password: Optional[str] = ""
class BroadcastBody(BaseModel):
    session_string: str
    targets: List[str]
    message: str
    delay_sec: int = 10

@app.get("/")
def root():
    return {"ok": True, "service": "astric-mtproto", "configured": bool(API_ID and API_HASH)}

@app.post("/api/auth/send-code")
async def send_code(body: SendCodeBody, x_mt_secret: str = Header(default="")):
    require_secret(x_mt_secret)
    phone = body.phone.strip()
    if not phone:
        return {"success": False, "error": "phone required"}
    client = get_client()
    try:
        await client.connect()
        r = await client.send_code_request(phone)
        return {"success": True, "message": "Code sent", "phone_code_hash": getattr(r, "phone_code_hash", "") or ""}
    except FloodWaitError as e:
        return {"success": False, "error": f"FLOOD_WAIT: подождите {e.seconds} сек"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        await client.disconnect()

@app.post("/api/auth/verify")
async def verify(body: VerifyBody, x_mt_secret: str = Header(default="")):
    require_secret(x_mt_secret)
    client = get_client()
    try:
        await client.connect()
        try:
            await client.sign_in(body.phone.strip(), body.code)
        except SessionPasswordNeededError:
            if not body.password:
                return {"success": False, "error": "needs_password"}
            try:
                await client.sign_in(password=body.password)
            except Exception:
                return {"success": False, "error": "Неверный пароль 2FA"}
        except PhoneCodeInvalidError:
            return {"success": False, "error": "Неверный код"}
        except PhoneCodeExpiredError:
            return {"success": False, "error": "Код истёк, запросите новый"}
        except FloodWaitError as e:
            return {"success": False, "error": f"FLOOD_WAIT: подождите {e.seconds} сек"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        ss = StringSession.save(client.session)
        save_session(body.phone.strip(), ss)
        return {"success": True, "session_ref": ss, "phone": body.phone, "message": "Аккаунт привязан"}
    finally:
        await client.disconnect()

TASKS = {}

async def run_broadcast(task_id: str, session_string: str, targets: List[str], message: str, delay_sec: int):
    client = get_client(session_string)
    await client.connect()
    sent, failed = 0, []
    try:
        for t in targets:
            t = t.strip()
            if not t:
                continue
            try:
                await client.send_message(t, message); sent += 1
            except FloodWaitError as e:
                TASKS[task_id]["error"] = f"FLOOD_WAIT подождите {e.seconds} сек"; break
            except Exception:
                failed.append(t)
            await asyncio.sleep(delay_sec)
    except Exception as e:
        TASKS[task_id]["error"] = str(e)
    finally:
        await client.disconnect()
        TASKS[task_id].update(status="done", sent=sent, total=len(targets), failed=failed)

@app.post("/api/broadcast")
async def broadcast(body: BroadcastBody, background: BackgroundTasks, x_mt_secret: str = Header(default="")):
    require_secret(x_mt_secret)
    if not body.session_string or not body.targets or not body.message:
        return {"success": False, "error": "session_string, targets и message обязательны"}
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"status": "queued", "sent": 0, "total": len(body.targets), "error": None}
    background.add_task(run_broadcast, task_id, body.session_string, body.targets, body.message, body.delay_sec)
    return {"success": True, "task_id": task_id, "message": "Рассылка запущена в фоне"}

@app.get("/api/broadcast/{task_id}")
def broadcast_status(task_id: str, x_mt_secret: str = Header(default="")):
    require_secret(x_mt_secret)
    return {"task_id": task_id, "status": TASKS.get(task_id, {"status": "not_found"})}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
