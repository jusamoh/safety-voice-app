import asyncio
import json
import os
import sys
import re  
import secrets 
import io
from datetime import datetime
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from anthropic import AsyncAnthropic
import azure.cognitiveservices.speech as speechsdk

# 🌟 파일 파싱 라이브러리
import PyPDF2
import docx

# ==========================================
# 💡 1. API 키 로드
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "")

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

class DowngradeException(Exception):
    pass

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.clients = {} 
        self.requests = [] 
        self.floor_owner = None
        self.is_admin_muted = False
        self.global_targets = "ko" 
        self.global_glossary = ""
        self.global_document_context = "" 
        self.speaking_allowed_clients = set()

    async def connect(self, websocket: WebSocket, client_id: str, name: str, role: str, ui_lang: str):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.clients[websocket] = {"id": client_id, "name": name, "role": role, "ui_lang": ui_lang}
        
        await self.broadcast_admin_state()
        await self.broadcast_user_list()
        
        await websocket.send_json({
            "type": "floor_state",
            "floor_owner": self.floor_owner,
            "is_admin_muted": self.is_admin_muted
        })

    async def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        
        client_info = self.clients.get(websocket)
        if client_info:
            client_id = client_info["id"]
            self.requests = [req for req in self.requests if req["id"] != client_id]
            self.speaking_allowed_clients.discard(client_id)
            if self.floor_owner == client_id:
                self.floor_owner = None
            del self.clients[websocket]
            
        await self.broadcast_admin_state()
        await self.broadcast_floor_state()
        await self.broadcast_user_list()

    async def broadcast_user_list(self):
        users = []
        for info in self.clients.values():
            u = info.copy()
            u["is_speaker"] = u["role"] in ["admin", "speaker"] or u["id"] in self.speaking_allowed_clients
            users.append(u)
        msg = {"type": "user_list", "users": users}
        for ws, info in self.clients.items():
            if info["role"] == "admin":
                try: await ws.send_json(msg)
                except: pass

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
# 🌟 추가된 라우터: 다중 포맷 문서 업로드 및 텍스트 파싱
# ==========================================
@app.post("/api/upload_context")
async def upload_context(file: UploadFile = File(...)):
    content = await file.read()
    ext = file.filename.split('.')[-1].lower()
    extracted_text = ""
    
    try:
        if ext in ['txt', 'csv']:
            extracted_text = content.decode('utf-8')
        elif ext == 'pdf':
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
            for page in pdf_reader.pages:
                extracted_text += page.extract_text() + "\n"
        elif ext == 'docx':
            doc = docx.Document(io.BytesIO(content))
            extracted_text = "\n".join([para.text for para in doc.paragraphs])
        else:
            return JSONResponse({"success": False, "message": "지원하지 않는 파일 형식입니다. (pdf, docx, txt 가능)"})
        
        # 클로드 토큰 한도 보호를 위해 최대 50,000자로 제한
        extracted_text = extracted_text[:50000]
        manager.global_document_context = extracted_text
        print(f"📄 [문서 학습 성공] {file.filename} (길이: {len(extracted_text)} 자)", flush=True)
        
        return JSONResponse({"success": True, "message": "문서 파싱 및 AI 학습 준비 완료"})
    except Exception as e:
        print(f"❌ [문서 파싱 오류] {e}", flush=True)
        return JSONResponse({"success": False, "message": f"문서 처리 중 오류 발생: {str(e)}"})

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

async def update_sliding_summary(summary_state: dict, new_sentences: list):
    current_summary = summary_state.get("text", "")
    new_text = "\n".join(new_sentences)
    
    prompt = f"""You are a context summarizer for a multinational civil engineering expert seminar.
    Update the existing summary with the new sentences.
    Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context regarding road paving technologies, asphalt/concrete materials, pavement design, construction equipment, or specific engineering parameters.
    
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
    except Exception as e:
        pass

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):
    try:
        text_lower = text.lower().strip()
        ignore_exact_phrases = [
            "that's the one", "yeah right there", "that's quite a lot", "good thinking",
            "that's the one yeah right there", "yeah that one right there", "that's quite remarkable",
            "thank you", "hmm", "uh", "well", "so", "okay", "아", "음", "hola", "어", "yeah", "right",
            "네", "아니요", "예", "아니"
        ]
        
        clean_text = re.sub(r'[^a-z가-힣\s]', '', text_lower).strip()
        if not clean_text or clean_text in ignore_exact_phrases or (len(clean_text) <= 2 and re.match(r'^[가-힣]+$', clean_text)):
            return 

        history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"
        glossary_section = f"\n[CIVIL ENGINEERING GLOSSARY]\n{glossary_text}\n" if glossary_text.strip() else ""
        doc_section = f"\n[REFERENCE DOCUMENT / PAPER CONTEXT]\n{manager.global_document_context}\n" if manager.global_document_context else ""

        if source_lang == "multi" or source_lang == "multi_azure":
            lang_instruction = "The STT engine detected the language automatically. However, cross-check the context."
        else:
            lang_instruction = f"The spoken language is strictly '{source_lang}'."

        system_prompt = f"""You are an elite simultaneous interpreter for an international civil engineering expert seminar involving Korea, China, Japan, and the US.
