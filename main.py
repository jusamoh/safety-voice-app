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
# 💰 2. 유료 사용자 DB & 인증 (SaaS 기능)
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

# ==========================================
# 🌐 3. 라우팅 및 웹소켓 관리자
# ==========================================
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
# ⚡ 4. 다국적 웹소켓 파이프라인 (STT 연동)
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

    try:
        if role == "viewer":
            while True:
                data = await websocket.receive()
                if "bytes" in data:
                    print(f"📩 [현장 보고] 노동자로부터 위험 보고 수신됨")
        else:
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}"
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

            async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
                async def sender():
                    nonlocal glossary_text 
                    try:
                        while True:
                            message = await websocket.receive()
                            if message.get("type") == "websocket.receive":
                                if message.get("bytes"):
                                    await dg_ws.send(message.get("bytes"))
                                elif message.get("text"):
                                    try:
                                        config = json.loads(message.get("text"))
                                        if config.get("type") == "config":
                                            glossary_text = config.get("glossary", "")
                                            print(f"✅ [용어집 수신 완료] {len(glossary_text)}자")
                                    except:
                                        pass
                    except Exception as e:
                        print(f"🚨 [에러] 오디오 전송 중단: {e}")

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
                                await manager.broadcast_json({"type": "interim", "text": transcript})

                                if is_final: current_sentence += " " + transcript

                                # 💡 완벽하게 조율된 들여쓰기 위치 (마침표/물음표 감지)
                                is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                                if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                    final_text = current_sentence.strip()
                                    current_sentence = ""  
                                    await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                    asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text))
                    except Exception as e:
                        print(f"🚨 [에러] 딥그램 수신 중단: {e}")
                
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 에러 발생: {e}")
    finally:
        manager.disconnect(websocket)

# ==========================================
# 🧠 5. LLM 번역 및 안전 경고 로직 (Claude 3.5 Haiku)
# ==========================================
async def translate_and_send(text: str, source_lang: str, targets: str, context_memory: list, glossary_text: str):
    
    if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰", "대피"]):
        await manager.broadcast_json({"type": "alert"})
        print(f"🚨 [경고 발송] 스마트폰 점멸 트리거 작동 (원인: '{text}')")

    ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어", "그", "예", "네"]
    if not text or len(text) < 2 or text.lower() in ignore_words:
        await manager.broadcast_json({"type": "status", "text": "✅ 방송 중...", "role": "speaker"})
        return

    history_str = "\n".join([f"- {past}" for past in context_memory]) if context_memory else "대화의 시작입니다. (No previous context)"
    glossary_section = f"\n[MEETING GLOSSARY / DOMAIN KNOWLEDGE]\n{glossary_text}\n" if glossary_text.strip() else ""

    system_prompt = f"""
    You are a top-tier professional simultaneous interpreter. The spoken language is '{source_lang}'.
    
    [PAST CONTEXT]
    {history_str}
    {glossary_section}
    [CURRENT SENTENCE TO TRANSLATE]
    {text}
    
    CRITICAL INSTRUCTIONS:
    1. Translate ONLY the CURRENT SENTENCE into the target language codes: {targets}.
    2. Respond EXACTLY and ONLY in this JSON format. Do not add any text before or after the JSON.
    {{"original": "clean current sentence", "translations": {{"lang_code_1": "result", "lang_code_2": "result"}}}}
    """
    
    try:
        # 🔥 회원님께서 확인해주신 현존 최신 모델 Claude 4.5 Haiku 적용 완료
        response = await claude_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                temperature=0.2, # JSON 형식을 엄격하게 지키도록 온도 낮춤
                system=system_prompt, 
                messages=[{"role": "user", "content": text}]
        )

        output = response.content[0].text.strip()
        
        # JSON 데이터 추출 방어 로직 강화
        json_match = re.search(r'\{.*\}', output, re.DOTALL)
        if not json_match: 
            print(f"⚠️ [JSON 파싱 에러] AI 응답: {output}")
            raise ValueError("JSON format not found in AI response")
            
        result = json.loads(json_match.group(0))
        
        context_memory.append(result['original'])
        if len(context_memory) > 3: context_memory.pop(0)
            
        await manager.broadcast_json({"type": "translation", "data": result, "source_lang": source_lang})
        await manager.broadcast_json({"type": "status", "text": "✅ 방송 중...", "role": "speaker"})
        
    except BaseException as e:
        print(f"🚨 Translation Error: {e}")
        # 에러가 발생해도 화면이 영원히 먹통되지 않도록 강제로 대기 상태로 복구
        await manager.broadcast_json({"type": "status", "text": "✅ 방송 중...", "role": "speaker"})

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    print("🚀 실시간 글로벌 현장 안전 통역 서버를 시작합니다... (http://0.0.0.0:8000)")
    uvicorn.run(app, host="0.0.0.0", port=8000)