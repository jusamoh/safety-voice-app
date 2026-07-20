import asyncio
import hmac
import json
import os
import sys
import re  
import secrets 
import io
import time
from datetime import datetime
import websockets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from anthropic import AsyncAnthropic

import azure.cognitiveservices.speech as speechsdk
import PyPDF2
import docx

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
# 🛡️ [백신 모듈] 동음이의어 예외 사전 로드 및 주입
# ==========================================
def load_homophone_exceptions(filepath="homophone_exceptions.json") -> dict:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            print("🛡️ [백신 로드 성공] 동음이의어 방어 사전(homophone_exceptions.json)이 적용되었습니다.", flush=True)
            return json.load(f)
    except FileNotFoundError:
        print("⚠️ [백신 경고] homophone_exceptions.json 파일이 없습니다. 일반 번역 모드로 작동합니다.", flush=True)
        return {}

EXCEPTIONS_DICT = load_homophone_exceptions()

def inject_exception_prompts(transcript: str, exceptions: dict) -> str:
    injected_rules = []
    for risk_word, rule in exceptions.items():
        if risk_word in transcript:
            injected_rules.append(rule)
    
    if not injected_rules:
        return ""
    return "\n".join(injected_rules)
# ==========================================

def load_user_db() -> dict[str, str]:
    raw_users = os.environ.get("APP_USERS_JSON", "").strip()
    if not raw_users:
        print("⚠️ APP_USERS_JSON 환경변수가 설정되지 않아 로그인이 비활성화됩니다.", flush=True)
        return {}

    try:
        parsed = json.loads(raw_users)
    except json.JSONDecodeError as error:
        print(f"❌ APP_USERS_JSON 형식 오류: {error}", flush=True)
        return {}

    if not isinstance(parsed, dict):
        print("❌ APP_USERS_JSON은 사용자명과 비밀번호의 JSON 객체여야 합니다.", flush=True)
        return {}

    return {
        str(username).strip(): str(password)
        for username, password in parsed.items()
        if str(username).strip() and isinstance(password, str) and password
    }

USER_DB = load_user_db()

ACTIVE_TOKENS = set()

