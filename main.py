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

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
    return FileResponse("index.html")

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        # 공백 제거 및 문자열 강제 변환으로 안전한 검증 수행
        user_id = str(data.get("username", data.get("id", ""))).strip()
        password = str(data.get("password", "")).strip()
        
        # 관리자 로그인 검증 (admin / 1234)
        if user_id == "admin" and password == "1234":
            token = uuid.uuid4().hex
            manager.auth_tokens.add(token)
            print(f"🔐 [인증 성공] 관리자 로그인 통과", flush=True)
            return JSONResponse(content={"success": True, "token": token})
        else:
            print(f"❌ [인증 실패] ID: {user_id}, PW: {password}", flush=True)
            # 프론트엔드와 일치하는 'message' 키 사용으로 undefined 에러 원천 차단
            return JSONResponse(content={"success": False, "message": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)
    except Exception as e:
        return JSONResponse(content={"success": False, "message": f"로그인 에러: {str(e)}"}, status_code=400)

async def translate_and_send(text: str, source_lang: str, targets: list, context_memory: list, glossary_text: str, role: str):
    # 이전 문맥(최대 3개 문장)을 프롬프트에 포함하여 주어 생략 현상 극복
    context_str = " ".join(context_memory[-3:])
    
    system_prompt = f"""
    You are a real-time translator for construction safety.
    The source text is in '{source_lang}'.
    Target languages to translate into: {targets}.
    
    [Glossary]
    {glossary_text}
    
    [Context (Previous sentences)]
    {context_str}
    
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
        # 합의된 최신 4.5 모델 적용
        response = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": text}]
        )
        
        result_text = response.content[0].text.strip()
        
        # JSON 추출기
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result_json = json.loads(json_match.group(0))
        else:
            result_json = json.loads(result_text)

        translations = result_json.get("translations", {})
        
        # 클로드가 언어를 빼먹었을 경우 원문으로 강제 채움 방어막
        for t in targets:
            if t not in translations or not translations[t]:
                translations[t] = text

        # 문맥 메모리 업데이트
        context_memory.append(text)
        if len(context_memory) > 3:
            context_memory.pop(0)

        # 번역 결과를 모든 클라이언트에게 전송
        await manager.broadcast_json({
            "type": "translation",
            "original": text,
            "source_lang": source_lang,
            "translations": translations,
            "role": role
        })
        print(f"✅ [번역 완료] {translations}", flush=True)
        
        # 버튼 상태 복구
        if role == "speaker":
            await manager.broadcast_json({"type": "status", "text": "✅ 번역 완료 (대기 중)", "role": role})

    except Exception as e:
        print(f"🚨 [번역 에러] {str(e)}", flush=True)
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
    endpointing = websocket.query_params.get("endpointing", "500")
    max_chars = int(websocket.query_params.get("max_chars", "50"))
    
    context_memory = []
    glossary_text = ""
    last_translated_text = ""

    # keepalive=true 로 타임아웃 방지, detect_language=true 미사용으로 400 에러 차단
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
    # additional_headers 대신 extra_headers 사용 (websockets 최신 버전 호환성)
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    try:
        async with websockets.connect(dg_url, extra_headers=headers) as dg_ws:
            print("🟢 [연결됨] 딥그램 STT 서버 연결 성공", flush=True)

            # 브라우저 -> 딥그램 오디오/설정 전송
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

            # 딥그램 STT 결과 수신 -> 절단 로직 -> 번역 전송
            async def receiver():
                nonlocal last_translated_text
                current_sentence = ""
                try:
                    while True:
                        dg_result = await dg_ws.recv()
                        result_dict = json.loads(dg_result)
                        
                        if result_dict.get("type") == "Metadata":
                            continue

                        is_final = result_dict.get("is_final", False)
                        speech_final = result_dict.get("speech_final", False)
                        
                        channel = result_dict.get("channel", {})
                        alternatives = channel.get("alternatives", [{}])
                        transcript = alternatives[0].get("transcript", "")

                        if transcript:
                            current_sentence = transcript

                        if current_sentence.strip():
                            await manager.broadcast_json({
                                "type": "interim",
                                "text": current_sentence,
                                "source_lang": lang,
                                "role": role
                            })

                        # 문맥 감지 기반 마침표 트리거
                        is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))
                        
                        # 3중 방어막 절단 조건
                        if (speech_final or is_semantic_end or is_final or len(current_sentence) > max_chars) and current_sentence.strip():
                            final_text = current_sentence.strip()
                            
                            # 중복 문장 방지 (딥그램 과잉 친절에 의한 폭탄 요청 방어막)
                            if final_text == last_translated_text:
                                current_sentence = ""
                                continue
                                
                            last_translated_text = final_text
                            current_sentence = ""
                            
                            print(f"🎤 [번역 요청] 원문: {final_text}", flush=True)
                            if role == "speaker":
                                await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중...", "role": role})
                            
                            asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text, role))

                except Exception as e:
                    print(f"🚨 [에러] 딥그램 수신 중단: {e}", flush=True)

            await asyncio.gather(sender(), receiver())
            
    except Exception as e:
        print(f"🚨 [서버 에러] {e}", flush=True)
    finally:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)