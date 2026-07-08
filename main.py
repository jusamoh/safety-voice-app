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

# 클로드 클라이언트 초기화 (최신 3.5 모델)
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

@app.get("/")
async def get_index():
    # 프론트엔드 HTML 파일 제공
    return FileResponse("index.html")

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        user_id = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()

        # 🚨 [만능 로그인] 무엇을 입력하든 무조건 프리패스 통과!
        token = uuid.uuid4().hex
        manager.auth_tokens.add(token)
        print(f"🔐 [인증 성공] 사용자 '{user_id}' 로그인 (토큰 발급됨)", flush=True)
        # 프론트엔드가 요구하는 'success' 키워드로 명확히 응답
        return JSONResponse(content={"success": True, "token": token})
    except Exception as e:
        return JSONResponse(content={"success": False, "message": f"로그인 에러: {str(e)}"}, status_code=400)

async def translate_and_send(text: str, source_lang: str, targets: list, context_memory: list, glossary_text: str, role: str):
    # 클로드에게 번역 언어를 절대 빼먹지 말라고 강력하게 지시
    system_prompt = f"""
    You are a real-time translator for construction safety.
    The source text is in '{source_lang}'.
    Target languages to translate into: {targets}.
    
    Glossary: {glossary_text}
    
    CRITICAL INSTRUCTION: You MUST return ONLY a JSON object. 
    You MUST include EVERY single language code from the target languages list as a key in the 'translations' dictionary. Do NOT omit any language.
    Format:
    {{
        "translations": {{
            "ko": "...",
            "en": "...",
            "id": "...",
            "vi": "..."
        }}
    }}
    """
    
    try:
        # 합의된 빠르고 강력한 최신 모델 사용
        response = await claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
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
        
        # 🚨 [치명적 버그 해결] 프론트엔드 에러 방어막
        # 클로드가 타겟 언어를 빼먹고 주면, 화면(JS) 코드가 뻗어버리므로 원문으로 강제로 빈칸을 메워줍니다.
        for t in targets:
            if t not in translations or not translations[t]:
                translations[t] = text

        # 화면(프론트엔드)으로 결과 전송
        await manager.broadcast_json({
            "type": "translation",
            "original": text,
            "source_lang": source_lang,
            "translations": translations,
            "role": role
        })
        print(f"✅ [번역 완료] {translations}", flush=True)
        
        # 버튼을 20초 동안 멈추게 했던 증상 해결: 번역 완료 후 버튼 상태 강제 초기화
        if role == "speaker":
            await manager.broadcast_json({"type": "status", "text": "✅ 번역 완료 (대기 중)", "role": role})

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
    last_translated_text = ""  # 🚨 중복 번역 방지용 변수 추가

    # keepalive=true 옵션으로 12초 타임아웃 끊김 현상 방지
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    try:
        async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
            print("🟢 [연결됨] 딥그램 STT 서버 연결 성공", flush=True)

            async def sender():
                nonlocal glossary_text
                try:
                    packet_count = 0
                    while True:
                        message = await websocket.receive()
                        if "bytes" in message:
                            await dg_ws.send(message["bytes"])
                            packet_count += 1
                            if packet_count % 50 == 0:
                                print(f"🎵 [정상] 브라우저로부터 오디오 데이터 수신 중... ({packet_count} 패킷)", flush=True)
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
                nonlocal last_translated_text
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
                        
                        # 3중 방어막: 침묵 감지 OR 마침표 감지 OR 완전 종료 플래그 OR 글자수 초과
                        if (speech_final or is_semantic_end or is_final or len(current_sentence) > max_chars) and current_sentence.strip():
                            final_text = current_sentence.strip()
                            
                            # 🚨 [중복 요청 방어막] 방금 번역한 똑같은 문장이면 무시하고 버림
                            if final_text == last_translated_text:
                                current_sentence = "" # 버퍼만 비우고 무시
                                continue
                                
                            last_translated_text = final_text
                            current_sentence = "" # 버퍼 비우기
                            
                            print(f"🎤 [번역 요청] 원문: {final_text}", flush=True)
                            if role == "speaker":
                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중...", "role": role})
                            
                            # 번역 작업 비동기 실행
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
    uvicorn.run(app, host="0.0.0.0", port=10000)