class DowngradeException(Exception):
    pass

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        user_id = str(data.get("username", data.get("id", ""))).strip()
        password = str(data.get("password", "")).strip()
        
        if not USER_DB:
            return JSONResponse(
                content={"success": False, "message": "서버 로그인 환경변수가 설정되지 않았습니다."},
                status_code=503
            )

        expected_password = USER_DB.get(user_id)
        if expected_password and hmac.compare_digest(expected_password, password):
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
        self.global_targets = "ko" 
        self.global_glossary = ""
        self.global_document_context = "" 
        self.speaking_allowed_clients = set()
        self.is_rehearsal_mode = False 
        self.is_tts_enabled = False

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
        await websocket.send_json({
            "type": "display_settings",
            "tts_enabled": self.is_tts_enabled
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
                try:
                    await ws.send_json(msg)
                except Exception:
                    pass

    async def broadcast_admin_state(self):
        state = {"type": "admin_state", "requests": self.requests}
        for ws, info in self.clients.items():
            if info["role"] == "admin":
                try:
                    await ws.send_json(state)
                except Exception:
                    pass
                
    async def broadcast_display_settings(self):
        await self.broadcast_json({
            "type": "display_settings",
            "tts_enabled": self.is_tts_enabled
        })

    async def broadcast_floor_state(self):
        msg = {"type": "floor_state", "floor_owner": self.floor_owner, "is_admin_muted": self.is_admin_muted}
        await self.broadcast_json(msg)

    async def broadcast_json(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass
            
    async def broadcast_feedback(self, message: dict, speaker_id: str):
        for ws, info in self.clients.items():
            is_target = False
            if info["role"] == "admin":
                is_target = True
            elif self.is_rehearsal_mode and info["id"] == speaker_id:
                is_target = True
            
            if is_target:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    def set_floor(self, client_id: str):
        self.floor_owner = client_id
        asyncio.create_task(self.broadcast_floor_state())
        
    def release_floor(self):
        self.floor_owner = None
        asyncio.create_task(self.broadcast_floor_state())

manager = ConnectionManager()
translation_semaphore = asyncio.Semaphore(2)

FILLER_ONLY_PHRASES = {
   "아", "어", "음", "흠", "uh", "um", "hmm",
    "あの", "えっと", "えーと", "えー", "んー", "うーん", "まあ",
    "嗯", "啊", "那个", "这个", "呃"
}

def normalize_targets(targets: str) -> list[str]:
    result = []
    for target in targets.split(','):
        lang = target.strip().lower()
        if lang and re.fullmatch(r'[a-z-]+', lang) and lang not in result:
            result.append(lang)
    return result

def is_filler_only(text: str) -> bool:
    normalized = re.sub(r'[^\w]+', '', text).lower()
    return normalized in FILLER_ONLY_PHRASES

async def run_until_first_complete(*coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        exception = task.exception()
        if exception:
            raise exception

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
            return JSONResponse({"success": False, "message": "지원하지 않는 파일 형식입니다."})
        
        extracted_text = extracted_text[:50000]
        manager.global_document_context = extracted_text
        return JSONResponse({"success": True, "message": "문서 파싱 및 AI 학습 준비 완료"})
    except Exception as e:
        return JSONResponse({"success": False, "message": f"문서 처리 중 오류 발생: {str(e)}"})

async def update_sliding_summary(summary_state: dict, new_sentences: list):
    current_summary = summary_state.get("text", "")
    new_text = "\n".join(new_sentences)
    
    prompt = f"""You are a context summarizer for a multinational civil engineering expert seminar.
    Update the existing summary with the new sentences.
    Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context regarding highway and airport pavement engineering, materials, or specific engineering parameters.
    
    [Existing Summary]
    {current_summary if current_summary else "None"}
    
    [New Sentences]
    {new_text}
    
    Respond ONLY with the newly updated summary string in Korean."""
    
    try:
        response = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0.0, 
            messages=[{"role": "user", "content": prompt}]
        )
        summary_state["text"] = response.content[0].text.strip()
        print(f"\n🧠 [비서 요약 봇] 문맥 압축 완료: {summary_state['text']}\n", flush=True)
    except Exception as e:
        print(f"Summary Error: {e}", flush=True)

async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str, role: str, name: str):
    had_error = False
    try:
        target_list = normalize_targets(targets)
        if not target_list or is_filler_only(text):
            return

        history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"
        glossary_section = f"\n[CIVIL ENGINEERING GLOSSARY]\n{glossary_text}\n" if glossary_text.strip() else ""
        doc_section = f"\n[REFERENCE DOCUMENT / PAPER CONTEXT]\n{manager.global_document_context}\n" if manager.global_document_context else ""

        if source_lang == "multi" or source_lang == "multi_azure":
            lang_instruction = "The STT engine detected the language automatically. However, cross-check the context."
        else:
            lang_instruction = f"The spoken language is strictly '{source_lang}'."

        # 💡 [핵심 업데이트]: AI가 분석 과정을 화면에 뱉지 않도록 13번 규칙과 33번 규칙, 그리고 출력 포맷 지시를 매우 강력하게 수정했습니다.
        system_prompt = f"""You are an elite simultaneous interpreter for an international Highway Engineering expert symposium involving Korea, China, Japan, and the US.
Domain focus: Highway engineering, specifically Road Pavement and Airport Pavement design, materials (asphalt and concrete), structural evaluation, and distress management technologies.
[PAST CONTEXT SUMMARY]
{summary_state.get('text', 'No summary yet.')}

[RECENT CONTEXT]
{history_str}
{glossary_section}
{doc_section}

CRITICAL INSTRUCTIONS (MUST OBEY):
1. [DOMAIN FORCED ANCHORING]: The absolute core context is 'Road and Airport Pavement Engineering'. All homophones, acronyms, and ambiguous terms MUST be translated exclusively into standard pavement engineering terminology.
2. [NUMERICAL & UNIT IMMUTABILITY]: Numbers, dimensions, and engineering units (e.g., MPa, mm, °C, kg/m³, kN) MUST be preserved exactly as spoken. Convert any colloquial numbers into strict Arabic numerals without spacing before the unit.
3. [STT MEDIA-BIAS CORRECTION]: The STT input may contain media-biased misrecognitions. You MUST logically auto-correct broadcast terms (e.g., "구독자/subscribers", "채널/channel") into academic terms (e.g., "참석자/attendees", "세미나/seminar") based on the academic context.
4. [MATERIAL & DISTRESS SPECIFICITY]: Strictly differentiate between pavement materials (never confuse "Binder" with "Mixture", or "Cement" with "Concrete"). Accurately match specific pavement distresses to rigorous KR/CN/JP/US agency standards (e.g., "Rutting", "Alligator Cracking", "Pothole" for asphalt; "Spalling", "Faulting", "Blowup" for concrete). Do not use generic terms like "damage".
5. [SINGLE DEFINITIVE OUTPUT]: Provide EXACTLY ONE best translation per target language. NEVER use slashes (/) for alternatives or provide multiple options. Be decisive.
6. [PROFESSIONAL SEMINAR TONE]: Maintain a clear, professional, and respectful tone suitable for a live international seminar. Avoid overly rigid or dry academic phrasing, but ensure standard polite forms in Korean (e.g., ~입니다/합니다, ~해요) and Japanese (e.g., です/ます), and standard professional style in Chinese.
7. [OMITTED SUBJECT INFERENCE]: Korean and Japanese speakers often omit subjects. You MUST accurately infer the omitted subject (e.g., "I", "We", "This study", "The pipeline") based on the recent engineering context before translating to English or Chinese.
8. [STRICT NO FABRICATION]: Do not over-infer or complete fragmented sentences. If the STT input is an incomplete fragment (e.g., "Therefore...", "This is..."), translate ONLY the fragment. NEVER fabricate technical facts, verbs, or complete the sentence based on past context.
9. [ACRONYM & REGULATION PRESERVATION]: Internationally recognized civil engineering acronyms (e.g., GPR, IMU) MUST be kept in English capital letters across all outputs. Additionally, keep local country-specific standards (e.g., KS, JIS, GB) in their original names. Do not arbitrarily localize them.
10. [OPERATIONAL PHRASE TRANSLATION & TRIMMING]: NEVER output [SKIP]. You must translate functional meeting phrases (e.g., "마이크 테스트", "다음 슬라이드") without omission. However, drastically trim overly lengthy ceremonial greetings down to their core meaning (e.g., "Thank you for attending") to save display space.
11. [CROSS-LINGUAL CONSISTENCY]: Ensure the core engineering concept remains identical across KR, EN, CN, and JP translations. Use the English standard as the semantic anchor.
12. [GLOSSARY OVERRIDE & HIGHLIGHTING]: If a [REFERENCE DOCUMENT / GLOSSARY] is provided, its terminology ABSOLUTELY OVERRIDES your pre-trained knowledge. Whenever you use a translated term from this glossary, you MUST wrap it in double asterisks (e.g., Flexible Pavement, 소성변형).
13. [STRICT NO CONVERSING - ZERO CHATBOT BEHAVIOR]: You are a passive, mechanical translation pipeline. NEVER act like an AI assistant. NEVER output your reasoning, "CRITICAL CONTEXT ANALYSIS", or any explanations. Output ONLY the pure translated sentence.
14. [COMPLETE TARGET COVERAGE]: Every requested language tag MUST contain a non-empty translation. Never omit a tag and never leave its content blank.
15. [SPEAKER PERSPECTIVE ALIGNMENT]: Maintain the speaker's first-person perspective as the researcher/engineer. Do not translate as a third-party observer.
16. [EQUIPMENT LOCALIZATION]: Translate construction machinery names into industry-standard terms avoiding literal or generic translations.
17. [METHODOLOGY & PROCESS PRESERVATION]: When translating construction methods or experimental procedures, preserve the chronological sequence and causal relationships exactly as spoken.
18. [CULTURAL IDIOM NEUTRALIZATION]: Translate cultural idioms or metaphors into clear, objective engineering statements.
19. [REGIONAL STANDARD AWARENESS]: Be aware that Korea/Japan/China use metric standards, while the US uses imperial. Do not auto-convert units unless specifically instructed, but translate the unit names accurately.
20. [SAFETY & RISK ALERTNESS]: Terms related to construction safety, hazards, or structural failures MUST be translated with absolute clarity and urgency, avoiding any ambiguity.
21. [REAL-TIME SELF-CORRECTION COMPRESSION]: When the speaker instantly corrects a number or word (e.g., "150 degrees... no, 160 degrees"), DO NOT translate the entire erratic process. Extract and translate ONLY the final intended fact ("160 degrees") into a concise sentence.
22. [INLINE DISFLUENCY REMOVAL]: Seamlessly remove meaningless interjections, filler words (e.g., "uh", "um", "you know"), and stutters from the middle of otherwise valid sentences before translating, preserving the academic context.
23. [VISUAL POINTER EXACTNESS]: Phrases pointing to visual presentation materials (e.g., "Looking at the top right of this graph", "The red dashed line") MUST be translated literally without any paraphrasing to synchronize with the audience's visual tracking.
24. [COMPOUND NOUN DISENTANGLEMENT]: Deconstruct heavy compound nouns typical in Korean/Japanese (e.g., "아스팔트포장공용성평가결과") into grammatically natural English/Chinese structures using prepositions and adjectives, rather than awkward direct word-for-word combinations.
25. [DIRECT QUOTATION ISOLATION]: If the speaker directly quotes another paper, a previous speaker, or a specific regulation, strictly enclose the quoted section in quotation marks (" ") to visually separate it from the speaker's own assertions.
26. [PROPER NOUN PHONETIC TRANSLITERATION]: NEVER translate proper nouns (author names, research institutions, regional names) by their literal meanings (e.g., do not translate "광주" as "Light City"). Always transliterate them phonetically.
27. [VERB TENSE STANDARDIZATION]: Auto-correct mixed tenses. Use the 'present tense' for universal engineering facts or conclusions, and the 'past tense' for past experimental procedures or data measurement results.
28. [NATURAL SENTENCE STRUCTURE]: When translating into English, you may use either active or passive voice depending on which sounds more natural and engaging for a live presentation. Prioritize clarity and conversational flow over strict academic passive structures.
29. [INTERROGATIVE CLARIFICATION]: During Q&A, even if a panelist asks a question with a declarative intonation, analyze the context and translate it into a clear interrogative syntactic structure (question marker/format) in the target language.
30. [Q&A EMOTIONAL NEUTRALIZATION]: Even if the STT captures aggressive, emotional, or argumentative vocabulary during debates, absolutely neutralize the tone and translate it into the most dry, objective, and polite academic text.
31. [EQUATION DICTATION FORMATTING]: If the speaker dictates an equation verbally (e.g., "A equals B divided by C squared"), format it into actual mathematical symbols ("A = B / C^2") rather than spelling it out in words.
32. [MODERATOR TRANSITION TAGGING]: Translate the moderator's procedural phrases indicating session transitions (e.g., "Let's welcome the next speaker", "We will now take questions") into the most clear, concise, and action-oriented sentences, preventing them from mixing with academic content.
33. [ZERO META-TALK]: ABSOLUTELY NEVER output your internal reasoning, "CRITICAL CONTEXT ANALYSIS", warnings, or explanations. Any extra words besides the pure translation will critically break the UI system.
{lang_instruction}

Respond EXACTLY in this tag format (DO NOT USE JSON).
ABSOLUTELY NO METADATA, NO REASONING, NO ANALYSIS. JUST THE FINAL TEXT.
[original]
(original text here)
"""

        dynamic_exceptions = inject_exception_prompts(text, EXCEPTIONS_DICT)
        if dynamic_exceptions:
            system_prompt += f"\n\n🚨 IMPORTANT TRANSLATION RULES FOR THIS TURN:\n{dynamic_exceptions}\n"

        translations = {}
        original_text = text
        last_failure_detail = ""
        max_tokens = min(4096, max(1200, 300 + len(text) * 5 + len(target_list) * 350))

        for attempt in range(3):
            missing_targets = [target for target in target_list if target not in translations]
            if not missing_targets:
                break

            attempt_prompt = system_prompt
            for target in missing_targets:
                attempt_prompt += f"[{target}]\n"
            
            allowed_tag_pattern = '|'.join(re.escape(tag) for tag in ['original', *missing_targets])
            response_pattern = re.compile(
                rf'\[({allowed_tag_pattern})\]\s*(.*?)(?=\[(?:{allowed_tag_pattern})\]|$)',
                re.DOTALL | re.IGNORECASE
            )

            buffer = ""
            stop_reason = None
            try:
                async with translation_semaphore:
                    stream = await claude_client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=max_tokens,
                        temperature=0.0,
                        system=attempt_prompt,
                        messages=[{"role": "user", "content": text}],
                        stream=True
                    )

                    async for event in stream:
                        if event.type == "content_block_delta" and getattr(event.delta, "text", None):
                            buffer += event.delta.text
                            matches = response_pattern.finditer(buffer)
                            for match in matches:
                                lang = match.group(1).lower().strip()
                                text_so_far = match.group(2).strip()
                                if lang in missing_targets and text_so_far and "[SKIP]" not in text_so_far.upper():
                                    await manager.broadcast_json({
                                        "type": "stream_update",
                                        "lang": lang,
                                        "text": text_so_far,
                                        "original_text": original_text,
                                        "source_lang": source_lang,
                                        "msg_id": msg_id
                                    })
                        elif event.type == "message_delta":
                            stop_reason = getattr(event.delta, "stop_reason", None)

                parsed = {}
                for match in response_pattern.finditer(buffer):
                    parsed[match.group(1).lower().strip()] = match.group(2).strip()

                if parsed.get('original'):
                    original_text = parsed['original']

                for target in missing_targets:
                    translated = parsed.get(target, '').strip()
                    if translated and "[SKIP]" not in translated.upper():
                        translations[target] = translated

                still_missing = [target for target in target_list if target not in translations]
                if not still_missing:
                    break

                last_failure_detail = f"누락 언어: {', '.join(still_missing)}"
                if stop_reason == "max_tokens":
                    last_failure_detail += f" / 출력 한도 도달({max_tokens} tokens)"
                    max_tokens = min(4096, max_tokens * 2)
            except Exception as attempt_error:
                status_code = getattr(attempt_error, 'status_code', None)
                last_failure_detail = type(attempt_error).__name__
                if status_code:
                    last_failure_detail += f" / HTTP {status_code}"
                print(f"❌ [번역 시도 {attempt + 1}/3 실패]: {attempt_error}", flush=True)

            if attempt < 2:
                await asyncio.sleep(2 ** attempt)

        missing_targets = [target for target in target_list if target not in translations]
        if missing_targets:
            had_error = True
            await manager.broadcast_json({
                "type": "system_issue",
                "key": "translation",
                "code": "번역 결과 누락",
                "message": f"{', '.join(missing_targets)} 번역을 3회 시도했지만 완료하지 못했습니다.",
                "detail": last_failure_detail or "AI 응답이 비어 있습니다.",
                "msg_id": msg_id,
                "targets": missing_targets
            })
        else:
            await manager.broadcast_json({"type": "system_recovered", "key": "translation"})

        if translations:
            recent_history.append(original_text)
            if len(recent_history) >= 5:
                sentences_to_summarize = recent_history[:3]
                del recent_history[:3]
                asyncio.create_task(update_sliding_summary(summary_state, sentences_to_summarize))

        for lang in target_list:
            final_text = translations.get(lang)
            if not final_text:
                continue
            display_final = f"[{'사회자' if role == 'admin' else name}] {final_text}"
            await manager.broadcast_json({
                "type": "stream_end",
                "lang": lang,
                "text": display_final,
                "raw_text": final_text,
                "original_text": original_text,
                "source_lang": source_lang,
                "msg_id": msg_id,
                "role": role,
                "name": '사회자' if role == 'admin' else name
            })
                
    except Exception as e:
        had_error = True
        print(f"❌ [번역 에러 발생]: {e}", flush=True)
        await manager.broadcast_json({
            "type": "system_issue",
            "key": "translation",
            "code": "번역 처리 실패",
            "message": "번역 처리 중 복구할 수 없는 오류가 발생했습니다.",
            "detail": type(e).__name__,
            "msg_id": msg_id
        })
    
    finally:
        await manager.broadcast_json({"type": "sentence_complete"})
        manager.release_floor()
        if not had_error:
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
    endpointing: int = Query(800), 
    max_chars: int = Query(50), 
    glossary: str = Query("") 
):
    if token not in ACTIVE_TOKENS:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if not client_id:
        client_id = secrets.token_hex(4)
    if not name:
        name = f"User_{client_id}"

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
                        except Exception:
                            pass
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
                    
                    await manager.broadcast_json({"type": "status", "text": "🌐 글로벌 다국어 식별 가동 중..."})

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
                                                            try:
                                                                await ws_client.send_json({"type": "speak_approved"})
                                                            except Exception:
                                                                pass
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
                                                            try:
                                                                await ws_client.send_json({"type": "speak_revoked"})
                                                            except Exception:
                                                                pass
                                                elif action == "revoke_all_viewers":
                                                    revoked_ids = list(manager.speaking_allowed_clients)
                                                    manager.speaking_allowed_clients.clear()
                                                    if manager.floor_owner in revoked_ids:
                                                        manager.release_floor()
                                                    await manager.broadcast_user_list()
                                                    for ws_client, info in manager.clients.items():
                                                        if info["id"] in revoked_ids:
                                                            try:
                                                                await ws_client.send_json({"type": "speak_revoked"})
                                                            except Exception:
                                                                pass
                                                elif action == "mute_all":
                                                    manager.is_admin_muted = True
                                                    manager.release_floor()
                                                elif action == "unmute_all":
                                                    manager.is_admin_muted = False
                                                    await manager.broadcast_floor_state()
                                            
                                            elif msg_type == "config":
                                                if role == "admin":
                                                    if "glossary" in msg:
                                                        manager.global_glossary = msg.get("glossary", "")
                                                    if "targets" in msg:
                                                        manager.global_targets = msg.get("targets", manager.global_targets)
                                                    if "rehearsal_mode" in msg:
                                                        manager.is_rehearsal_mode = msg["rehearsal_mode"]
                                                    if "tts_enabled" in msg:
                                                        manager.is_tts_enabled = bool(msg["tts_enabled"])
                                                        await manager.broadcast_display_settings()
                                        
                                        except DowngradeException as de:
                                            raise de 
                                        except Exception:
                                            pass
                        except (websockets.exceptions.ConnectionClosed, WebSocketDisconnect):
                            pass  
                        except DowngradeException as de:
                            raise de
                        except RuntimeError as e:
                            if "disconnect message has been received" in str(e):
                                pass
                            else:
                                print(f"🚨 Azure Sender 런타임 에러: {e}", flush=True)
                        except Exception as e:
                            print(f"🚨 Azure Sender 예외 에러: {e}", flush=True)

                    async def receiver():
                        current_msg_id = secrets.token_hex(4)
                        sentence_start_time = time.time()
                        
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
                                        elapsed_time = time.time() - sentence_start_time
                                        if elapsed_time > 8:
                                            await manager.broadcast_feedback({"type": "speaker_feedback", "code": "pause", "speaker_name": name}, client_id)
                                            sentence_start_time = time.time() 
                                        if len(text) > max_chars:
                                            await manager.broadcast_feedback({"type": "speaker_feedback", "code": "length", "speaker_name": name}, client_id)
                                            
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
                                        sentence_start_time = time.time() 
                        except Exception as e:
                            print(f"🚨 Azure Receiver 에러: {e}", flush=True)

                    try:
                        await run_until_first_complete(sender(), receiver())
                    except DowngradeException:
                        recognizer.stop_continuous_recognition_async()
                        push_stream.close()
                        manager.speaking_allowed_clients.discard(client_id)
                        if manager.floor_owner == client_id:
                            manager.release_floor()
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

                    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={dg_lang}&smart_format=true&interim_results=true&endpointing={endpointing}&utterance_end_ms=1300&vad_events=true{keywords_param}{replace_param}"
                    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

                    ws_kwargs = {}
                    if int(websockets.__version__.split('.')[0]) >= 14:
                        ws_kwargs["additional_headers"] = headers
                    else:
                        ws_kwargs["extra_headers"] = headers

                    async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
                        await manager.broadcast_json({"type": "status", "text": "🚀 첨단 언어 모드 가동 중..."})
                        
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
                                                                try:
                                                                    await ws_client.send_json({"type": "speak_approved"})
                                                                except Exception:
                                                                    pass
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
                                                                try:
                                                                    await ws_client.send_json({"type": "speak_revoked"})
                                                                except Exception:
                                                                    pass
                                                    elif action == "revoke_all_viewers":
                                                        revoked_ids = list(manager.speaking_allowed_clients)
                                                        manager.speaking_allowed_clients.clear()
                                                        if manager.floor_owner in revoked_ids:
                                                            manager.release_floor()
                                                        await manager.broadcast_user_list()
                                                        for ws_client, info in manager.clients.items():
                                                            if info["id"] in revoked_ids:
                                                                try:
                                                                    await ws_client.send_json({"type": "speak_revoked"})
                                                                except Exception:
                                                                    pass
                                                    elif action == "mute_all":
                                                        manager.is_admin_muted = True
                                                        manager.release_floor()
                                                    elif action == "unmute_all":
                                                        manager.is_admin_muted = False
                                                        await manager.broadcast_floor_state()
                                                
                                                elif msg_type == "config":
                                                    if role == "admin":
                                                        if "glossary" in msg:
                                                            manager.global_glossary = msg.get("glossary", "")
                                                        if "targets" in msg:
                                                            manager.global_targets = msg.get("targets", manager.global_targets)
                                                        if "rehearsal_mode" in msg:
                                                            manager.is_rehearsal_mode = msg["rehearsal_mode"]
                                                        if "tts_enabled" in msg:
                                                            manager.is_tts_enabled = bool(msg["tts_enabled"])
                                                            await manager.broadcast_display_settings()
                                            
                                            except DowngradeException as de:
                                                raise de 
                                            except Exception:
                                                pass
                            except (websockets.exceptions.ConnectionClosed, WebSocketDisconnect):
                                pass 
                            except DowngradeException as de:
                                raise de
                            except RuntimeError as e:
                                if "disconnect message has been received" in str(e):
                                    pass
                                else:
                                    print(f"🚨 Deepgram Sender 런타임 에러: {e}", flush=True)
                            except Exception as e:
                                print(f"🚨 Deepgram Sender 예외 에러: {e}", flush=True)

                        async def keep_alive():
                            try:
                                while True:
                                    await asyncio.sleep(4)
                                    await dg_ws.send(json.dumps({"type": "KeepAlive"}))
                            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                                return

                        async def receiver():
                            current_sentence = ""
                            last_translated_text = ""
                            last_translated_at = 0.0
                            current_msg_id = secrets.token_hex(4)
                            sentence_start_time = time.time()

                            async def submit_current_sentence():
                                nonlocal current_sentence, last_translated_text, last_translated_at, current_msg_id, sentence_start_time
                                final_text = current_sentence.strip()
                                if not final_text:
                                    return

                                now = time.time()
                                is_immediate_duplicate = final_text == last_translated_text and now - last_translated_at < 2.0
                                if not is_immediate_duplicate:
                                    last_translated_text = final_text
                                    last_translated_at = now
                                    await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                    asyncio.create_task(translate_and_send(final_text, lang, manager.global_targets, recent_history, summary_state, manager.global_glossary, current_msg_id, role, name))

                                current_sentence = ""
                                current_msg_id = secrets.token_hex(4)
                                sentence_start_time = now

                            try:
                                while True:
                                    try:
                                        dg_result = await asyncio.wait_for(dg_ws.recv(), timeout=1.5)
                                    except asyncio.TimeoutError:
                                        await submit_current_sentence()
                                        continue

                                    dg_json = json.loads(dg_result)
                                    event_type = dg_json.get("type")

                                    if event_type == "UtteranceEnd":
                                        await submit_current_sentence()
                                        continue
                                    if event_type == "Error":
                                        detail = f"{dg_json.get('err_code', 'Deepgram error')}: {dg_json.get('description', '')}".strip()
                                        await manager.broadcast_json({
                                            "type": "system_issue",
                                            "key": "stt",
                                            "code": "STT 연결 오류",
                                            "message": "음성 인식 서버 연결이 종료되어 브라우저가 자동 재연결합니다.",
                                            "detail": detail
                                        })
                                        raise RuntimeError(detail)
                                    if event_type != "Results":
                                        continue

                                    is_final = dg_json.get("is_final", False)
                                    speech_final = dg_json.get("speech_final", False)
                                    alternative = dg_json.get("channel", {}).get("alternatives", [{}])[0]
                                    transcript = alternative.get("transcript", "").strip()
                                    confidence = alternative.get("confidence", 1.0)

                                    if transcript:
                                        if manager.floor_owner is None and not manager.is_admin_muted and role != "admin":
                                            manager.set_floor(client_id)
                                        if role != "admin" and manager.floor_owner != client_id:
                                            continue

                                    if transcript or current_sentence:
                                        display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                        current_targets_list = manager.global_targets.split(',')
                                        tag = f"[{name}] "
                                        elapsed_time = time.time() - sentence_start_time

                                        if confidence > 0 and confidence < 0.6:
                                            await manager.broadcast_feedback({"type": "speaker_feedback", "code": "mic", "speaker_name": name}, client_id)
                                        elif elapsed_time > 8 and len(current_sentence) > 20:
                                            await manager.broadcast_feedback({"type": "speaker_feedback", "code": "pause", "speaker_name": name}, client_id)
                                            sentence_start_time = time.time()
                                        elif len(display_text) > max_chars:
                                            await manager.broadcast_feedback({"type": "speaker_feedback", "code": "length", "speaker_name": name}, client_id)

                                        await manager.broadcast_json({
                                            "type": "interim",
                                            "text": tag + display_text.strip(),
                                            "targets": current_targets_list,
                                            "msg_id": current_msg_id
                                        })

                                    if is_final and transcript:
                                        current_sentence = f"{current_sentence} {transcript}".strip()
                                        elapsed_time = time.time() - sentence_start_time
                                        if elapsed_time > 0 and (len(transcript) / elapsed_time) > 13:
                                            await manager.broadcast_feedback({"type": "speaker_feedback", "code": "speed", "speaker_name": name}, client_id)

                                    is_semantic_end = current_sentence.endswith(('.', '?', '!'))
                                    if speech_final or len(current_sentence) > max_chars or is_semantic_end:
                                        await submit_current_sentence()
                            except websockets.exceptions.ConnectionClosed as e:
                                await manager.broadcast_json({
                                    "type": "system_issue",
                                    "key": "stt",
                                    "code": "STT 연결 종료",
                                    "message": "음성 인식 연결이 종료되어 자동 복구를 시작합니다.",
                                    "detail": f"Deepgram code={e.code} reason={e.reason}"
                                })
                                raise

                        try:
                            await run_until_first_complete(sender(), receiver(), keep_alive())
                        except DowngradeException:
                            manager.speaking_allowed_clients.discard(client_id)
                            if manager.floor_owner == client_id:
                                manager.release_floor()
                            await manager.broadcast_user_list()
                            continue 
                break 
    except (websockets.exceptions.ConnectionClosed, WebSocketDisconnect):
        pass 
    except RuntimeError as e:
        if "disconnect message has been received" in str(e):
            pass
        else:
            print(f"🚨 전체 웹소켓 런타임 에러: {e}", flush=True)
    except Exception as e:
        print(f"🚨 전체 웹소켓 연결 에러: {e}", flush=True)
    finally:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    port = int(os.environ.get("PORT", 10000)) 
    print(f"🚀 실시간 글로벌 통역 서버 (Hybrid 엔진)를 시작합니다... (Port: {port})")
    uvicorn.run(app, host="0.0.0.0", port=port)