Domain focus: Road paving, asphalt/concrete materials, pavement design, compaction, construction equipment, and related civil engineering technologies.

[PAST CONTEXT SUMMARY]
{summary_state.get('text', 'No summary yet.')}

[RECENT CONTEXT]
{history_str}
{glossary_section}
{doc_section}

CRITICAL INSTRUCTIONS (MUST OBEY):
1. {lang_instruction} 
2. [NUMBER & UNIT STRICTNESS]: Convert colloquial numbers/units into Arabic numerals and exact standard symbols (e.g., 50mm, 160°C, m², m³, MPa). NO space between number and unit. Verify values using [REFERENCE DOCUMENT / PAPER CONTEXT] if provided.
3. [DOMAIN FORCED CORRECTION]: The context is 'KICT' (Korea Institute of Civil Engineering and Building Technology). STT input "Payment" MUST be translated as "Pavement" (도로포장).
4. [CHITCHAT & WHISPER REJECTION]: If the STT picked up a meaningless filler noise, a background whisper, an incomplete casual remark, or internal staff chitchat (e.g., "안 하세요?", "박사 가시기 바랍니다", "That's the one", "아니요"), DO NOT translate it. Output exactly [SKIP].
5. Translate ONLY the CURRENT SENTENCE into the exact language codes: {targets}. Utilize the [REFERENCE DOCUMENT / PAPER CONTEXT] to predict omitted subjects, disambiguate homophones, and ensure precise academic terminology.
6. [SINGLE DEFINITIVE TRANSLATION]: Provide EXACTLY ONE best translation per language. DO NOT use slashes (/) to provide multiple options or alternatives.
7. [STRICT FRAGMENT TRANSLATION - NO HALLUCINATION]: This is the most important rule. NEVER complete an unfinished sentence. If the speaker stops mid-sentence or provides an incomplete fragment, you MUST translate EXACTLY up to that point and end with an ellipsis (...). DO NOT infer missing verbs, add conclusions, or guess what the speaker meant to say. Be strictly literal and mechanical.
8. CRITICAL: DO NOT converse with the speaker. Just output the translation.

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
                    
                    if lang == 'skip':
                        continue
                        
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
        
        is_skipped = any("[SKIP]" in t.upper() for t in lang_text.values()) or "[SKIP]" in buffer.upper()
        
        if is_skipped:
            for target_lang in targets.split(','):
                target_lang = target_lang.strip().lower()
                await manager.broadcast_json({
                    "type": "stream_end",
                    "lang": target_lang,
                    "text": f"[{'사회자' if role == 'admin' else name}] 🔇 (기계음/잡음 제외)",
                    "raw_text": "🔇",
                    "original_text": original_text,
                    "source_lang": source_lang,
                    "msg_id": msg_id,
                    "role": role,
                    "name": '사회자' if role == 'admin' else name
                })
            return 

        recent_history.append(original_text)
        if len(recent_history) >= 5:
            sentences_to_summarize = recent_history[:3]
            del recent_history[:3] 
            asyncio.create_task(update_sliding_summary(summary_state, sentences_to_summarize))
        
        for lang, final_text in lang_text.items():
            if lang != 'original':
                if not final_text.strip():
                    continue
                    
                display_final = f"[{'사회자' if role == 'admin' else name}] {final_text}"
                await manager.broadcast_json({
                    "type": "stream_end",
                    "lang": lang,
                    "text": display_final,
                    "raw_text": final_text,
                    "original_text": lang_text.get('original', ''),
                    "source_lang": source_lang,
                    "msg_id": msg_id,
                    "role": role,
                    "name": '사회자' if role == 'admin' else name
                })
                
    except Exception as e:
        print(f"❌ [번역 에러 발생]: {e}", flush=True)
        await manager.broadcast_json({"type": "status", "text": "❌ 번역 실패 (재시도 중)"})
    
    finally:
        await manager.broadcast_json({"type": "sentence_complete", "msg_id": msg_id})
        manager.release_floor()
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    token: str = Query(None), 
    lang: str = Query("ko"), 
    targets: str = Query("ko,en,ja,zh"), 
    role: str = Query("speaker"),
    client_id: str = Query(None),
    name: str = Query(None),
    ui_lang: str = Query("ko"), 
    endpointing: int = Query(500), 
    max_chars: int = Query(35),
    glossary: str = Query("") 
):
    if token not in ACTIVE_TOKENS:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if not client_id: client_id = secrets.token_hex(4)
    if not name: name = f"User_{client_id}"

    await manager.connect(websocket, client_id, name, role, ui_lang)
    recent_history = [] 
    summary_state = {"text": ""} 

    try:
        while True:
            is_speaker = role in ["admin", "speaker"] or client_id in manager.speaking_allowed_clients
            if not is_speaker:
                while True:
                    data = await websocket.receive()
                    if data.get("type") == "websocket.receive" and data.get("text") is not None:
                        try:
                            msg = json.loads(data.get("text"))
                            msg_type = msg.get("type")
                            if msg_type == "request_speak":
                                manager.requests.append({"id": client_id, "name": name})
                                await manager.broadcast_admin_state()
                            elif msg_type == "cancel_request":
                                manager.requests = [r for r in manager.requests if r["id"] != client_id]
                                await manager.broadcast_admin_state()
                            elif msg_type == "upgrade_to_speaker":
                                if client_id in manager.speaking_allowed_clients:
                                    break 
                        except: pass
            else:
                engine_mode = "azure" if lang == "multi_azure" else "deepgram"
                if engine_mode == "azure":
                    if not AZURE_SPEECH_KEY:
                        await websocket.send_json({"type": "status", "text": "❌ 엔진 API Key가 설정되지 않았습니다."})
                        raise Exception("Azure key missing")
                        
                    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
                    compressed_format = speechsdk.audio.AudioStreamFormat(compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY)
                    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=compressed_format)
                    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
                    auto_detect_source_language_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(languages=["ko-KR", "en-US", "ja-JP", "zh-CN"])
                    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, auto_detect_source_language_config=auto_detect_source_language_config, audio_config=audio_config)

                    azure_queue = asyncio.Queue()
                    loop = asyncio.get_running_loop()

                    def recognizing_cb(evt):
                        if evt.result.text:
                            lid_result = evt.result.properties.get(speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult, "unknown")
                            loop.call_soon_threadsafe(azure_queue.put_nowait, {"type": "interim", "text": evt.result.text, "lid": lid_result})

                    def recognized_cb(evt):
                        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
                            lid_result = evt.result.properties.get(speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult, "unknown")
                            loop.call_soon_threadsafe(azure_queue.put_nowait, {"type": "final", "text": evt.result.text, "lid": lid_result})

                    recognizer.recognizing.connect(recognizing_cb)
                    recognizer.recognized.connect(recognized_cb)
                    recognizer.start_continuous_recognition_async()
                    
                    await manager.broadcast_json({"type": "status", "text": "🌐 다국어 자동 식별 모드 가동 중..."})

                    async def sender():
                        try:
                            while True:
                                data = await websocket.receive()
                                if data.get("type") == "websocket.receive":
                                    if data.get("bytes") is not None:
                                        if role == "admin" or (not manager.is_admin_muted and (manager.floor_owner is None or manager.floor_owner == client_id)):
                                            push_stream.write(data.get("bytes"))
                                    elif data.get("text") is not None:
                                        try:
                                            msg = json.loads(data.get("text"))
                                            msg_type = msg.get("type")
                                            if msg_type == "downgrade_to_viewer": raise DowngradeException()
                                            if msg_type == "admin_action" and role == "admin":
                                                action = msg.get("action")
                                                if action == "approve":
                                                    tid = msg.get("target_id")
                                                    manager.requests = [r for r in manager.requests if r["id"] != tid]
                                                    manager.speaking_allowed_clients.add(tid)
                                                    await manager.broadcast_admin_state()
                                                    await manager.broadcast_user_list()
                                                    for ws_client, info in manager.clients.items():
                                                        if info["id"] == tid:
                                                            try: await ws_client.send_json({"type": "speak_approved"})
                                                            except: pass
                                                elif action == "reject":
                                                    tid = msg.get("target_id")
                                                    manager.requests = [r for r in manager.requests if r["id"] != tid]
                                                    await manager.broadcast_admin_state()
                                                elif action == "revoke":
                                                    tid = msg.get("target_id")
                                                    manager.speaking_allowed_clients.discard(tid)
                                                    if manager.floor_owner == tid: manager.release_floor()
                                                    await manager.broadcast_user_list()
                                                    for ws_client, info in manager.clients.items():
                                                        if info["id"] == tid:
                                                            try: await ws_client.send_json({"type": "speak_revoked"})
                                                            except: pass
                                                elif action == "revoke_all_viewers":
                                                    revoked_ids = list(manager.speaking_allowed_clients)
                                                    manager.speaking_allowed_clients.clear()
                                                    if manager.floor_owner in revoked_ids: manager.release_floor()
                                                    await manager.broadcast_user_list()
                                                    for ws_client, info in manager.clients.items():
                                                        if info["id"] in revoked_ids:
                                                            try: await ws_client.send_json({"type": "speak_revoked"})
                                                            except: pass
                                                elif action == "mute_all":
                                                    manager.is_admin_muted = True
                                                    manager.release_floor()
                                                elif action == "unmute_all":
                                                    manager.is_admin_muted = False
                                                    await manager.broadcast_floor_state()
                                            elif msg_type == "config":
                                                if role == "admin":
                                                    if "glossary" in msg: manager.global_glossary = msg.get("glossary", "")
                                                    if "targets" in msg: manager.global_targets = msg.get("targets", manager.global_targets)
                                        except DowngradeException as de: raise de 
                                        except: pass
                        except websockets.exceptions.ConnectionClosed: pass
                        except DowngradeException as de: raise de
                        except Exception: pass

                    async def receiver():
                        current_msg_id = secrets.token_hex(4)
                        try:
                            while True:
                                msg = await azure_queue.get()
                                text = msg["text"]
                                raw_lid = msg["lid"]
                                
                                if manager.floor_owner is None and not manager.is_admin_muted and role != "admin": manager.set_floor(client_id)
                                if role != "admin" and manager.floor_owner != client_id: continue

                                if text:
                                    current_targets_list = manager.global_targets.split(',')
                                    tag = f"[{name}] "
                                    if msg["type"] == "interim":
                                        await manager.broadcast_json({"type": "interim", "text": tag + text, "targets": current_targets_list, "msg_id": current_msg_id})
                                    elif msg["type"] == "final":
                                        await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                        detected_lang = raw_lid[:2] if raw_lid != "unknown" else "multi_azure"
                                        asyncio.create_task(translate_and_send(text, detected_lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))
                                        current_msg_id = secrets.token_hex(4)
                        except Exception: pass

                    try: await asyncio.gather(sender(), receiver())
                    except DowngradeException:
                        recognizer.stop_continuous_recognition_async()
                        push_stream.close()
                        manager.speaking_allowed_clients.discard(client_id)
                        if manager.floor_owner == client_id: manager.release_floor()
                        await manager.broadcast_user_list()
                        continue 
                    finally:
                        recognizer.stop_continuous_recognition_async()
                        push_stream.close()
                        
                else:
                    dg_lang = lang
                    keywords_param = ""
                    if glossary:
                        extracted_words = re.findall(r'^([^=:-]+)', glossary, re.MULTILINE)
                        clean_words = [w.strip() for w in extracted_words if w.strip()]
                        if clean_words: keywords_param = "&" + "&".join([f"keywords={w}" for w in clean_words])

                    replace_rules = ["payment:pavement", "Payment:Pavement", "payments:pavements", "Payments:Pavements", "computer:computing"]
                    replace_param = "".join([f"&replace={r}" for r in replace_rules])

                    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={dg_lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true{keywords_param}{replace_param}"
                    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

                    ws_kwargs = {}
                    if int(websockets.__version__.split('.')[0]) >= 14: ws_kwargs["additional_headers"] = headers
                    else: ws_kwargs["extra_headers"] = headers

                    async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
                        await manager.broadcast_json({"type": "status", "text": "🚀 단일 언어 집중 모드 가동 중..."})
                        
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
                                                msg_type = msg.get("type")
                                                if msg_type == "downgrade_to_viewer": raise DowngradeException()
                                                if msg_type == "admin_action" and role == "admin":
                                                    action = msg.get("action")
                                                    if action == "approve":
                                                        tid = msg.get("target_id")
                                                        manager.requests = [r for r in manager.requests if r["id"] != tid]
                                                        manager.speaking_allowed_clients.add(tid)
                                                        await manager.broadcast_admin_state()
                                                        await manager.broadcast_user_list()
                                                        for ws_client, info in manager.clients.items():
                                                            if info["id"] == tid:
                                                                try: await ws_client.send_json({"type": "speak_approved"})
                                                                except: pass
                                                    elif action == "reject":
                                                        tid = msg.get("target_id")
                                                        manager.requests = [r for r in manager.requests if r["id"] != tid]
                                                        await manager.broadcast_admin_state()
                                                    elif action == "revoke":
                                                        tid = msg.get("target_id")
                                                        manager.speaking_allowed_clients.discard(tid)
                                                        if manager.floor_owner == tid: manager.release_floor()
                                                        await manager.broadcast_user_list()
                                                        for ws_client, info in manager.clients.items():
                                                            if info["id"] == tid:
                                                                try: await ws_client.send_json({"type": "speak_revoked"})
                                                                except: pass
                                                    elif action == "revoke_all_viewers":
                                                        revoked_ids = list(manager.speaking_allowed_clients)
                                                        manager.speaking_allowed_clients.clear()
                                                        if manager.floor_owner in revoked_ids: manager.release_floor()
                                                        await manager.broadcast_user_list()
                                                        for ws_client, info in manager.clients.items():
                                                            if info["id"] in revoked_ids:
                                                                try: await ws_client.send_json({"type": "speak_revoked"})
                                                                except: pass
                                                    elif action == "mute_all":
                                                        manager.is_admin_muted = True
                                                        manager.release_floor()
                                                    elif action == "unmute_all":
                                                        manager.is_admin_muted = False
                                                        await manager.broadcast_floor_state()
                                                elif msg_type == "config":
                                                    if role == "admin":
                                                        if "glossary" in msg: manager.global_glossary = msg.get("glossary", "")
                                                        if "targets" in msg: manager.global_targets = msg.get("targets", manager.global_targets)
                                            except DowngradeException as de: raise de 
                                            except: pass
                            except websockets.exceptions.ConnectionClosed: pass
                            except DowngradeException as de: raise de
                            except Exception: pass

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
                                            if manager.floor_owner is None and not manager.is_admin_muted and role != "admin": manager.set_floor(client_id)
                                            if role != "admin" and manager.floor_owner != client_id: continue

                                        if transcript or current_sentence:
                                            display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                            current_targets_list = manager.global_targets.split(',')
                                            tag = f"[{name}] "
                                            await manager.broadcast_json({"type": "interim", "text": tag + display_text.strip(), "targets": current_targets_list, "msg_id": current_msg_id})

                                        if is_final and transcript:
                                            if current_sentence: current_sentence += " " + transcript
                                            else: current_sentence = transcript

                                        is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                                        if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                            final_text = current_sentence.strip()
                                            if final_text != last_translated_text:
                                                last_translated_text = final_text
                                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                                asyncio.create_task(translate_and_send(final_text, lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))
                                            current_sentence = ""
                                            current_msg_id = secrets.token_hex(4)
                            except websockets.exceptions.ConnectionClosed: pass
                            except Exception: pass

                        try: await asyncio.gather(sender(), receiver())
                        except DowngradeException:
                            manager.speaking_allowed_clients.discard(client_id)
                            if manager.floor_owner == client_id: manager.release_floor()
                            await manager.broadcast_user_list()
                            continue 
                break 
    except websockets.exceptions.ConnectionClosed: pass
    except Exception as e: print(f"🚨 전체 웹소켓 에러: {e}", flush=True)
    finally: await manager.disconnect(websocket)

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 실시간 글로벌 통역 서버를 시작합니다... (Port: {port})")
    uvicorn.run(app, host="0.0.0.0", port=port)