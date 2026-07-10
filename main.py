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
    "hyundai": "hdec1234"     
}

ACTIVE_TOKENS = set()

# 💡 [핵심] 청중이 말할 때 어떤 언어로 번역할지 알려주는 '전역 기억 장치'
ROOM_STATE = {
    "targets": "ko,en,zh,id",
    "glossary": ""
}

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

# ==========================================
# 🧠 4. 백그라운드 슬라이딩 요약 봇
# ==========================================
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
        pass

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    token: str = Query(None), 
    lang: str = Query("ko"), 
    targets: str = Query("ko,id"), 
    role: str = Query("speaker"),
    client_id: str = Query(None),
    name: str = Query(None),
    endpointing: int = Query(700), 
    max_chars: int = Query(50)    
):
    if token not in ACTIVE_TOKENS:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if not client_id: client_id = secrets.token_hex(4)
    if not name: name = f"User_{client_id}"

    await manager.connect(websocket, client_id, name, role)
    
    recent_history = [] 
    summary_state = {"text": ""} 
    glossary_text = ""
    dynamic_targets = targets 

    try:
        if role == "viewer":
            while True:
                data = await websocket.receive()
                if data.get("type") == "websocket.receive" and "text" in data:
                    try:
                        msg = json.loads(data["text"])
                        if msg.get("type") == "request_speak":
                            manager.requests.append({"id": client_id, "name": name})
                            await manager.broadcast_admin_state()
                        elif msg.get("type") == "cancel_request":
                            manager.requests = [r for r in manager.requests if r["id"] != client_id]
                            await manager.broadcast_admin_state()
                    except: pass
        else:
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

            ws_kwargs = {}
            if int(websockets.__version__.split('.')[0]) >= 14:
                ws_kwargs["additional_headers"] = headers
            else:
                ws_kwargs["extra_headers"] = headers

            async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
                async def sender():
                    nonlocal glossary_text, dynamic_targets 
                    try:
                        while True:
                            data = await websocket.receive()
                            if data.get("type") == "websocket.receive":
                                if "bytes" in data:
                                    # 발언권 검증: 어드민이거나, 전체음소거가 아니고(바닥권이 없거나 내것일때)
                                    if role == "admin" or (not manager.is_admin_muted and (manager.floor_owner is None or manager.floor_owner == client_id)):
                                        await dg_ws.send(data["bytes"])
                                elif "text" in data:
                                    try:
                                        msg = json.loads(data["text"])
                                        # 사회자 제어 처리
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
                                            if "glossary" in msg: glossary_text = msg.get("glossary", "")
                                            if "targets" in msg: dynamic_targets = msg.get("targets", dynamic_targets)
                                    except: pass
                    except: pass

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
                                    # 바닥권(Floor) 선점 로직
                                    if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":
                                        manager.set_floor(client_id)
                                    
                                    # 남이 발언권 획득 시 내 인식결과 무시
                                    if role != "admin" and manager.floor_owner is None:
                                        continue # 혹시 모를 버그 방어
                                    if role != "admin" and manager.floor_owner != client_id:
                                        continue

                                if transcript or current_sentence:
                                    display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                    current_targets_list = dynamic_targets.split(',') if isinstance(dynamic_targets, str) else dynamic_targets
                                    
                                    # 누가 말하는지 태그 표시
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
                                        
                                        asyncio.create_task(translate_and_send(final_text, lang, dynamic_targets, recent_history, summary_state, glossary_text, current_msg_id, role, name))
                                    
                                    current_sentence = ""
                                    current_msg_id = secrets.token_hex(4)
                    except: pass
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓 에러: {e}", flush=True)
    finally:
        manager.disconnect(websocket)

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):
    
    if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
        await manager.broadcast_json({"type": "alert"})

    ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어"]
    if not text or len(text) < 2 or text.lower() in ignore_words:
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
        manager.release_floor() # 무의미한 발화 시 바닥권 즉시 반납
        return

    await manager.connect(websocket)
    
    recent_history = [] 
    summary_state = {"text": ""} 

    try:
        if role == "viewer":
            # 뷰어는 단방향 수신만 하므로 루프 대기 (무전기 버튼을 누르면 새 'speaker' 웹소켓이 생성됨)
            while True:
                data = await websocket.receive()
                # 뷰어 소켓을 통해 방장의 config(체크박스 변경)가 들어올 경우 무시
        else:
            # 토론자/방장 (speaker) 접속 처리
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
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
                            message = await websocket.receive()
                            if message.get("type") == "websocket.receive":
                                if message.get("bytes"):
                                    await dg_ws.send(message.get("bytes"))
                                elif message.get("text"):
                                    try:
                                        config = json.loads(message.get("text"))
                                        # 💡 방장이 체크박스/용어집을 바꿀 때마다 글로벌 룸 상태(ROOM_STATE) 업데이트
                                        if config.get("type") == "config":
                                            if "glossary" in config:
                                                ROOM_STATE["glossary"] = config.get("glossary", "")
                                            if "targets" in config:
                                                ROOM_STATE["targets"] = config.get("targets", ROOM_STATE["targets"])
                                    except: pass
                    except: pass

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
                                
                                if transcript or current_sentence:
                                    display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                    
                                    # 실시간 화면 표시 대상 (방장이 정해놓은 타겟 언어들로 동기화)
                                    current_targets_list = ROOM_STATE["targets"].split(',')
                                    
                                    await manager.broadcast_json({
                                        "type": "interim", 
                                        "text": display_text.strip(),
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
                                        
                                        # 💡 청중이 '무전기'로 말하더라도, 무조건 방장이 세팅한 'ROOM_STATE' 타겟 언어들로 번역 지시
                                        asyncio.create_task(translate_and_send(
                                            final_text, 
                                            lang, 
                                            ROOM_STATE["targets"], 
                                            recent_history, 
                                            summary_state, 
                                            ROOM_STATE["glossary"], 
                                            current_msg_id
                                        ))
                                    
                                    current_sentence = ""
                                    current_msg_id = secrets.token_hex(4)
                    except: pass
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 에러 발생: {e}")

# ==========================================
# 🧠 6. 메인 LLM 번역 로직
# ==========================================
async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str):
    
    if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
        await manager.broadcast_json({"type": "alert"})

    ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어"]
    if not text or len(text) < 2 or text.lower() in ignore_words:
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
        return

    history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"
    glossary_section = f"\n[MEETING GLOSSARY / DOMAIN KNOWLEDGE]\n{glossary_text}\n" if glossary_text.strip() else ""

    if source_lang == "multi":
        lang_instruction = "The speaker might use various languages during the discussion (e.g., Korean, English, Indonesian, etc.). Detect the spoken language of the CURRENT SENTENCE automatically."
    else:
        lang_instruction = f"The spoken language is strictly '{source_lang}'."

    system_prompt = [
        {
            "type": "text",
            "text": f"""You are a top-tier professional simultaneous interpreter for a multinational meeting.
    
    [PAST CONTEXT SUMMARY]
    {summary_state['text'] if summary_state['text'] else "No summary yet."}
    
    [RECENT CONTEXT]
    {history_str}
    
    {glossary_section}
    
    CRITICAL INSTRUCTIONS:
    1. {lang_instruction}
    2. Fix any STT typos in the CURRENT SENTENCE based on the context.
    3. Translate ONLY the CURRENT SENTENCE into the target language codes: {targets}.
    4. ABSOLUTE RULE: You MUST provide EXACTLY ONE best translation per language. 
       - NEVER use slashes (/) or parentheses to provide alternative options.
       - Pick ONLY ONE natural translation and output it.
    
    Respond EXACTLY in this tag format (DO NOT USE JSON):
    [original]
    clean current sentence
    [lang_code_1]
    result
    [lang_code_2]
    result""",
            "cache_control": {"type": "ephemeral"}
        }
    ]
    
    try:
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
                
                matches = re.finditer(r'\[([a-z]+)\]\s*(.*?)(?=\[|$)', buffer, re.DOTALL)
                for match in matches:
                    lang = match.group(1)
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
                # 번역본에도 화자 이름 태그 달기
                display_final = f"[{'사회자' if role == 'admin' else name}] {final_text}"
                await manager.broadcast_json({
                    "type": "stream_end",
                    "lang": lang,
                    "text": display_final,
                    "original_text": lang_text.get('original', ''),
                    "source_lang": source_lang,
                    "msg_id": msg_id
                })
                
        await manager.broadcast_json({"type": "sentence_complete"})
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
        
        # 완벽한 번역 전송 완료 후 바닥권(Floor) 반납
        manager.release_floor()
        
    except Exception as e:
        print(f"Translation Error: {e}", flush=True)
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
        manager.release_floor()

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    print("🚀 실시간 글로벌 통역 서버를 시작합니다... (http://0.0.0.0:10000)")
    uvicorn.run(app, host="0.0.0.0", port=10000)
