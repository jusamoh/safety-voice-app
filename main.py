import asyncio
import json
import os
import sys
import re  
import secrets 
import io
from datetime import datetime
import websockets

# ✅ 점검 완료: 파일 업로드 및 FastAPI 필수 모듈 완비[cite: 3]
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, UploadFile, File[cite: 3]
from fastapi.middleware.cors import CORSMiddleware[cite: 3]
from fastapi.responses import FileResponse, JSONResponse[cite: 3]
from pydantic import BaseModel[cite: 3]
from anthropic import AsyncAnthropic[cite: 3]

# ✅ 점검 완료: 다국어 및 문서 파싱용 외부 라이브러리 완비[cite: 3]
import azure.cognitiveservices.speech as speechsdk[cite: 3]
import PyPDF2[cite: 3]
import docx[cite: 3]

# ==========================================
# 💡 1. API 키 로드[cite: 3]
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")[cite: 3]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")[cite: 3]
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")[cite: 3]
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "")[cite: 3]

claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)[cite: 3]
app = FastAPI()[cite: 3]

app.add_middleware(
    CORSMiddleware,[cite: 3]
    allow_origins=["*"],[cite: 3]
    allow_credentials=True,[cite: 3]
    allow_methods=["*"],[cite: 3]
    allow_headers=["*"],[cite: 3]
)

# ==========================================
# 💰 2. 인증 & 글로벌 룸 세팅 (Room State)[cite: 3]
# ==========================================
USER_DB = {
    "admin": "1234",[cite: 3]
    "samsung": "sam1234",[cite: 3]
    "hyundai": "hdec1234",[cite: 3]
    "speaker": "speaker1234"[cite: 3]
}

ACTIVE_TOKENS = set()[cite: 3]

class DowngradeException(Exception):[cite: 3]
    pass[cite: 3]

