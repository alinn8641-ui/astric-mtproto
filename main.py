import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient

API_ID = int(os.getenv("TG_API_ID", "38362745"))
API_HASH = os.getenv("TG_API_HASH", "8244e49d9e8758f6dfd8c64dadfab87d")

app = FastAPI()
clients = {}

class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    phone: str
    phone_code_hash: str
    code: str

@app.get("/")
def read_root():
    return {"status": "ok", "message": "MTProto Backend is running"}

@app.post("/send-code")
async def send_code(req: PhoneRequest):
    try:
        client = TelegramClient(f"session_{req.phone}", API_ID, API_HASH)
        await client.connect()
        res = await client.send_code_request(req.phone)
        clients[req.phone] = client
        return {"phone_code_hash": res.phone_code_hash}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/sign-in")
async def sign_in(req: CodeRequest):
    client = clients.get(req.phone)
    if not client:
        client = TelegramClient(f"session_{req.phone}", API_ID, API_HASH)
        await client.connect()
    try:
        await client.sign_in(req.phone, req.code, phone_code_hash=req.phone_code_hash)
        session_str = client.session.save()
        await client.disconnect()
        return {"status": "success", "session": session_str}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
