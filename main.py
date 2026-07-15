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
import PyPDF2  # fitz 대신 절대 오류가 안 나는 PyPDF2로 복구
import docx
from pptx import Presentation

# ==========================================
# 💡 1. API 키 로드
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "")

claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
app = FastAPI()

# CORS 설정 (온렌더 URL 명시적 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "https://safety-voice-app.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 💰 2. 인증 & 글로벌 룸 세팅
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

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        user_id = str(data.get("username", data.get("id", ""))).strip()
        password = str(data.get("password", "")).strip()
        
        if user_id in USER_DB and USER_DB[user_id] == password:
            token = secrets.token_hex(16)
            ACTIVE_TOKENS.add(token)
            print(f"🔐 [인증 성공] 사용자 '{user_id}' 로그인", flush=True)
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

    def disconnect(self, websocket: WebSocket):
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
            
        asyncio.create_task(self.broadcast_admin_state())
        asyncio.create_task(self.broadcast_floor_state())
        asyncio.create_task(self.broadcast_user_list())

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
# 🌟 문서 업로드 파싱 (PyPDF2로 롤백, PPT 지원 유지)
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
            # fitz 대신 절대 오류 안 나는 PyPDF2 사용
            try:
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
                for page in pdf_reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
            except Exception as e:
                return JSONResponse({"success": False, "message": f"PDF 파싱 오류: {str(e)}"})
        elif ext in ['doc', 'docx']:
            doc = docx.Document(io.BytesIO(content))
            extracted_text = "\n".join([para.text for para in doc.paragraphs])
        elif ext in ['ppt', 'pptx']:
            try:
                prs = Presentation(io.BytesIO(content))
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text.strip():
                            extracted_text += shape.text + "\n"
            except Exception as e:
                return JSONResponse({"success": False, "message": f"PPT 파싱 오류: {str(e)}"})
        else:
            return JSONResponse({"success": False, "message": "지원하지 않는 파일 형식입니다. (pdf, docx, pptx, txt 가능)"})
        
        extracted_text = extracted_text[:50000]
        manager.global_document_context = extracted_text
        return JSONResponse({"success": True, "message": "문서 파싱 및 AI 학습 준비 완료"})
    except Exception as e:
        return JSONResponse({"success": False, "message": f"문서 처리 중 오류 발생: {str(e)}"})

async def update_sliding_summary(summary_state: dict, new_sentences: list):
    current_summary = summary_state.get("text", "")
    new_text = "\n".join(new_sentences)
    
    prompt = f"""You are a context summarizer for an international road pavement engineering seminar.
    Update the existing summary with the new sentences.
    Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context regarding pavement design, materials, or structural analysis.
    
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
    except Exception:
        pass

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):
    try:
        if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰", "위험해", "파손", "붕괴"]):
            await manager.broadcast_json({"type": "alert"})

        ignore_words = ["you", "thank you", "o", "hmm", "uh", "well", "so", "okay", "아", "음", "hola", "어", "네", "아니요"]
        if not text or len(text) < 2 or text.lower() in ignore_words:
            return 

        history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"
        glossary_section = f"\n[CIVIL ENGINEERING GLOSSARY]\n{glossary_text}\n" if glossary_text.strip() else ""
        doc_section = f"\n[REFERENCE DOCUMENT / PAPER CONTEXT]\n{manager.global_document_context}\n" if manager.global_document_context else ""

        if source_lang == "multi" or source_lang == "multi_azure":
            lang_instruction = "The STT engine detected the language automatically. However, cross-check the context."
        else:
            lang_instruction = f"The spoken language is strictly '{source_lang}'."

        system_prompt = f"""You are an elite simultaneous interpreter for an international civil engineering expert seminar involving Korea, China, Japan, and the US.
Domain focus: Road pavement engineering, asphalt and Portland cement concrete materials, pavement structure design, construction quality control (QA/QC), and pavement distress evaluation.

[PAST CONTEXT SUMMARY]
{summary_state.get('text', 'No summary yet.')}

[RECENT CONTEXT]
{history_str}
{glossary_section}
{doc_section}

