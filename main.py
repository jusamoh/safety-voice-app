import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import websockets
from anthropic import AsyncAnthropic

# ==========================================
# ⚡ 1. API 키 및 기본 설정 (보안 환경변수)
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# ==========================================
# ⚡ 2. 웹소켓 매니저 (다중 접속 처리)
# ==========================================
ACTIVE_TOKENS = set()

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
# ⚡ 3. 기본 라우팅 및 로그인
# ==========================================
class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
async def login(req: LoginRequest):
    if req.username == "admin" and req.password == "1234":
        import uuid
        token = str(uuid.uuid4().hex)
        ACTIVE_TOKENS.add(token)
        print(f"🔐 [인증 성공] 사용자 'admin' 로그인 (토큰 발급됨)")
        return {"success": True, "token": token}
    return {"success": False}

@app.get("/")
async def get_index():
    return FileResponse("index.html")

# ==========================================
# ⚡ 4. 클로드 4.5 다국어 번역 로직
# ==========================================
async def translate_and_send(text: str, source_lang: str, targets: str, context_memory: list, glossary: str, role: str):
    target_list = [t.strip() for t in targets.split(",") if t.strip()]
    if not target_list: return

    context_str = " ".join(context_memory[-3:])
    system_prompt = f"""You are a professional construction site safety interpreter.
    Glossary (Must strictly follow): {glossary}
    Context of previous sentences: {context_str}
    Translate the following '{source_lang}' text into {', '.join(target_list)}.
    Return ONLY a valid JSON object without any markdown formatting or extra text.
    Format: {{"translations": {{"lang_code": "translated text"}}}}"""

    try:
        response = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": text}]
        )
        
        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```json"): raw_text = raw_text.split("```json")[1]
        if raw_text.endswith("```"): raw_text = raw_text.rsplit("```", 1)[0]
        raw_text = raw_text.strip()
        
        result_json = json.loads(raw_text)
        
        context_memory.append(text)
        if len(context_memory) > 3: context_memory.pop(0)

        await manager.broadcast_json({
            "type": "translation",
            "original": text,
            "translations": result_json.get("translations", {}),
            "source_lang": source_lang,
            "role": role
        })

    except Exception as e:
        print(f"🚨 Translation Error: {e}")

# ==========================================
# ⚡ 5. 다국적 웹소켓 파이프라인 (STT 연동)
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
    if token not in ACTIVE_TOKENS and role == "speaker":
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
        else:
            # 🌟 딥그램 타임아웃 방지 옵션 (keepalive=true) 추가!
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

            async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
                
                async def sender():
                    nonlocal glossary_text 
                    audio_packet_count = 0 # 🌟 오디오 패킷 수신 추적기
                    try:
                        while True:
                            message = await websocket.receive()
                            if message.get("type") == "websocket.receive":
                                if message.get("bytes"):
                                    audio_packet_count += 1
                                    # 약 1초마다 로그 출력 (패킷 20개당 1번)
                                    if audio_packet_count % 20 == 0:
                                        print(f"🎵 [정상] 브라우저로부터 오디오 데이터 수신 중... ({audio_packet_count} 패킷)")
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
                                await manager.broadcast_json({"type": "interim", "text": transcript, "role": role, "source_lang": lang})

                                # 💡 완벽하게 조율된 들여쓰기 위치 (마침표/물음표 감지)
                                if is_final: current_sentence += " " + transcript

                                is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                                if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                    final_text = current_sentence.strip()
                                    current_sentence = ""  
                                    await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중...", "role": role})
                                    asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text, role))
                    except Exception as e:
                        print(f"🚨 [에러] 딥그램 수신 중단: {e}")
                
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 에러 발생: {e}")
    finally:
        manager.disconnect(websocket)