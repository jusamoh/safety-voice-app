import asyncio
import json
import os
import re  
import secrets 
from fastapi import FastAPI, WebSocket, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import websockets
from anthropic import AsyncAnthropic

# ==========================================
# 1. API 키 로드 및 클라이언트 초기화
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

USER_DB = {
    "admin": "1234",          
    "samsung": "sam1234",     
    "hyundai": "hdec1234"
}
ACTIVE_TOKENS = set()

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        user_id = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()
        
        if user_id in USER_DB and USER_DB[user_id] == password:
            token = secrets.token_hex(16)
            ACTIVE_TOKENS.add(token)
            return JSONResponse(content={"success": True, "token": token, "username": user_id})
        return JSONResponse(content={"success": False, "message": "인증 실패"}, status_code=401)
    except Exception as e:
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=400)

@app.get("/")
async def get():
    return FileResponse("index.html")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.clients = {} 
        self.requests = [] 
        self.floor_owner = None
        self.is_admin_muted = False
        self.global_targets = "ko" 
        self.global_glossary = ""
        # 동적 발언권을 얻은 청취자 명단 (새로고침 없이 실시간 제어)
        self.speaking_allowed_clients = set()

    async def connect(self, websocket: WebSocket, client_id: str, name: str, role: str, lang: str):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.clients[websocket] = {"id": client_id, "name": name, "role": role, "lang": lang}
        await self.broadcast_admin_state()
        await self.broadcast_participant_list() # 접속 시 현황판 실시간 업데이트
        await websocket.send_json({
            "type": "floor_state",
            "floor_owner": self.floor_owner,
            "is_admin_muted": self.is_admin_muted
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        
        client_info = self.clients.get(websocket)
        if client_info:
            client_id = client_info["id"]
            self.requests = [req for req in self.requests if req["id"] != client_id]
            if client_id in self.speaking_allowed_clients:
                self.speaking_allowed_clients.remove(client_id)
            if self.floor_owner == client_id:
                self.floor_owner = None
            del self.clients[websocket]
            
        asyncio.create_task(self.broadcast_admin_state())
        asyncio.create_task(self.broadcast_participant_list()) # 퇴장 시 현황판 실시간 업데이트
        asyncio.create_task(self.broadcast_floor_state())

    async def broadcast_admin_state(self):
        state = {"type": "admin_state", "requests": self.requests}
        for ws, info in self.clients.items():
            if info["role"] == "admin":
                try: await ws.send_json(state)
                except: pass

    async def broadcast_participant_list(self):
        # 언어별 접속자 현황 그룹화 (관리자 대시보드용)
        participants = {}
        for ws, info in self.clients.items():
            if info["role"] == "admin": continue
            l = info["lang"]
            if l not in participants: participants[l] = []
            
            # 발언권이 있는 접속자 판별
            is_speaker = info["role"] == "speaker" or info["id"] in self.speaking_allowed_clients
            participants[l].append({
                "id": info["id"],
                "name": info["name"],
                "is_speaker": is_speaker
            })
            
        msg = {"type": "participant_list", "data": participants}
        for ws, info in self.clients.items():
            if info["role"] == "admin":
                try: await ws.send_json(msg)
                except: pass

    async def broadcast_floor_state(self):
        msg = {"type": "floor_state", "floor_owner": self.floor_owner, "is_admin_muted": self.is_admin_muted}
        await self.broadcast_json(msg)

    async def broadcast_json(self, message: dict):
        for connection in self.active_connections:
            try: await connection.send_json(message)
            except: pass
            
    def set_floor(self, client_id: str):
        self.floor_owner = client_id
        asyncio.create_task(self.broadcast_floor_state())
        
    def release_floor(self):
        self.floor_owner = None
        asyncio.create_task(self.broadcast_floor_state())

manager = ConnectionManager()

async def update_sliding_summary(summary_state: dict, new_sentences: list):
    current_summary = summary_state.get("text", "")
    new_text = "\n".join(new_sentences)
    
    prompt = f"""You are a context summarizer for a multinational meeting/discussion.
    Update the existing summary with the new sentences. Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context: who, where, what, and specific risks or items mentioned.
    [Existing Summary]\n{current_summary if current_summary else "None"}
    [New Sentences]\n{new_text}
    Respond ONLY with the newly updated summary string in Korean."""
    
    try:
        response = await claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        summary_state["text"] = response.content[0].text.strip()
    except: pass

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):
    try:
        if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
            await manager.broadcast_json({"type": "alert"})

        if not text or len(text) < 2 or text.lower() in ["you", "thank you", "o", "hmm", "uh", "아", "음", "어"]: return 

        history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음"
        glossary_section = f"\n[GLOSSARY]\n{glossary_text}\n" if glossary_text.strip() else ""
        lang_instruction = "Detect the spoken language of the CURRENT SENTENCE automatically." if source_lang == "multi" else f"The spoken language is strictly '{source_lang}'."

        system_prompt = f"""You are a professional simultaneous interpreter machine.
[PAST SUMMARY]\n{summary_state.get('text', 'No summary yet.')}
[RECENT CONTEXT]\n{history_str}\n{glossary_section}
INSTRUCTIONS:
1. {lang_instruction}
2. Translate ONLY the CURRENT SENTENCE into: {targets}. Provide ONE best translation per language.
3. DO NOT converse. Remove filler words.
4. If danger is implied, use IMPERATIVE tone.
Respond EXACTLY in this tag format:
[original]
clean current sentence
"""
        for t in targets.split(','): system_prompt += f"[{t.strip()}]\nresult\n"

        stream = await claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            system=system_prompt, 
            messages=[{"role": "user", "content": text}],
            stream=True
        )

        buffer = ""
        lang_text = {}
        async for event in stream:
            if event.type == "content_block_delta":
                buffer += event.delta.text
                matches = re.finditer(r'\[([a-zA-Z-]+)\]\s*(.*?)(?=\[|$)', buffer, re.DOTALL)
                for match in matches:
                    l = match.group(1).lower().strip()
                    text_so_far = match.group(2).strip()
                    lang_text[l] = text_so_far
                    if l != 'original':
                        await manager.broadcast_json({
                            "type": "stream_update", "lang": l, "text": text_so_far,
                            "original_text": lang_text.get('original', ''), "source_lang": source_lang, "msg_id": msg_id
                        })
        
        original_text = lang_text.get('original', text)
        recent_history.append(original_text)
        if len(recent_history) >= 5:
            asyncio.create_task(update_sliding_summary(summary_state, recent_history[:3]))
            del recent_history[:3] 
        
        for l, final_text in lang_text.items():
            if l != 'original':
                await manager.broadcast_json({
                    "type": "stream_end", "lang": l, "text": f"[{name}] {final_text}",
                    "original_text": lang_text.get('original', ''), "source_lang": source_lang, "msg_id": msg_id
                })
    except:
        await manager.broadcast_json({"type": "status", "text": "❌ 번역 실패"})
    finally:
        await manager.broadcast_json({"type": "sentence_complete"})
        manager.release_floor()
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, token: str = Query(None), lang: str = Query("ko"), 
    targets: str = Query("ko,id"), role: str = Query("viewer"),
    client_id: str = Query(None), name: str = Query(None),
    endpointing: int = Query(500), max_chars: int = Query(35), glossary: str = Query("")
):
    if token not in ACTIVE_TOKENS:
        await websocket.close(code=1008)
        return

    if not client_id: client_id = secrets.token_hex(4)
    if not name: name = f"User_{client_id}"

    await manager.connect(websocket, client_id, name, role, lang)
    recent_history = [] 
    summary_state = {"text": ""} 

    try:
        dg_lang = "ko" if lang == "multi" else lang
        keywords_param = ""
        if glossary:
            clean_words = [w.strip() for w in re.findall(r'^([^=:-]+)', glossary, re.MULTILINE) if w.strip()]
            if clean_words: keywords_param = "&" + "&".join([f"keywords={w}" for w in clean_words])

        dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={dg_lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true{keywords_param}"
        
        ws_kwargs = {"additional_headers": {"Authorization": f"Token {DEEPGRAM_API_KEY}"}} if int(websockets.__version__.split('.')[0]) >= 14 else {"extra_headers": {"Authorization": f"Token {DEEPGRAM_API_KEY}"}}

        async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
            async def sender():
                try:
                    while True:
                        data = await websocket.receive()
                        if data.get("bytes") is not None:
                            # 🚨 마이크 전송 권한 판별: 어드민이거나, 상시 토론자거나, [동적 발언권을 부여받은 청취자] 일 때 허용
                            is_allowed = role == "admin" or role == "speaker" or client_id in manager.speaking_allowed_clients
                            if is_allowed and (not manager.is_admin_muted and (manager.floor_owner is None or manager.floor_owner == client_id)):
                                await dg_ws.send(data.get("bytes"))
                                
                        elif data.get("text") is not None:
                            try:
                                msg = json.loads(data.get("text"))
                                
                                # 일반 청취자의 발언 요청
                                if msg.get("type") == "request_speak":
                                    manager.requests.append({"id": client_id, "name": name})
                                    await manager.broadcast_admin_state()
                                elif msg.get("type") == "cancel_request":
                                    manager.requests = [r for r in manager.requests if r["id"] != client_id]
                                    await manager.broadcast_admin_state()
                                
                                # 관리자의 제어 액션 (동적 권한 부여/회수)
                                elif msg.get("type") == "admin_action" and role == "admin":
                                    action = msg.get("action")
                                    tid = msg.get("target_id")
                                    
                                    if action == "approve":
                                        manager.requests = [r for r in manager.requests if r["id"] != tid]
                                        manager.speaking_allowed_clients.add(tid)
                                        await manager.broadcast_admin_state()
                                        await manager.broadcast_participant_list()
                                        for ws, info in manager.clients.items():
                                            if info["id"] == tid:
                                                try: await ws.send_json({"type": "speak_approved"})
                                                except: pass
                                                
                                    elif action == "revoke":
                                        if tid in manager.speaking_allowed_clients:
                                            manager.speaking_allowed_clients.remove(tid)
                                        await manager.broadcast_participant_list()
                                        for ws, info in manager.clients.items():
                                            if info["id"] == tid:
                                                try: await ws.send_json({"type": "speak_revoked"})
                                                except: pass
                                                
                                    elif action == "reject":
                                        manager.requests = [r for r in manager.requests if r["id"] != tid]
                                        await manager.broadcast_admin_state()
                                        
                                    elif action == "mute_all":
                                        manager.is_admin_muted = True
                                        manager.release_floor()
                                    elif action == "unmute_all":
                                        manager.is_admin_muted = False
                                        await manager.broadcast_floor_state()
                                        
                                elif msg.get("type") == "config" and role == "admin":
                                    if "glossary" in msg: manager.global_glossary = msg.get("glossary", "")
                                    if "targets" in msg: manager.global_targets = msg.get("targets", manager.global_targets)
                            except: pass
                except: pass

            async def receiver():
                current_sentence = ""
                last_text = "" 
                msg_id = secrets.token_hex(4)
                try:
                    while True:
                        dg_result = await dg_ws.recv()
                        dg_json = json.loads(dg_result)
                        if dg_json.get("type") == "Results":
                            transcript = dg_json.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()
                            
                            is_allowed = role == "admin" or role == "speaker" or client_id in manager.speaking_allowed_clients
                            if transcript and is_allowed:
                                if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":
                                    manager.set_floor(client_id)
                                if role != "admin" and manager.floor_owner != client_id: continue

                            if transcript or current_sentence:
                                display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                await manager.broadcast_json({
                                    "type": "interim", "text": f"[{name}] " + display_text.strip(),
                                    "targets": manager.global_targets.split(','), "msg_id": msg_id
                                })

                            if dg_json.get("is_final", False) and transcript:
                                current_sentence = current_sentence + " " + transcript if current_sentence else transcript

                            if (dg_json.get("speech_final", False) or len(current_sentence) > max_chars or current_sentence.strip().endswith(('.', '?', '!'))) and current_sentence.strip():
                                if current_sentence.strip() != last_text:
                                    last_text = current_sentence.strip()
                                    await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                    asyncio.create_task(translate_and_send(last_text, lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, msg_id, role, name))
                                current_sentence = ""
                                msg_id = secrets.token_hex(4)
                except: pass

            await asyncio.gather(sender(), receiver())
    except: pass
    finally: manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)