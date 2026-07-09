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
            print(f"❌ [인증 실패] ID: {user_id}, PW: {password}", flush=True)
            return JSONResponse(content={"success": False, "message": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)
    except Exception as e:
        return JSONResponse(content={"success": False, "message": f"로그인 에러: {str(e)}"}, status_code=400)

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
        print("❌ [보안 경고] 유효하지 않은 토큰으로 통역 서버 접근 시도 차단됨", flush=True)
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await manager.connect(websocket)
    context_memory = [] 
    glossary_text = ""
    dynamic_targets = targets 

    try:
        if role == "viewer":
            while True:
                data = await websocket.receive()
                if "bytes" in data:
                    print(f"📩 [현장 보고] 노동자로부터 위험 보고 수신됨", flush=True)
        else:
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

            async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
                async def sender():
                    nonlocal glossary_text, dynamic_targets 
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
                                            if "glossary" in config:
                                                glossary_text = config.get("glossary", "")
                                            if "targets" in config:
                                                dynamic_targets = config.get("targets", dynamic_targets)
                                                print(f"🔄 [타겟 언어 실시간 변경됨] 현재 타겟: {dynamic_targets}", flush=True)
                                    except:
                                        pass
                    except:
                        pass

                async def receiver():
                    current_sentence = ""
                    last_translated_text = ""
                    try:
                        while True:
                            dg_result = await dg_ws.recv()
                            dg_json = json.loads(dg_result)
                            
                            if dg_json.get("type") == "Metadata": continue

                            is_final = dg_json.get("is_final", False)
                            speech_final = dg_json.get("speech_final", False)
                            transcript = dg_json.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()
                            
                            if transcript:
                                current_sentence = transcript

                            if current_sentence.strip():
                                # 💡 뷰어들에게 현재 방송 중인 언어 목록을 함께 넘겨줍니다.
                                current_targets_list = dynamic_targets.split(',') if isinstance(dynamic_targets, str) else dynamic_targets
                                await manager.broadcast_json({
                                    "type": "interim", 
                                    "text": current_sentence,
                                    "targets": current_targets_list
                                })

                            if is_final: current_sentence += " " + transcript

                            is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                            if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                final_text = current_sentence.strip()
                                
                                if final_text == last_translated_text:
                                    current_sentence = ""
                                    continue
                                
                                last_translated_text = final_text
                                current_sentence = ""  
                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                asyncio.create_task(translate_and_send(final_text, lang, dynamic_targets, context_memory, glossary_text))
                    except:
                        pass
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 에러 발생: {e}", flush=True)
    finally:
        manager.disconnect(websocket)

# ==========================================
# 🧠 5. LLM 번역 및 안전 경고 로직 (Claude 4.5 Haiku)
# ==========================================
async def translate_and_send(text: str, source_lang: str, targets: str, context_memory: list, glossary_text: str):
    
    if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
        await manager.broadcast_json({"type": "alert"})
        print(f"🚨 [경고 발송] 스마트폰 점멸 트리거 작동 (원인: '{text}')", flush=True)

    ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어"]
    if not text or len(text) < 2 or text.lower() in ignore_words:
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
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
                model="claude-haiku-4-5-20251001",
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
            
        await manager.broadcast_json({"type": "translation", "data": result, "source_lang": source_lang})
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
        
    except Exception as e:
        print(f"Translation Error: {e}", flush=True)
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    print("🚀 실시간 글로벌 현장 안전 통역 서버를 시작합니다... (http://0.0.0.0:10000)")
    uvicorn.run(app, host="0.0.0.0", port=10000)