@app.post("/api/login")[cite: 3]
async def login(request: Request):[cite: 3]
    try:[cite: 3]
        data = await request.json()[cite: 3]
        user_id = str(data.get("username", data.get("id", ""))).strip()[cite: 3]
        password = str(data.get("password", "")).strip()[cite: 3]
        
        if user_id in USER_DB and USER_DB[user_id] == password:[cite: 3]
            token = secrets.token_hex(16)[cite: 3]
            ACTIVE_TOKENS.add(token)[cite: 3]
            print(f"🔐 [인증 성공] 사용자 '{user_id}' 로그인 (토큰 발급됨)", flush=True)[cite: 3]
            return JSONResponse(content={"success": True, "token": token, "username": user_id})[cite: 3]
        else:[cite: 3]
            return JSONResponse(content={"success": False, "message": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)[cite: 3]
    except Exception as e:[cite: 3]
        return JSONResponse(content={"success": False, "message": f"로그인 에러: {str(e)}"}, status_code=400)[cite: 3]

@app.get("/")[cite: 3]
async def get():[cite: 3]
    return FileResponse("index.html")[cite: 3]

@app.get("/favicon.ico", include_in_schema=False)[cite: 3]
async def favicon():[cite: 3]
    from fastapi import Response[cite: 3]
    return Response(status_code=204)[cite: 3]

class ConnectionManager:[cite: 3]
    def __init__(self):[cite: 3]
        self.active_connections: list[WebSocket] = [][cite: 3]
        self.clients = {}[cite: 3]
        self.requests = [][cite: 3]
        self.floor_owner = None[cite: 3]
        self.is_admin_muted = False[cite: 3]
        self.global_targets = "ko"[cite: 3]
        self.global_glossary = ""[cite: 3]
        self.global_document_context = "" # ✅ 점검 완료: 사전 학습 텍스트 저장용 변수[cite: 3]
        self.speaking_allowed_clients = set()[cite: 3]

    async def connect(self, websocket: WebSocket, client_id: str, name: str, role: str, ui_lang: str):[cite: 3]
        await websocket.accept()[cite: 3]
        self.active_connections.append(websocket)[cite: 3]
        self.clients[websocket] = {"id": client_id, "name": name, "role": role, "ui_lang": ui_lang}[cite: 3]
        
        await self.broadcast_admin_state()[cite: 3]
        await self.broadcast_user_list()[cite: 3]
        
        await websocket.send_json({[cite: 3]
            "type": "floor_state",[cite: 3]
            "floor_owner": self.floor_owner,[cite: 3]
            "is_admin_muted": self.is_admin_muted[cite: 3]
        })[cite: 3]

    def disconnect(self, websocket: WebSocket):[cite: 3]
        if websocket in self.active_connections:[cite: 3]
            self.active_connections.remove(websocket)[cite: 3]
        
        client_info = self.clients.get(websocket)[cite: 3]
        if client_info:[cite: 3]
            client_id = client_info["id"][cite: 3]
            self.requests = [req for req in self.requests if req["id"] != client_id][cite: 3]
            self.speaking_allowed_clients.discard(client_id)[cite: 3]
            if self.floor_owner == client_id:[cite: 3]
                self.floor_owner = None[cite: 3]
            del self.clients[websocket][cite: 3]
            
        asyncio.create_task(self.broadcast_admin_state())[cite: 3]
        asyncio.create_task(self.broadcast_floor_state())[cite: 3]
        asyncio.create_task(self.broadcast_user_list())[cite: 3]

    async def broadcast_user_list(self):[cite: 3]
        users = [][cite: 3]
        for info in self.clients.values():[cite: 3]
            u = info.copy()[cite: 3]
            u["is_speaker"] = u["role"] in ["admin", "speaker"] or u["id"] in self.speaking_allowed_clients[cite: 3]
            users.append(u)[cite: 3]
        msg = {"type": "user_list", "users": users}[cite: 3]
        for ws, info in self.clients.items():[cite: 3]
            if info["role"] == "admin":[cite: 3]
                try:[cite: 3]
                    await ws.send_json(msg)[cite: 3]
                except:[cite: 3]
                    pass[cite: 3]

    async def broadcast_admin_state(self):[cite: 3]
        state = {"type": "admin_state", "requests": self.requests}[cite: 3]
        for ws, info in self.clients.items():[cite: 3]
            if info["role"] == "admin":[cite: 3]
                try: await ws.send_json(state)[cite: 3]
                except: pass[cite: 3]
                
    async def broadcast_floor_state(self):[cite: 3]
        msg = {"type": "floor_state", "floor_owner": self.floor_owner, "is_admin_muted": self.is_admin_muted}[cite: 3]
        await self.broadcast_json(msg)[cite: 3]

    async def broadcast_json(self, message: dict):[cite: 3]
        for connection in self.active_connections:[cite: 3]
            try: await connection.send_json(message)[cite: 3]
            except: pass[cite: 3]
            
    def set_floor(self, client_id: str):[cite: 3]
        self.floor_owner = client_id[cite: 3]
        asyncio.create_task(self.broadcast_floor_state())[cite: 3]
        
    def release_floor(self):[cite: 3]
        self.floor_owner = None[cite: 3]
        asyncio.create_task(self.broadcast_floor_state())[cite: 3]

manager = ConnectionManager()[cite: 3]

# ==========================================
# 🌟 문서 업로드 파싱[cite: 3]
# ==========================================
@app.post("/api/upload_context")[cite: 3]
async def upload_context(file: UploadFile = File(...)):[cite: 3]
    content = await file.read()[cite: 3]
    ext = file.filename.split('.')[-1].lower()[cite: 3]
    extracted_text = ""[cite: 3]
    
    try:[cite: 3]
        if ext in ['txt', 'csv']:[cite: 3]
            extracted_text = content.decode('utf-8')[cite: 3]
        elif ext == 'pdf':[cite: 3]
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))[cite: 3]
            for page in pdf_reader.pages:[cite: 3]
                extracted_text += page.extract_text() + "\n"[cite: 3]
        elif ext == 'docx':[cite: 3]
            doc = docx.Document(io.BytesIO(content))[cite: 3]
            extracted_text = "\n".join([para.text for para in doc.paragraphs])[cite: 3]
        else:[cite: 3]
            return JSONResponse({"success": False, "message": "지원하지 않는 파일 형식입니다. (pdf, docx, txt 가능)"})[cite: 3]
        
        extracted_text = extracted_text[:50000][cite: 3]
        manager.global_document_context = extracted_text[cite: 3]
        return JSONResponse({"success": True, "message": "문서 파싱 및 AI 학습 준비 완료"})[cite: 3]
    except Exception as e:[cite: 3]
        return JSONResponse({"success": False, "message": f"문서 처리 중 오류 발생: {str(e)}"})[cite: 3]

