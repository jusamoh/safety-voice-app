import asyncio
import json
import os
import sys
import re  
import secrets 
from datetime import datetime
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from anthropic import AsyncAnthropic

# ==========================================
# 💡 1. API 키 로드
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

# ==========================================
# 💰 2. 인증 & 글로벌 룸 세팅 (Room State)
# ==========================================
USER_DB = {
    "admin": "1234",          
    "samsung": "sam1234",     
    "hyundai": "hdec1234",
    "speaker": "speaker1234"     
}

ACTIVE_TOKENS = set()

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        user_id = str(data.get("username", data.get("id", ""))).strip()
        password = str(data.get("password", "")).strip()
        
        if user_id in USER_DB and USER_DB[user_id] == password:
            token = secrets.token_hex(16)
            ACTIVE_TOKENS.add(token)
            print(f"🔐 [인증 성공] 사용자 '{user_id}' 로그인 (토큰 발급됨)", flush=True)
            return JSONResponse(content={"success": True, "token": token, "username": user_id})
        else:
            return JSONResponse(content={"success": False, "message": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)
    except Exception as e:
        return JSONResponse(content={"success": False, "message": f"로그인 에러: {str(e)}"}, status_code=400)

@app.get("/")
async def get():
    return FileResponse("index.html")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi import Response
    return Response(status_code=204)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.clients = {} 
        self.requests = [] 
        self.floor_owner = None
        self.is_admin_muted = False
        # 🚨 [수정 1] 방 전체 공용(Global) 수첩 생성: 소장님의 설정이 방 전체를 통제합니다.
        self.global_targets = "ko" 
        self.global_glossary = ""

    async def connect(self, websocket: WebSocket, client_id: str, name: str, role: str):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.clients[websocket] = {"id": client_id, "name": name, "role": role}
        await self.broadcast_admin_state()
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
            if self.floor_owner == client_id:
                self.floor_owner = None
            del self.clients[websocket]
            
        asyncio.create_task(self.broadcast_admin_state())
        asyncio.create_task(self.broadcast_floor_state())

    async def broadcast_admin_state(self):
        state = {"type": "admin_state", "requests": self.requests}
        for ws, info in self.clients.items():
            if info["role"] == "admin":
                try: await ws.send_json(state)
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
    Update the existing summary with the new sentences.
    Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context: who, where, what, and specific risks or items mentioned.
    
    [Existing Summary]
    {current_summary if current_summary else "None"}
    
    [New Sentences]
    {new_text}
    
    Respond ONLY with the newly updated summary string in Korean."""
    
    try:
        response = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        summary_state["text"] = response.content[0].text.strip()
        print(f"\n🧠 [비서 요약 봇] 문맥 압축 완료: {summary_state['text']}\n", flush=True)
    except Exception as e:
        print(f"Summary Error: {e}", flush=True)

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):
    try:
        if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
            await manager.broadcast_json({"type": "alert"})

        ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어"]
        if not text or len(text) < 2 or text.lower() in ignore_words:
            return 

        history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"
        glossary_section = f"\n[MEETING GLOSSARY / DOMAIN KNOWLEDGE]\n{glossary_text}\n" if glossary_text.strip() else ""

        if source_lang == "multi":
            lang_instruction = "Detect the spoken language of the CURRENT SENTENCE automatically."
        else:
            lang_instruction = f"The spoken language is strictly '{source_lang}'."

        # 🚨 [수정 2] AI 군기 잡기: 환각(Chatbot) 방지 강력한 프롬프트 추가 (5번 지시사항)
        system_prompt = f"""You are a professional simultaneous interpreter.
[PAST CONTEXT SUMMARY]
{summary_state.get('text', 'No summary yet.')}

[RECENT CONTEXT]
{history_str}
{glossary_section}

CRITICAL INSTRUCTIONS:
1. {lang_instruction}
2. Fix STT typos based on the context.
3. Translate ONLY the CURRENT SENTENCE into the exact language codes: {targets}.
4. Provide EXACTLY ONE best translation per language.
5. CRITICAL: If the input is incomplete, fragmented, or a complete STT error, DO NOT explain it, DO NOT converse, and DO NOT apologize. Just translate it literally or output it as-is. NEVER output conversational responses.

Respond EXACTLY in this tag format (DO NOT USE JSON):
[original]
clean current sentence
"""
        for t in targets.split(','):
            system_prompt += f"[{t.strip()}]\nresult\n"

        stream = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
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
                    lang = match.group(1).lower().strip()
                    text_so_far = match.group(2).strip()
                    
                    lang_text[lang] = text_so_far
                    
                    if lang != 'original':
                        await manager.broadcast_json({
                            "type": "stream_update",
                            "lang": lang,
                            "text": text_so_far,
                            "original_text": lang_text.get('original', ''),
                            "source_lang": source_lang,
                            "msg_id": msg_id
                        })
        
        original_text = lang_text.get('original', text)
        recent_history.append(original_text)
        
        if len(recent_history) >= 5:
            sentences_to_summarize = recent_history[:3]
            del recent_history[:3] 
            asyncio.create_task(update_sliding_summary(summary_state, sentences_to_summarize))
        
        for lang, final_text in lang_text.items():
            if lang != 'original':
                display_final = f"[{'사회자' if role == 'admin' else name}] {final_text}"
                await manager.broadcast_json({
                    "type": "stream_end",
                    "lang": lang,
                    "text": display_final,
                    "original_text": lang_text.get('original', ''),
                    "source_lang": source_lang,
                    "msg_id": msg_id
                })
                
    except Exception as e:
        print(f"❌ [번역 에러 발생]: {e}", flush=True)
        await manager.broadcast_json({"type": "status", "text": "❌ 번역 실패 (재시도 중)"})
    
    finally:
        await manager.broadcast_json({"type": "sentence_complete"})
        manager.release_floor()
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    token: str = Query(None), 
    lang: str = Query("ko"), 
    targets: str = Query("ko,id"), 
    role: str = Query("speaker"),
    client_id: str = Query(None),
    name: str = Query(None),
    endpointing: int = Query(500), 
    max_chars: int = Query(35)    
):
    if token not in ACTIVE_TOKENS:
        print(f"❌ [보안 차단] 유효하지 않은 토큰. (IP: {websocket.client})", flush=True)
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if not client_id: client_id = secrets.token_hex(4)
    if not name: name = f"User_{client_id}"

    # 소장님(admin)이 처음 접속할 때 기본 타겟을 초기화합니다.
    if role == "admin":
        manager.global_targets = targets

    await manager.connect(websocket, client_id, name, role)
    
    recent_history = [] 
    summary_state = {"text": ""} 

    try:
        if role == "viewer":
            while True:
                data = await websocket.receive()
                if data.get("type") == "websocket.receive" and data.get("text") is not None:
                    try:
                        msg = json.loads(data.get("text"))
                        if msg.get("type") == "request_speak":
                            manager.requests.append({"id": client_id, "name": name})
                            await manager.broadcast_admin_state()
                        elif msg.get("type") == "cancel_request":
                            manager.requests = [r for r in manager.requests if r["id"] != client_id]
                            await manager.broadcast_admin_state()
                    except: pass
        else:
            dg_lang = "ko" if lang == "multi" else lang
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={dg_lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

            ws_kwargs = {}
            if int(websockets.__version__.split('.')[0]) >= 14:
                ws_kwargs["additional_headers"] = headers
            else:
                ws_kwargs["extra_headers"] = headers

            async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
                
                async def sender():
                    try:
                        while True:
                            data = await websocket.receive()
                            if data.get("type") == "websocket.receive":
                                if data.get("bytes") is not None:
                                    if role == "admin" or (not manager.is_admin_muted and (manager.floor_owner is None or manager.floor_owner == client_id)):
                                        await dg_ws.send(data.get("bytes"))
                                        
                                elif data.get("text") is not None:
                                    try:
                                        msg = json.loads(data.get("text"))
                                        if msg.get("type") == "admin_action" and role == "admin":
                                            action = msg.get("action")
                                            if action == "approve":
                                                tid = msg.get("target_id")
                                                manager.requests = [r for r in manager.requests if r["id"] != tid]
                                                await manager.broadcast_admin_state()
                                                for ws, info in manager.clients.items():
                                                    if info["id"] == tid:
                                                        try: await ws.send_json({"type": "speak_approved"})
                                                        except: pass
                                            elif action == "reject":
                                                tid = msg.get("target_id")
                                                manager.requests = [r for r in manager.requests if r["id"] != tid]
                                                await manager.broadcast_admin_state()
                                            elif action == "mute_all":
                                                manager.is_admin_muted = True
                                                manager.release_floor()
                                            elif action == "unmute_all":
                                                manager.is_admin_muted = False
                                                await manager.broadcast_floor_state()
                                        elif msg.get("type") == "config":
                                            # 🚨 [수정 3] Admin의 설정을 '공용 수첩(Global)'에 저장합니다.
                                            if role == "admin":
                                                if "glossary" in msg: manager.global_glossary = msg.get("glossary", "")
                                                if "targets" in msg: manager.global_targets = msg.get("targets", manager.global_targets)
                                    except: pass
                    except websockets.exceptions.ConnectionClosed: pass
                    except Exception as e: print(f"🚨 Sender 루프 에러: {e}", flush=True)

                async def receiver():
                    current_sentence = ""
                    last_translated_text = "" 
                    current_msg_id = secrets.token_hex(4)
                    
                    try:
                        while True:
                            dg_result = await dg_ws.recv()
                            dg_json = json.loads(dg_result)
                            
                            if dg_json.get("type") == "Results":
                                is_final = dg_json.get("is_final", False)
                                speech_final = dg_json.get("speech_final", False)
                                transcript = dg_json.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()
                                
                                if transcript:
                                    if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":
                                        manager.set_floor(client_id)
                                    
                                    if role != "admin" and manager.floor_owner is None:
                                        continue 
                                    if role != "admin" and manager.floor_owner != client_id:
                                        continue

                                if transcript or current_sentence:
                                    display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                    
                                    # 🚨 [수정 4] 토론자의 개인 설정이 아닌, 방장(Admin)의 '공용 수첩(Global)'을 가져와 방송합니다.
                                    current_targets_list = manager.global_targets.split(',')
                                    
                                    tag = "[사회자] " if role == "admin" else f"[{name}] "
                                    
                                    await manager.broadcast_json({
                                        "type": "interim", 
                                        "text": tag + display_text.strip(),
                                        "targets": current_targets_list,
                                        "msg_id": current_msg_id
                                    })

                                if is_final and transcript:
                                    if current_sentence: current_sentence += " " + transcript
                                    else: current_sentence = transcript

                                is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                                if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                    final_text = current_sentence.strip()
                                    
                                    if final_text != last_translated_text:
                                        last_translated_text = final_text
                                        await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                        
                                        # 🚨 [수정 5] 번역 지시 시에도 Admin의 '공용 타겟 언어'와 '공용 용어집'을 강제 적용합니다.
                                        asyncio.create_task(translate_and_send(final_text, lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))
                                    
                                    current_sentence = ""
                                    current_msg_id = secrets.token_hex(4)
                    except websockets.exceptions.ConnectionClosed: pass
                    except Exception as e: print(f"🚨 Receiver 루프 에러: {e}", flush=True)

                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 전체 웹소켓 연결 에러: {e}", flush=True)
    finally:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    print("🚀 실시간 글로벌 통역 서버를 시작합니다... (http://0.0.0.0:10000)")
    uvicorn.run(app, host="0.0.0.0", port=10000)