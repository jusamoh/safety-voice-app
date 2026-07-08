import asyncio
import json
import os
import sys
import re  
import secrets 
from datetime import datetime
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from anthropic import AsyncAnthropic

# ==========================================
# 💡 1. API 키 로드 및 안전장치
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

if DEEPGRAM_API_KEY.startswith("여기에") or ANTHROPIC_API_KEY.startswith("여기에"):
    print("❌ 오류: main.py 파일 내부에 실제 API 키를 입력해주세요.")
    sys.exit(1)

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
# 💰 2. 유료 사용자 DB & 인증
# ==========================================
USER_DB = {
    "admin": "1234",          
    "samsung": "sam1234",     
    "hyundai": "hdec1234"     
}

ACTIVE_TOKENS = {}

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
async def login(req: LoginRequest):
    if req.username in USER_DB and USER_DB[req.username] == req.password:
        token = secrets.token_hex(16)
        ACTIVE_TOKENS[token] = req.username
        print(f"🔐 [인증 성공] 사용자 '{req.username}' 로그인 (토큰 발급됨)")
        return {"success": True, "token": token, "username": req.username}
    return {"success": False, "message": "아이디 또는 비밀번호가 올바르지 않습니다."}

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

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

# ==========================================
# ⚡ 4. 양방향 무전 지원 웹소켓 파이프라인
# ==========================================
@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    token: str = Query(None), 
    lang: str = Query("ko"), 
    targets: str = Query("ko,id"), 
    role: str = Query("speaker"),
    endpointing: int = Query(700), 
    max_chars: int = Query(50)    
):
    if token not in ACTIVE_TOKENS:
        print("❌ [보안 경고] 유효하지 않은 토큰으로 통역 서버 접근 시도 차단됨")
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await manager.connect(websocket)
    context_memory = [] 
    glossary_text = ""

    # 양방향 통신 지원을 위해 모두가 각자의 언어(lang)로 Deepgram에 연결 준비를 합니다.
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    
    dg_ws = None
    receiver_task = None

    try:
        # 💡 비용 최적화: 마이크(PTT) 버튼을 눌러서 오디오 데이터가 최초로 들어올 때만 Deepgram 엔진을 켭니다.
        async def ensure_dg_connection():
            nonlocal dg_ws, receiver_task
            if dg_ws is None:
                dg_ws = await websockets.connect(dg_url, extra_headers=headers)
                receiver_task = asyncio.create_task(receiver())
                print(f"🎙️ [{role}] 오디오 스트리밍 시작 (언어: {lang}) -> 딥그램 엔진 가동")

        async def sender():
            nonlocal glossary_text
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.receive":
                        if message.get("bytes"):
                            await ensure_dg_connection()
                            await dg_ws.send(message.get("bytes"))
                        elif message.get("text"):
                            try:
                                config = json.loads(message.get("text"))
                                if config.get("type") == "config":
                                    glossary_text = config.get("glossary", "")
                            except: pass
            except BaseException:
                pass

        async def receiver():
            current_sentence = ""
            try:
                while True:
                    dg_result = await dg_ws.recv()
                    dg_json = json.loads(dg_result)
                    
                    if dg_json.get("type") == "Results":
                        is_final = dg_json.get("is_final", False)
                        speech_final = dg_json.get("speech_final", False)
                        transcript = dg_json.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()
                        
                        if not transcript: continue
                        
                        # 화자 정보(role)를 꼬리표로 달아서 브로드캐스팅합니다.
                        await manager.broadcast_json({
                            "type": "interim", 
                            "text": transcript,
                            "role": role,
                            "source_lang": lang
                        })

                        if is_final: current_sentence += " " + transcript

                        is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                        if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                            final_text = current_sentence.strip()
                            current_sentence = ""  
                            
                            if role == "speaker":
                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중...", "role": role})
                            
                            asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text, role))
            except BaseException:
                pass

        await sender()
        
    except Exception as e:
        print(f"🚨 웹소켓 처리 에러: {e}")
    finally:
        if receiver_task: receiver_task.cancel()
        if dg_ws: await dg_ws.close()
        manager.disconnect(websocket)

# ==========================================
# 🧠 5. LLM 번역 로직 (누가 말했는지 Role 추가)
# ==========================================
async def translate_and_send(text: str, source_lang: str, targets: str, context_memory: list, glossary_text: str, role: str):
    
    if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
        await manager.broadcast_json({"type": "alert"})
        print(f"🚨 [경고 발송] 스마트폰 점멸 트리거 작동 (원인: '{text}')")

    ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어"]
    if not text or len(text) < 2 or text.lower() in ignore_words:
        return

    history_str = "\n".join([f"- {past}" for past in context_memory]) if context_memory else "대화의 시작입니다. (No previous context)"
    glossary_section = f"\n[MEETING GLOSSARY / DOMAIN KNOWLEDGE]\n{glossary_text}\n" if glossary_text.strip() else ""

    system_prompt = f"""
    You are a top-tier professional simultaneous interpreter. The spoken language is '{source_lang}'.
    
    [PAST CONTEXT (For understanding only, DO NOT translate this)]
    {history_str}
    {glossary_section}
    [CURRENT SENTENCE TO TRANSLATE]
    {text}
    
    CRITICAL INSTRUCTIONS:
    1. Deeply analyze the PAST CONTEXT. Use it to accurately infer missing subjects.
    2. If [MEETING GLOSSARY] is provided, STRICTLY USE the domain-specific terms mapped.
    3. Fix any STT typos in the CURRENT SENTENCE.
    4. Translate ONLY the CURRENT SENTENCE into the target language codes: {targets}.
    5. If a target language code matches '{source_lang}', return the clean 'original' text without translating.
    
    Respond EXACTLY in this JSON format:
    {{"original": "clean current sentence", "translations": {{"lang_code_1": "result", "lang_code_2": "result"}}}}
    """
    try:
        response = await claude_client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=500,
                system=system_prompt, 
                messages=[{"role": "user", "content": text}]
        )

        output = response.content[0].text.strip()
        json_match = re.search(r'\{.*\}', output, re.DOTALL)
        if not json_match: raise ValueError("JSON format not found")
            
        result = json.loads(json_match.group(0))
        
        context_memory.append(result['original'])
        if len(context_memory) > 3: context_memory.pop(0)
            
        await manager.broadcast_json({
            "type": "translation", 
            "data": result, 
            "source_lang": source_lang,
            "role": role # 화자가 누구인지 꼬리표 전송
        })
        
        if role == "speaker":
            await manager.broadcast_json({"type": "status", "text": "✅ 방송 대기 중...", "role": role})
        
    except Exception as e:
        print(f"Translation Error: {e}")

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    print("🚀 실시간 양방향 글로벌 안전 무전기 서버 시작... (http://0.0.0.0:8000)")
    uvicorn.run(app, host="0.0.0.0", port=8000)