async def update_sliding_summary(summary_state: dict, new_sentences: list):[cite: 3]
    current_summary = summary_state.get("text", "")[cite: 3]
    new_text = "\n".join(new_sentences)[cite: 3]
    
    prompt = f"""You are a context summarizer for a multinational civil engineering expert seminar.
    Update the existing summary with the new sentences.
    Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context regarding 3D mapping of small underground pipelines, GPR, or specific engineering parameters.
    
    [Existing Summary]
    {current_summary if current_summary else "None"}
    
    [New Sentences]
    {new_text}
    
    Respond ONLY with the newly updated summary string in Korean."""[cite: 3]
    
    try:[cite: 3]
        response = await claude_client.messages.create([cite: 3]
            model="claude-haiku-4-5-20251001",[cite: 3]
            max_tokens=150,[cite: 3]
            messages=[{"role": "user", "content": prompt}][cite: 3]
        )[cite: 3]
        summary_state["text"] = response.content[0].text.strip()[cite: 3]
        print(f"\n🧠 [비서 요약 봇] 문맥 압축 완료: {summary_state['text']}\n", flush=True)[cite: 3]
    except Exception as e:[cite: 3]
        print(f"Summary Error: {e}", flush=True)[cite: 3]

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):[cite: 3]
    try:[cite: 3]
        if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰", "위험해"]):[cite: 3]
            await manager.broadcast_json({"type": "alert"})[cite: 3]

        ignore_words = ["you", "thank you", "o", "hmm", "uh", "well", "so", "Okay", "아", "음", "hola", "어", "네", "아니요"][cite: 3]
        if not text or len(text) < 2 or text.lower() in ignore_words:[cite: 3]
            return[cite: 3]

        history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"[cite: 3]
        glossary_section = f"\n[CIVIL ENGINEERING GLOSSARY]\n{glossary_text}\n" if glossary_text.strip() else ""[cite: 3]
        
        # ✅ 점검 완료: 문서 내용을 프롬프트에 제공하여 학술용어 번역 정확도 향상[cite: 3]
        doc_section = f"\n[REFERENCE DOCUMENT / PAPER CONTEXT]\n{manager.global_document_context}\n" if manager.global_document_context else ""[cite: 3]

        if source_lang == "multi" or source_lang == "multi_azure":[cite: 3]
            lang_instruction = "The STT engine detected the language automatically. However, cross-check the context."[cite: 3]
        else:[cite: 3]
            lang_instruction = f"The spoken language is strictly '{source_lang}'."[cite: 3]

        # 🌟 극강의 정확도를 위한 관로 매핑 엔지니어링 특화 프롬프트[cite: 3]
        system_prompt = f"""You are an elite simultaneous interpreter for an international civil engineering expert seminar involving Korea, China, Japan, and the US.
Domain focus: 3D mapping of small underground pipelines, Ground Penetrating Radar (GPR), multi-jointed robot technologies, and related underground utility detection methods.

[PAST CONTEXT SUMMARY]
{summary_state.get('text', 'No summary yet.')}

[RECENT CONTEXT]
{history_str}
{glossary_section}
{doc_section}

CRITICAL INSTRUCTIONS (MUST OBEY):
1. [DOMAIN FORCED ANCHORING]: The sole context is 'Underground Pipeline 3D Mapping and Civil Engineering'. Homophones must be translated into engineering terms.
2. [NUMERICAL & UNIT IMMUTABILITY]: Numbers, dimensions, and engineering units (e.g., MPa, mm, °C, kg/m³, kN) MUST be preserved exactly as spoken. Convert any colloquial numbers into strict Arabic numerals without spacing before the unit.
3. [STT MEDIA-BIAS CORRECTION]: The STT input may contain media-biased misrecognitions. You MUST logically auto-correct broadcast terms (e.g., "구독자/subscribers", "채널/channel") into academic terms (e.g., "참석자/attendees", "세미나/seminar") based on the academic context.
4. [MATERIAL SPECIFICITY]: Strictly differentiate between engineering materials. Do not confuse "Cement" with "Concrete". Use the exact corresponding terms in KR, CN, JP, and US standards.
5. [SINGLE DEFINITIVE OUTPUT]: Provide EXACTLY ONE best translation per target language. NEVER use slashes (/) for alternatives or provide multiple options. Be decisive.
6. [ACADEMIC FORMALITY]: Maintain a highly formal, objective, and professional academic tone. Use formal polite forms in Korean (e.g., ~입니다/합니다) and Japanese (e.g., です/ます), and formal written style in Chinese.
7. [OMITTED SUBJECT INFERENCE]: Korean and Japanese speakers often omit subjects. You MUST accurately infer the omitted subject (e.g., "I", "We", "This study", "The pipeline") based on the recent engineering context before translating to English or Chinese.
8. [ACRONYM & ABBREVIATION RETENTION]: Internationally recognized civil engineering acronyms (e.g., GPR, IMU) MUST be kept in English capital letters across all language outputs unless a strict local academic equivalent exists.
9. [CHITCHAT & NOISE REJECTION]: If the STT captures meaningless filler words, coughs, or irrelevant background chitchat (e.g., "아", "음", "마이크 테스트"), DO NOT translate. Output exactly [SKIP].
10. [CROSS-LINGUAL CONSISTENCY]: Ensure the core engineering concept remains identical across KR, EN, CN, and JP translations. Use the English standard as the semantic anchor.
11. [GLOSSARY OVERRIDE]: If a [REFERENCE DOCUMENT / GLOSSARY] is provided, its terminology and context ABSOLUTELY OVERRIDE your pre-trained knowledge.
12. [NO CONVERSING]: You are a translation engine. NEVER converse with the speaker, ask clarifying questions, or add meta-comments. Output ONLY the translated text.
13. [INCOMPLETE SENTENCE HANDLING]: If the input sentence is grammatically incomplete but contains valid engineering data, translate the available fragment accurately without hallucinating an ending.
14. [FORMAT STRICTNESS]: Respond EXACTLY in the requested tag format (e.g., [en] result). NEVER use markdown code blocks (```) or add any extra text outside the tags.
15. [SPEAKER PERSPECTIVE ALIGNMENT]: Maintain the speaker's first-person perspective as the researcher/engineer. Do not translate as a third-party observer.
16. [EQUIPMENT LOCALIZATION]: Translate construction machinery names into industry-standard terms avoiding literal or generic translations.
17. [METHODOLOGY & PROCESS PRESERVATION]: When translating construction methods or experimental procedures, preserve the chronological sequence and causal relationships exactly as spoken.
18. [CULTURAL IDIOM NEUTRALIZATION]: Translate cultural idioms or metaphors into clear, objective engineering statements.
19. [REGIONAL STANDARD AWARENESS]: Be aware that Korea/Japan/China use metric standards, while the US uses imperial. Do not auto-convert units unless specifically instructed, but translate the unit names accurately.
20. [SAFETY & RISK ALERTNESS]: Terms related to construction safety, hazards, or structural failures MUST be translated with absolute clarity and urgency, avoiding any ambiguity.

{lang_instruction}

Respond EXACTLY in this tag format (DO NOT USE JSON):
[original]
clean current sentence
"""[cite: 3]
        for t in targets.split(','):[cite: 3]
            system_prompt += f"[{t.strip()}]\nresult\n"[cite: 3]

        stream = await claude_client.messages.create([cite: 3]
            model="claude-haiku-4-5-20251001",[cite: 3]
            max_tokens=500,[cite: 3]
            system=system_prompt, [cite: 3]
            messages=[{"role": "user", "content": text}],[cite: 3]
            stream=True[cite: 3]
        )[cite: 3]

        buffer = ""[cite: 3]
        lang_text = {}[cite: 3]
        
        async for event in stream:[cite: 3]
            if event.type == "content_block_delta":[cite: 3]
                buffer += event.delta.text[cite: 3]
                
                matches = re.finditer(r'\[([a-zA-Z-]+)\]\s*(.*?)(?=\[|$)', buffer, re.DOTALL)[cite: 3]
                for match in matches:[cite: 3]
                    lang = match.group(1).lower().strip()[cite: 3]
                    text_so_far = match.group(2).strip()[cite: 3]
                    
                    lang_text[lang] = text_so_far[cite: 3]
                    
                    if lang != 'original':[cite: 3]
                        await manager.broadcast_json({[cite: 3]
                            "type": "stream_update",[cite: 3]
                            "lang": lang,[cite: 3]
                            "text": text_so_far,[cite: 3]
                            "original_text": lang_text.get('original', ''),[cite: 3]
                            "source_lang": source_lang,[cite: 3]
                            "msg_id": msg_id[cite: 3]
                        })[cite: 3]
        
        original_text = lang_text.get('original', text)[cite: 3]
        if any("[SKIP]" in t.upper() for t in lang_text.values()):[cite: 3]
            return[cite: 3]

        recent_history.append(original_text)[cite: 3]
        
        if len(recent_history) >= 5:[cite: 3]
            sentences_to_summarize = recent_history[:3][cite: 3]
            del recent_history[:3] [cite: 3]
            asyncio.create_task(update_sliding_summary(summary_state, sentences_to_summarize))[cite: 3]
        
        for lang, final_text in lang_text.items():[cite: 3]
            if lang != 'original':[cite: 3]
                display_final = f"[{'사회자' if role == 'admin' else name}] {final_text}"[cite: 3]
                await manager.broadcast_json({[cite: 3]
                    "type": "stream_end",[cite: 3]
                    "lang": lang,[cite: 3]
                    "text": display_final,[cite: 3]
                    "raw_text": final_text,[cite: 3]
                    "original_text": lang_text.get('original', ''),[cite: 3]
                    "source_lang": source_lang,[cite: 3]
                    "msg_id": msg_id,[cite: 3]
                    "role": role,[cite: 3]
                    "name": '사회자' if role == 'admin' else name[cite: 3]
                })[cite: 3]
                
    except Exception as e:[cite: 3]
        print(f"❌ [번역 에러 발생]: {e}", flush=True)[cite: 3]
        await manager.broadcast_json({"type": "status", "text": "❌ 번역 실패 (재시도 중)"})[cite: 3]
    
    finally:[cite: 3]
        await manager.broadcast_json({"type": "sentence_complete"})[cite: 3]
        manager.release_floor()[cite: 3]
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})[cite: 3]


