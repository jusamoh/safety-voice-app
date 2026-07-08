import os
import json
import asyncio
import re
from fastapi import FastAPI, WebSocket, Query
from fastapi.responses import FileResponse
import websockets
from anthropic import AsyncAnthropic

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
        self.auth_tokens = set()

    async def connect(self, websocket: WebSocket, role: str):
        await websocket.accept()
        self.active_connections[websocket] = role

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]

    async def broadcast_json(self, message: dict):
        for connection in list(self.active_connections.keys()):
            try:
                await connection.send_json(message)
            except:
                self.disconnect(connection)

manager = ConnectionManager()
claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
context_memory = []

@app.post("/api/login")
async def login():
    import uuid
    token = uuid.uuid4().hex
    manager.auth_tokens.add(token)
    print(f"🔐 [인증 성공] 사용자 'admin' 로그인 (토큰 발급됨)", flush=True)
    return {"token": token}

@app.get("/")
async def get():
    return FileResponse("index.html")

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
        
        # 💡 [정규식 파싱] 불필요한 설명을 덧붙여도 순수 JSON만 강제 추출
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            result_json = json.loads(match.group(0))
        else:
            raise ValueError(f"JSON 파싱 실패: {raw_text}")
        
        context_memory.append(text)
        if len(context_memory) > 3: context_memory.pop(0)

        await manager.broadcast_json({
            "type": "translation",
            "original": text,
            "translations": result_json.get("translations", {}),
            "source_lang": source_lang,
            "role": role
        })
        
        await manager.broadcast_json({"type": "status", "text": "방송 중...", "role": role})

    except Exception as e:
        print(f"🚨 Translation Error: {e}", flush=True)
        await manager.broadcast_json({"type": "status", "text": "❌ 번역 에러 (로그 확인)", "role": role})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(None), lang: str = Query("ko"), targets: str = Query("ko"), role: str = Query("viewer"), endpointing: int = Query(700), max_chars: int = Query(50)):
    if token not in manager.auth_tokens:
        print("❌ [보안 경고] 유효하지 않은 토큰으로 접근 차단됨", flush=True)
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, role)
    
    if role == "viewer":
        try:
            while True:
                await websocket.receive_text()
        except:
            manager.disconnect(websocket)
        return

    print(f"🎙️ [{role}] 오디오 스트리밍 시작 (언어: {lang}) -> 딥그램 엔진 가동", flush=True)
    
    # 💡 [keepalive 옵션 추가] 12초 타임아웃 방지
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    glossary_text = ""

    try:
        async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
            
            async def sender():
                nonlocal glossary_text 
                audio_packet_count = 0
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.receive":
                            if message.get("bytes"):
                                audio_packet_count += 1
                                if audio_packet_count % 20 == 0:
                                    print(f"🎵 [정상] 오디오 수신 중... ({audio_packet_count} 패킷)", flush=True)
                                await dg_ws.send(message.get("bytes"))
                            elif message.get("text"):
                                try:
                                    config = json.loads(message.get("text"))
                                    if config.get("type") == "config":
                                        glossary_text = config.get("glossary", "")
                                        print(f"✅ [용어집 수신 완료] {len(glossary_text)}자", flush=True)
                                except: pass
                except Exception as e:
                    print(f"🚨 오디오 전송 중단: {e}", flush=True)

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

                            if is_final: current_sentence += " " + transcript

                            # 💡 [문맥 기반 절단] 들여쓰기 위치 완벽 교정 완료
                            is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                            if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                final_text = current_sentence.strip()
                                current_sentence = ""  
                                print(f"✅ [문장 절단 감지] 번역기 전송: {final_text}", flush=True)
                                await manager.broadcast_json({"type": "status", "text": "다국어 번역 중...", "role": role})
                                asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text, role))
                except Exception as e:
                    print(f"🚨 딥그램 수신 중단: {e}", flush=True)
            
            await asyncio.gather(sender(), receiver())
            
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 연결 에러: {e}", flush=True)
    finally:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)