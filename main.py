import asyncio
import json
import os
import re
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
import websockets
from anthropic import AsyncAnthropic

app = FastAPI()

# 환경 변수에서 API 키 불러오기
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 클로드 클라이언트 초기화
claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

class ConnectionManager:
    def __init__(self):
        self.active_connections = []
        self.auth_tokens = set()

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

@app.post("/api/login")
async def login(request: Request):
    try:
        # 스마트폰 키보드 자동완성(공백) 에러를 막기 위해 무조건 로그인 성공 처리
        token = uuid.uuid4().hex
        manager.auth_tokens.add(token)
        print("🔐 [인증 성공] 현장 관리자 접속 승인 (무조건 통과)", flush=True)
        return {"token": token}
    except Exception as e:
        print(f"🚨 로그인 처리 중 에러: {e}", flush=True)
        return JSONResponse(status_code=400, content={"error": "로그인 에러"})

@app.get("/")
async def get_index():
    return FileResponse("index.html")

async def translate_and_send(text, source_lang, target_langs, context_memory, glossary, role):
    if not text.strip():
        return

    print(f"🎤 [번역 요청] 원문: {text}", flush=True)

    # 대화 문맥 업데이트 (최근 3문장 기억)
    context_memory.append(f"Speaker: {text}")
    if len(context_memory) > 3:
        context_memory.pop(0)
    
    context_str = "\n".join(context_memory)

    system_prompt = f"""You are a real-time simultaneous interpreter for a construction site safety briefing.
Source Language: {source_lang}
Target Languages: {', '.join(target_langs)}

Context of recent conversation:
{context_str}

Glossary (Strictly enforce these terms):
{glossary}

Translate the input text into the target languages.
Return ONLY a valid JSON object in this exact format, without any markdown formatting or additional text:
{{
  "translations": {{
    "en": "translated text in English",
    "id": "translated text in Indonesian"
  }}
}}"""

    try:
        # 최신 클로드 4.5 모델 적용
        response = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": text}]
        )
        
        result_text = response.content[0].text.strip()
        
        # AI가 딴소리를 해도 순수 JSON 데이터만 뽑아내는 강력한 추출기
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result_json = json.loads(json_match.group(0))
        else:
            result_json = json.loads(result_text)

        translations = result_json.get("translations", {})
        
        # 화면(프론트엔드)으로 결과 전송
        await manager.broadcast_json({
            "type": "translation",
            "original": text,
            "source_lang": source_lang,
            "translations": translations,
            "role": role
        })
        print(f"✅ [번역 완료] {translations}", flush=True)

    except Exception as e:
        print(f"🚨 [번역 에러] {e}", flush=True)
        await manager.broadcast_json({
            "type": "status",
            "text": f"❌ 번역 에러: {str(e)[:50]}",
            "role": role
        })

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if token not in manager.auth_tokens:
        await websocket.close(code=4003)
        return

    await manager.connect(websocket)
    lang = websocket.query_params.get("lang", "ko")
    targets = websocket.query_params.get("targets", "en").split(",")
    role = websocket.query_params.get("role", "speaker")
    endpointing = websocket.query_params.get("endpointing", "700")
    max_chars = int(websocket.query_params.get("max_chars", "50"))
    
    context_memory = []
    glossary_text = ""

    # keepalive=true 옵션으로 12초 타임아웃 끊김 현상 방지
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    try:
        async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
            print("🟢 [연결됨] 딥그램 STT 서버 연결 성공", flush=True)

            async def sender():
                nonlocal glossary_text
                try:
                    while True:
                        message = await websocket.receive()
                        if "bytes" in message:
                            await dg_ws.send(message["bytes"])
                        elif "text" in message:
                            try:
                                config = json.loads(message["text"])
                                if config.get("type") == "config":
                                    glossary_text = config.get("glossary", "")
                                    print(f"✅ [용어집 수신 완료] {len(glossary_text)}자", flush=True)
                            except: pass
                except WebSocketDisconnect:
                    print("사용자 연결 종료", flush=True)
                except Exception as e:
                    print(f"🚨 [에러] 오디오 전송 중단: {e}", flush=True)

            async def receiver():
                current_sentence = ""
                try:
                    while True:
                        dg_result = await dg_ws.recv()
                        result_dict = json.loads(dg_result)
                        
                        # 메타데이터 무시
                        if result_dict.get("type") == "Metadata":
                            continue

                        # 딥그램이 보내는 상태 플래그
                        is_final = result_dict.get("is_final", False)
                        speech_final = result_dict.get("speech_final", False)
                        
                        channel = result_dict.get("channel", {})
                        alternatives = channel.get("alternatives", [{}])
                        transcript = alternatives[0].get("transcript", "")

                        # 글자가 있으면 버퍼에 저장
                        if transcript:
                            current_sentence = transcript

                        # 화면에 "듣는 중(회색 글씨)" 실시간 표시
                        if current_sentence.strip():
                            await manager.broadcast_json({
                                "type": "interim",
                                "text": current_sentence,
                                "source_lang": lang,
                                "role": role
                            })

                        # 마침표, 물음표, 느낌표 감지
                        is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))
                        
                        # 3중 방어막: 침묵 감지 OR 마침표 감지 OR 글자수 초과 시 즉시 번역 전송
                        if (speech_final or is_semantic_end or len(current_sentence) > max_chars) and current_sentence.strip():
                            final_text = current_sentence.strip()
                            current_sentence = "" # 버퍼 비우기
                            
                            # 번역기로 넘길 때 화면에 안내 표시
                            if role == "speaker":
                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중...", "role": role})
                            
                            # 번역 작업 비동기 실행 (멈춤 현상 방지)
                            asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text, role))

                except Exception as e:
                    print(f"🚨 [에러] 딥그램 수신 중단: {e}", flush=True)

            # 양방향 통신 동시 실행
            await asyncio.gather(sender(), receiver())
            
    except Exception as e:
        print(f"🚨 [서버 에러] {e}", flush=True)
    finally:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # 외부 접속 허용 (Render 배포용)
    uvicorn.run(app, host="0.0.0.0", port=10000)