CRITICAL INSTRUCTIONS (MUST OBEY):
1. [DOMAIN FORCED ANCHORING]: The sole context is 'Road Pavement Engineering, Asphalt/Concrete Materials, and Pavement Structure'. Homophones and general terms MUST be strictly translated into pavement engineering terminology (e.g., interpret 'base' as 'Base Course', 'binder' as 'Asphalt Binder').
2. [NUMERICAL & UNIT IMMUTABILITY]: Numbers, dimensions, and engineering units (e.g., MPa, mm, °C, kg/m³, kN, ESAL) MUST be preserved exactly as spoken. Convert any colloquial numbers into strict Arabic numerals without spacing before the unit.
3. [STT MEDIA-BIAS CORRECTION]: The STT input may contain media-biased misrecognitions. You MUST logically auto-correct broadcast terms (e.g., "구독자/subscribers", "채널/channel") into academic terms (e.g., "참석자/attendees", "세미나/seminar") based on the academic context.
4. [MATERIAL SPECIFICITY]: Strictly differentiate between engineering materials. Do not confuse "Cement" with "Concrete". Use the exact corresponding terms in KR, CN, JP, and US standards.
5. [SINGLE DEFINITIVE OUTPUT]: Provide EXACTLY ONE best translation per target language. NEVER use slashes (/) for alternatives or provide multiple options. Be decisive.
6. [ACADEMIC FORMALITY]: Maintain a highly formal, objective, and professional academic tone. Use formal polite forms in Korean (e.g., ~입니다/합니다) and Japanese (e.g., です/ます), and formal written style in Chinese.
7. [OMITTED SUBJECT INFERENCE]: Korean and Japanese speakers often omit subjects. You MUST accurately infer the omitted subject (e.g., "I", "We", "This study", "The pavement") based on the recent engineering context before translating to English or Chinese.
8. [ACRONYM & ABBREVIATION RETENTION]: Internationally recognized civil engineering acronyms (e.g., RAP, HMA, WMA, FWD) MUST be kept in English capital letters across all language outputs unless a strict local academic equivalent exists.
9. [CHITCHAT & NOISE REJECTION]: If the STT captures meaningless filler words, coughs, or irrelevant background chitchat, DO NOT translate. Output exactly [SKIP].
10. [CROSS-LINGUAL CONSISTENCY]: Ensure the core engineering concept remains identical across KR, EN, CN, and JP translations. Use the English standard as the semantic anchor.
11. [GLOSSARY OVERRIDE]: If a [REFERENCE DOCUMENT / GLOSSARY] is provided, its terminology and context ABSOLUTELY OVERRIDE your pre-trained knowledge.
12. [NO CONVERSING]: You are a translation engine. NEVER converse with the speaker. Output ONLY the translated text.
13. [INCOMPLETE SENTENCE HANDLING]: If the input sentence is grammatically incomplete but contains valid engineering data, translate the available fragment accurately without hallucinating an ending.
14. [FORMAT STRICTNESS]: Respond EXACTLY in the requested tag format (e.g., [en] result). NEVER use markdown code blocks (```) or add any extra text outside the tags.
15. [SPEAKER PERSPECTIVE ALIGNMENT]: Maintain the speaker's first-person perspective as the researcher/engineer.
16. [EQUIPMENT LOCALIZATION]: Translate construction machinery names into industry-standard terms avoiding literal or generic translations.
17. [METHODOLOGY & PROCESS PRESERVATION]: When translating construction methods or experimental procedures, preserve the chronological sequence exactly as spoken.
18. [CULTURAL IDIOM NEUTRALIZATION]: Translate cultural idioms or metaphors into clear, objective engineering statements.
19. [REGIONAL STANDARD AWARENESS]: Be aware that Korea/Japan/China use metric standards, while the US uses imperial. Do not auto-convert units unless specifically instructed, but translate the unit names accurately.
20. [SAFETY & RISK ALERTNESS]: Terms related to pavement failure, hazards, or structural failures MUST be translated with absolute clarity and urgency.

{lang_instruction}

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
        if any("[SKIP]" in t.upper() for t in lang_text.values()):
            return

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
        await manager.broadcast_json({"type": "sentence_complete"})
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
                        await websocket.send_json({"type": "status", "text": "❌ Azure API Key가 설정되지 않았습니다."})
                        raise Exception("Azure key missing")
                        
                    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
                    compressed_format = speechsdk.audio.AudioStreamFormat(compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY)
                    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=compressed_format)
                    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

                    auto_detect_source_language_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                        languages=["ko-KR", "en-US", "ja-JP", "zh-CN"]
                    )

                    recognizer = speechsdk.SpeechRecognizer(
                        speech_config=speech_config,
                        auto_detect_source_language_config=auto_detect_source_language_config,
                        audio_config=audio_config
                    )

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
                    
                    await manager.broadcast_json({"type": "status", "text": "🌐 Azure 다국어 식별 가동 중..."})

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
                                            
                                            if msg_type == "downgrade_to_viewer":
                                                raise DowngradeException()

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
                                                    if manager.floor_owner == tid:
                                                        manager.release_floor()
                                                    await manager.broadcast_user_list()
                                                    for ws_client, info in manager.clients.items():
                                                        if info["id"] == tid:
                                                            try: await ws_client.send_json({"type": "speak_revoked"})
                                                            except: pass
                                                elif action == "revoke_all_viewers":
                                                    revoked_ids = list(manager.speaking_allowed_clients)
                                                    manager.speaking_allowed_clients.clear()
                                                    if manager.floor_owner in revoked_ids:
                                                        manager.release_floor()
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
                                        except DowngradeException as de:
                                            raise de 
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
                                
                                if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":
                                    manager.set_floor(client_id)
                                if role != "admin" and manager.floor_owner != client_id:
                                    continue

                                if text:
                                    current_targets_list = manager.global_targets.split(',')
                                    tag = f"[{name}] "
                                    
                                    if msg["type"] == "interim":
                                        await manager.broadcast_json({
                                            "type": "interim", 
                                            "text": tag + text,
                                            "targets": current_targets_list,
                                            "msg_id": current_msg_id
                                        })
                                    elif msg["type"] == "final":
                                        await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                        detected_lang = raw_lid[:2] if raw_lid != "unknown" else "multi_azure"
                                        asyncio.create_task(translate_and_send(text, detected_lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))
                                        current_msg_id = secrets.token_hex(4)
                        except Exception: pass

                    try:
                        await asyncio.gather(sender(), receiver())
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
                        if clean_words:
                            keywords_param = "&" + "&".join([f"keywords={w}" for w in clean_words])

                    replace_rules = ["구독자:참석자", "payment:pavement", "Payment:Pavement", "payments:pavements", "Payments:Pavements", "computer:computing"]
                    replace_param = "".join([f"&replace={r}" for r in replace_rules])

                    dg_url = f"wss://[api.deepgram.com/v1/listen?model=nova-2&language=](https://api.deepgram.com/v1/listen?model=nova-2&language=){dg_lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true{keywords_param}{replace_param}"
                    
                    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

                    ws_kwargs = {}
                    if int(websockets.__version__.split('.')[0]) >= 14:
                        ws_kwargs["additional_headers"] = headers
                    else:
                        ws_kwargs["extra_headers"] = headers

                    async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
                        await manager.broadcast_json({"type": "status", "text": "🚀 Deepgram 단일 언어 모드 가동 중..."})
                        
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
                                                
                                                if msg_type == "downgrade_to_viewer":
                                                    raise DowngradeException()

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
                                                        if manager.floor_owner == tid:
                                                            manager.release_floor()
                                                        await manager.broadcast_user_list()
                                                        for ws_client, info in manager.clients.items():
                                                            if info["id"] == tid:
                                                                try: await ws_client.send_json({"type": "speak_revoked"})
                                                                except: pass
                                                    elif action == "revoke_all_viewers":
                                                        revoked_ids = list(manager.speaking_allowed_clients)
                                                        manager.speaking_allowed_clients.clear()
                                                        if manager.floor_owner in revoked_ids:
                                                            manager.release_floor()
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
                                            except DowngradeException as de:
                                                raise de 
                                            except: pass
                            except websockets.exceptions.ConnectionClosed: pass
                            except DowngradeException as de:
                                raise de
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
                                            if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":
                                                manager.set_floor(client_id)
                                            if role != "admin" and manager.floor_owner != client_id:
                                                continue

                                        if transcript or current_sentence:
                                            display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                            
                                            current_targets_list = manager.global_targets.split(',')
                                            tag = f"[{name}] "
                                            
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
                                                
                                                asyncio.create_task(translate_and_send(final_text, lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))
                                            
                                            current_sentence = ""
                                            current_msg_id = secrets.token_hex(4)
                            except websockets.exceptions.ConnectionClosed: pass
                            except Exception: pass

                        try:
                            await asyncio.gather(sender(), receiver())
                        except DowngradeException:
                            manager.speaking_allowed_clients.discard(client_id)
                            if manager.floor_owner == client_id:
                                manager.release_floor()
                            await manager.broadcast_user_list()
                            continue 
                break 
    except websockets.exceptions.ConnectionClosed: pass
    except Exception as e: print(f"🚨 전체 웹소켓 연결 에러: {e}", flush=True)
    finally: manager.disconnect(websocket)

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 실시간 글로벌 통역 서버 (Hybrid 엔진)를 시작합니다... (Port: {port})")
    uvicorn.run(app, host="0.0.0.0", port=port)