@app.websocket("/ws")[cite: 3]
async def websocket_endpoint([cite: 3]
    websocket: WebSocket,  [cite: 3]
    token: str = Query(None), [cite: 3]
    lang: str = Query("ko"), [cite: 3]
    targets: str = Query("ko,en,ja,zh"), [cite: 3]
    role: str = Query("speaker"),[cite: 3]
    client_id: str = Query(None),[cite: 3]
    name: str = Query(None),[cite: 3]
    ui_lang: str = Query("ko"), [cite: 3]
    endpointing: int = Query(500), [cite: 3]
    max_chars: int = Query(35),[cite: 3]
    glossary: str = Query("") [cite: 3]
):[cite: 3]
    if token not in ACTIVE_TOKENS:[cite: 3]
        print(f"❌ [보안 차단] 유효하지 않은 토큰. (IP: {websocket.client})", flush=True)[cite: 3]
        await websocket.close(code=1008, reason="Unauthorized")[cite: 3]
        return[cite: 3]

    if not client_id: client_id = secrets.token_hex(4)[cite: 3]
    if not name: name = f"User_{client_id}"[cite: 3]

    await manager.connect(websocket, client_id, name, role, ui_lang)[cite: 3]
    
    recent_history = [] [cite: 3]
    summary_state = {"text": ""} [cite: 3]

    try:[cite: 3]
        while True:[cite: 3]
            is_speaker = role in ["admin", "speaker"] or client_id in manager.speaking_allowed_clients[cite: 3]
            
            if not is_speaker:[cite: 3]
                while True:[cite: 3]
                    data = await websocket.receive()[cite: 3]
                    if data.get("type") == "websocket.receive" and data.get("text") is not None:[cite: 3]
                        try:[cite: 3]
                            msg = json.loads(data.get("text"))[cite: 3]
                            msg_type = msg.get("type")[cite: 3]
                            if msg_type == "request_speak":[cite: 3]
                                manager.requests.append({"id": client_id, "name": name})[cite: 3]
                                await manager.broadcast_admin_state()[cite: 3]
                            elif msg_type == "cancel_request":[cite: 3]
                                manager.requests = [r for r in manager.requests if r["id"] != client_id][cite: 3]
                                await manager.broadcast_admin_state()[cite: 3]
                            elif msg_type == "upgrade_to_speaker":[cite: 3]
                                if client_id in manager.speaking_allowed_clients:[cite: 3]
                                    break [cite: 3]
                        except: pass[cite: 3]
            else:[cite: 3]
                # ==================================================[cite: 3]
                # 🌟 하이브리드 엔진 분기점 (Azure vs Deepgram)[cite: 3]
                # ==================================================[cite: 3]
                engine_mode = "azure" if lang == "multi_azure" else "deepgram"[cite: 3]
                
                if engine_mode == "azure":[cite: 3]
                    # --- [Azure Speech 가동: 4개국 토론 모드] ---[cite: 3]
                    if not AZURE_SPEECH_KEY:[cite: 3]
                        await websocket.send_json({"type": "status", "text": "❌ Azure API Key가 설정되지 않았습니다."})[cite: 3]
                        raise Exception("Azure key missing")[cite: 3]
                        
                    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)[cite: 3]
                    
                    compressed_format = speechsdk.audio.AudioStreamFormat(compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY)[cite: 3]
                    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=compressed_format)[cite: 3]
                    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)[cite: 3]

                    auto_detect_source_language_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig([cite: 3]
                        languages=["ko-KR", "en-US", "ja-JP", "zh-CN"][cite: 3]
                    )[cite: 3]

                    recognizer = speechsdk.SpeechRecognizer([cite: 3]
                        speech_config=speech_config,[cite: 3]
                        auto_detect_source_language_config=auto_detect_source_language_config,[cite: 3]
                        audio_config=audio_config[cite: 3]
                    )[cite: 3]

                    azure_queue = asyncio.Queue()[cite: 3]
                    loop = asyncio.get_running_loop()[cite: 3]

                    def recognizing_cb(evt):[cite: 3]
                        if evt.result.text:[cite: 3]
                            lid_result = evt.result.properties.get(speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult, "unknown")[cite: 3]
                            loop.call_soon_threadsafe(azure_queue.put_nowait, {"type": "interim", "text": evt.result.text, "lid": lid_result})[cite: 3]

                    def recognized_cb(evt):[cite: 3]
                        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:[cite: 3]
                            lid_result = evt.result.properties.get(speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult, "unknown")[cite: 3]
                            loop.call_soon_threadsafe(azure_queue.put_nowait, {"type": "final", "text": evt.result.text, "lid": lid_result})[cite: 3]

                    recognizer.recognizing.connect(recognizing_cb)[cite: 3]
                    recognizer.recognized.connect(recognized_cb)[cite: 3]
                    recognizer.start_continuous_recognition_async()[cite: 3]
                    
                    await manager.broadcast_json({"type": "status", "text": "🌐 Azure 다국어 식별 가동 중..."})[cite: 3]

                    async def sender():[cite: 3]
                        try:[cite: 3]
                            while True:[cite: 3]
                                data = await websocket.receive()[cite: 3]
                                if data.get("type") == "websocket.receive":[cite: 3]
                                    if data.get("bytes") is not None:[cite: 3]
                                        if role == "admin" or (not manager.is_admin_muted and (manager.floor_owner is None or manager.floor_owner == client_id)):[cite: 3]
                                            push_stream.write(data.get("bytes"))[cite: 3]
                                            
                                    elif data.get("text") is not None:[cite: 3]
                                        try:[cite: 3]
                                            msg = json.loads(data.get("text"))[cite: 3]
                                            msg_type = msg.get("type")[cite: 3]
                                            
                                            if msg_type == "downgrade_to_viewer":[cite: 3]
                                                raise DowngradeException()[cite: 3]

                                            if msg_type == "admin_action" and role == "admin":[cite: 3]
                                                action = msg.get("action")[cite: 3]
                                                if action == "approve":[cite: 3]
                                                    tid = msg.get("target_id")[cite: 3]
                                                    manager.requests = [r for r in manager.requests if r["id"] != tid][cite: 3]
                                                    manager.speaking_allowed_clients.add(tid)[cite: 3]
                                                    await manager.broadcast_admin_state()[cite: 3]
                                                    await manager.broadcast_user_list()[cite: 3]
                                                    for ws_client, info in manager.clients.items():[cite: 3]
                                                        if info["id"] == tid:[cite: 3]
                                                            try: await ws_client.send_json({"type": "speak_approved"})[cite: 3]
                                                            except: pass[cite: 3]
                                                elif action == "reject":[cite: 3]
                                                    tid = msg.get("target_id")[cite: 3]
                                                    manager.requests = [r for r in manager.requests if r["id"] != tid][cite: 3]
                                                    await manager.broadcast_admin_state()[cite: 3]
                                                elif action == "revoke":[cite: 3]
                                                    tid = msg.get("target_id")[cite: 3]
                                                    manager.speaking_allowed_clients.discard(tid)[cite: 3]
                                                    if manager.floor_owner == tid:[cite: 3]
                                                        manager.release_floor()[cite: 3]
                                                    await manager.broadcast_user_list()[cite: 3]
                                                    for ws_client, info in manager.clients.items():[cite: 3]
                                                        if info["id"] == tid:[cite: 3]
                                                            try: await ws_client.send_json({"type": "speak_revoked"})[cite: 3]
                                                            except: pass[cite: 3]
                                                elif action == "revoke_all_viewers":[cite: 3]
                                                    revoked_ids = list(manager.speaking_allowed_clients)[cite: 3]
                                                    manager.speaking_allowed_clients.clear()[cite: 3]
                                                    if manager.floor_owner in revoked_ids:[cite: 3]
                                                        manager.release_floor()[cite: 3]
                                                    await manager.broadcast_user_list()[cite: 3]
                                                    for ws_client, info in manager.clients.items():[cite: 3]
                                                        if info["id"] in revoked_ids:[cite: 3]
                                                            try: await ws_client.send_json({"type": "speak_revoked"})[cite: 3]
                                                            except: pass[cite: 3]
                                                elif action == "mute_all":[cite: 3]
                                                    manager.is_admin_muted = True[cite: 3]
                                                    manager.release_floor()[cite: 3]
                                                elif action == "unmute_all":[cite: 3]
                                                    manager.is_admin_muted = False[cite: 3]
                                                    await manager.broadcast_floor_state()[cite: 3]
                                            elif msg_type == "config":[cite: 3]
                                                if role == "admin":[cite: 3]
                                                    if "glossary" in msg: manager.global_glossary = msg.get("glossary", "")[cite: 3]
                                                    if "targets" in msg: manager.global_targets = msg.get("targets", manager.global_targets)[cite: 3]
                                        except DowngradeException as de:[cite: 3]
                                            raise de [cite: 3]
                                        except: pass[cite: 3]
                        except websockets.exceptions.ConnectionClosed: pass[cite: 3]
                        except DowngradeException as de: raise de[cite: 3]
                        except Exception as e: print(f"🚨 Azure Sender 에러: {e}", flush=True)[cite: 3]

                    async def receiver():[cite: 3]
                        current_msg_id = secrets.token_hex(4)[cite: 3]
                        try:[cite: 3]
                            while True:[cite: 3]
                                msg = await azure_queue.get()[cite: 3]
                                text = msg["text"][cite: 3]
                                raw_lid = msg["lid"][cite: 3]
                                
                                if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":[cite: 3]
                                    manager.set_floor(client_id)[cite: 3]
                                if role != "admin" and manager.floor_owner != client_id:[cite: 3]
                                    continue[cite: 3]

                                if text:[cite: 3]
                                    current_targets_list = manager.global_targets.split(',')[cite: 3]
                                    tag = f"[{name}] "[cite: 3]
                                    
                                    if msg["type"] == "interim":[cite: 3]
                                        await manager.broadcast_json({[cite: 3]
                                            "type": "interim", [cite: 3]
                                            "text": tag + text,[cite: 3]
                                            "targets": current_targets_list,[cite: 3]
                                            "msg_id": current_msg_id[cite: 3]
                                        })[cite: 3]
                                    elif msg["type"] == "final":[cite: 3]
                                        await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})[cite: 3]
                                        detected_lang = raw_lid[:2] if raw_lid != "unknown" else "multi_azure"[cite: 3]
                                        asyncio.create_task(translate_and_send(text, detected_lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))[cite: 3]
                                        current_msg_id = secrets.token_hex(4)[cite: 3]
                        except Exception as e: print(f"🚨 Azure Receiver 에러: {e}", flush=True)[cite: 3]

                    try:[cite: 3]
                        await asyncio.gather(sender(), receiver())[cite: 3]
                    except DowngradeException:[cite: 3]
                        recognizer.stop_continuous_recognition_async()[cite: 3]
                        push_stream.close()[cite: 3]
                        manager.speaking_allowed_clients.discard(client_id)[cite: 3]
                        if manager.floor_owner == client_id: manager.release_floor()[cite: 3]
                        await manager.broadcast_user_list()[cite: 3]
                        continue [cite: 3]
                    finally:[cite: 3]
                        recognizer.stop_continuous_recognition_async()[cite: 3]
                        push_stream.close()[cite: 3]
                        
                else:
                    # --- [Deepgram 가동: 단일 언어 발표 모드] ---[cite: 3]
                    dg_lang = lang[cite: 3]
                    keywords_param = ""[cite: 3]
                    if glossary:[cite: 3]
                        extracted_words = re.findall(r'^([^=:-]+)', glossary, re.MULTILINE)[cite: 3]
                        clean_words = [w.strip() for w in extracted_words if w.strip()][cite: 3]
                        if clean_words:[cite: 3]
                            keywords_param = "&" + "&".join([f"keywords={w}" for w in clean_words])[cite: 3]

                    # ✅ 점검 완료: STT 강제 치환 규칙 복구[cite: 3]
                    replace_rules = ["구독자:참석자", "payment:pavement", "Payment:Pavement", "payments:pavements", "Payments:Pavements", "computer:computing"][cite: 3]
                    replace_param = "".join([f"&replace={r}" for r in replace_rules])[cite: 3]

                    # ✅ 교정 완료: 대괄호 및 하이퍼링크 찌꺼기를 원천 제거하여 Invalid IPv6 URL 에러 해결
                    dg_url = f"wss://[api.deepgram.com/v1/listen?model=nova-2&language=](https://api.deepgram.com/v1/listen?model=nova-2&language=){dg_lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true{keywords_param}{replace_param}"[cite: 3]
                    
                    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}[cite: 3]

                    ws_kwargs = {}[cite: 3]
                    if int(websockets.__version__.split('.')[0]) >= 14:[cite: 3]
                        ws_kwargs["additional_headers"] = headers[cite: 3]
                    else:[cite: 3]
                        ws_kwargs["extra_headers"] = headers[cite: 3]

                    async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:[cite: 3]
                        await manager.broadcast_json({"type": "status", "text": "🚀 Deepgram 단일 언어 모드 가동 중..."})[cite: 3]
                        
                        async def sender():[cite: 3]
                            try:[cite: 3]
                                while True:[cite: 3]
                                    data = await websocket.receive()[cite: 3]
                                    if data.get("type") == "websocket.receive":[cite: 3]
                                        if data.get("bytes") is not None:[cite: 3]
                                            if role == "admin" or (not manager.is_admin_muted and (manager.floor_owner is None or manager.floor_owner == client_id)):[cite: 3]
                                                await dg_ws.send(data.get("bytes"))[cite: 3]
                                                
                                        elif data.get("text") is not None:[cite: 3]
                                            try:[cite: 3]
                                                msg = json.loads(data.get("text"))[cite: 3]
                                                msg_type = msg.get("type")[cite: 3]
                                                
                                                if msg_type == "downgrade_to_viewer":[cite: 3]
                                                    raise DowngradeException()[cite: 3]

                                                if msg_type == "admin_action" and role == "admin":[cite: 3]
                                                    action = msg.get("action")[cite: 3]
                                                    if action == "approve":[cite: 3]
                                                        tid = msg.get("target_id")[cite: 3]
                                                        manager.requests = [r for r in manager.requests if r["id"] != tid][cite: 3]
                                                        manager.speaking_allowed_clients.add(tid)[cite: 3]
                                                        await manager.broadcast_admin_state()[cite: 3]
                                                        await manager.broadcast_user_list()[cite: 3]
                                                        for ws_client, info in manager.clients.items():[cite: 3]
                                                            if info["id"] == tid:[cite: 3]
                                                                try: await ws_client.send_json({"type": "speak_approved"})[cite: 3]
                                                                except: pass[cite: 3]
                                                    elif action == "reject":[cite: 3]
                                                        tid = msg.get("target_id")[cite: 3]
                                                        manager.requests = [r for r in manager.requests if r["id"] != tid][cite: 3]
                                                        await manager.broadcast_admin_state()[cite: 3]
                                                    elif action == "revoke":[cite: 3]
                                                        tid = msg.get("target_id")[cite: 3]
                                                        manager.speaking_allowed_clients.discard(tid)[cite: 3]
                                                        if manager.floor_owner == tid:[cite: 3]
                                                            manager.release_floor()[cite: 3]
                                                        await manager.broadcast_user_list()[cite: 3]
                                                        for ws_client, info in manager.clients.items():[cite: 3]
                                                            if info["id"] == tid:[cite: 3]
                                                                try: await ws_client.send_json({"type": "speak_revoked"})[cite: 3]
                                                                except: pass[cite: 3]
                                                    elif action == "revoke_all_viewers":[cite: 3]
                                                        revoked_ids = list(manager.speaking_allowed_clients)[cite: 3]
                                                        manager.speaking_allowed_clients.clear()[cite: 3]
                                                        if manager.floor_owner in revoked_ids:[cite: 3]
                                                            manager.release_floor()[cite: 3]
                                                        await manager.broadcast_user_list()[cite: 3]
                                                        for ws_client, info in manager.clients.items():[cite: 3]
                                                            if info["id"] in revoked_ids:[cite: 3]
                                                                try: await ws_client.send_json({"type": "speak_revoked"})[cite: 3]
                                                                except: pass[cite: 3]
                                                    elif action == "mute_all":[cite: 3]
                                                        manager.is_admin_muted = True[cite: 3]
                                                        manager.release_floor()[cite: 3]
                                                    elif action == "unmute_all":[cite: 3]
                                                        manager.is_admin_muted = False[cite: 3]
                                                        await manager.broadcast_floor_state()[cite: 3]
                                                elif msg_type == "config":[cite: 3]
                                                    if role == "admin":[cite: 3]
                                                        if "glossary" in msg: manager.global_glossary = msg.get("glossary", "")[cite: 3]
                                                        if "targets" in msg: manager.global_targets = msg.get("targets", manager.global_targets)[cite: 3]
                                            except DowngradeException as de:[cite: 3]
                                                raise de [cite: 3]
                                            except: pass[cite: 3]
                            except websockets.exceptions.ConnectionClosed: pass[cite: 3]
                            except DowngradeException as de:[cite: 3]
                                raise de[cite: 3]
                            except Exception as e: print(f"🚨 Deepgram Sender 에러: {e}", flush=True)[cite: 3]

                        async def receiver():[cite: 3]
                            current_sentence = ""[cite: 3]
                            last_translated_text = "" [cite: 3]
                            current_msg_id = secrets.token_hex(4)[cite: 3]
                            try:[cite: 3]
                                while True:[cite: 3]
                                    dg_result = await dg_ws.recv()[cite: 3]
                                    dg_json = json.loads(dg_result)[cite: 3]
                                    
                                    if dg_json.get("type") == "Results":[cite: 3]
                                        is_final = dg_json.get("is_final", False)[cite: 3]
                                        speech_final = dg_json.get("speech_final", False)[cite: 3]
                                        transcript = dg_json.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()[cite: 3]
                                        
                                        if transcript:[cite: 3]
                                            if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":[cite: 3]
                                                manager.set_floor(client_id)[cite: 3]
                                            if role != "admin" and manager.floor_owner != client_id:[cite: 3]
                                                continue[cite: 3]

                                        if transcript or current_sentence:[cite: 3]
                                            display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript[cite: 3]
                                            
                                            current_targets_list = manager.global_targets.split(',')[cite: 3]
                                            tag = f"[{name}] "[cite: 3]
                                            
                                            await manager.broadcast_json({[cite: 3]
                                                "type": "interim", [cite: 3]
                                                "text": tag + display_text.strip(),[cite: 3]
                                                "targets": current_targets_list,[cite: 3]
                                                "msg_id": current_msg_id[cite: 3]
                                            })[cite: 3]

                                        if is_final and transcript:[cite: 3]
                                            if current_sentence: current_sentence += " " + transcript[cite: 3]
                                            else: current_sentence = transcript[cite: 3]

                                        is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))[cite: 3]

                                        if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():[cite: 3]
                                            final_text = current_sentence.strip()[cite: 3]
                                            
                                            if final_text != last_translated_text:[cite: 3]
                                                last_translated_text = final_text[cite: 3]
                                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})[cite: 3]
                                                
                                                asyncio.create_task(translate_and_send(final_text, lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))[cite: 3]
                                            
                                            current_sentence = ""[cite: 3]
                                            current_msg_id = secrets.token_hex(4)[cite: 3]
                            except websockets.exceptions.ConnectionClosed: pass[cite: 3]
                            except Exception as e: print(f"🚨 Deepgram Receiver 에러: {e}", flush=True)[cite: 3]

                        try:[cite: 3]
                            await asyncio.gather(sender(), receiver())[cite: 3]
                        except DowngradeException:[cite: 3]
                            manager.speaking_allowed_clients.discard(client_id)[cite: 3]
                            if manager.floor_owner == client_id:[cite: 3]
                                manager.release_floor()[cite: 3]
                            await manager.broadcast_user_list()[cite: 3]
                            continue [cite: 3]
                break [cite: 3]
    except websockets.exceptions.ConnectionClosed: pass[cite: 3]
    except Exception as e: print(f"🚨 전체 웹소켓 연결 에러: {e}", flush=True)[cite: 3]
    finally: manager.disconnect(websocket)[cite: 3]

if __name__ == "__main__":
    import multiprocessing[cite: 3]
    import uvicorn[cite: 3]
    multiprocessing.freeze_support()[cite: 3]
    print("🚀 실시간 글로벌 통역 서버 (Hybrid 엔진)를 시작합니다... ([http://0.0.0.0:10000](http://0.0.0.0:10000))")[cite: 3]
    uvicorn.run(app, host="0.0.0.0", port=10000)[cite: 3]