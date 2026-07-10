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
# 💡 1. API 키 로드 (클라우드 환경 변수 방식)
# ==========================================
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
# 🧠 4. 백그라운드 슬라이딩 요약 봇
# ==========================================
async def update_sliding_summary(summary_state: dict, new_sentences: list):
    current_summary = summary_state.get("text", "")
    new_text = "\n".join(new_sentences)
    
    prompt = f"""You are a context summarizer for a construction site meeting/discussion.
    Update the existing summary with the new sentences.
    Keep it EXTREMELY concise (1-2 sentences maximum).
    Focus ONLY on factual context: who, where, what, and specific risks or items mentioned.
    
    [Existing Summary]
    {current_summary if current_summary else "None"}
    
    [New Sentences]
    {new_text}
    
    Respond ONLY with the newly updated summary string in Korean."""
    
    try:
        response = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        summary_state["text"] = response.content[0].text.strip()
        print(f"\n🧠 [비서 요약 봇] 문맥 압축 완료: {summary_state['text']}\n", flush=True)
    except Exception as e:
        pass

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
    if token not in ACTIVE_TOKENS:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await manager.connect(websocket)
    
    recent_history = [] 
    summary_state = {"text": ""} 
    
    glossary_text = ""
    dynamic_targets = targets 

    try:
        if role == "viewer":
            while True:
                data = await websocket.receive()
        else:
            # 💡 [핵심] lang 파라미터가 "multi"로 넘어오면, Deepgram의 다국어 자동 감지 기능이 그대로 작동합니다.
            dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format=true&interim_results=true&endpointing={endpointing}&keepalive=true"
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

            ws_kwargs = {}
            if int(websockets.__version__.split('.')[0]) >= 14:
                ws_kwargs["additional_headers"] = headers
            else:
                ws_kwargs["extra_headers"] = headers

            async with websockets.connect(dg_url, **ws_kwargs) as dg_ws:
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
                                    except: pass
                    except: pass

                async def receiver():
                    current_sentence = ""
                    last_translated_text = "" 
                    current_msg_id = secrets.token_hex(4)
                    
                    try:
                        while True:
                            dg_result = await dg_ws.recv()
                            dg_json = json.loads(dg_result)
                            
                            if dg_json.get("type") == "Results":
                                is_final = dg_json.get("is_final", False)
                                speech_final = dg_json.get("speech_final", False)
                                transcript = dg_json.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()
                                
                                if transcript or current_sentence:
                                    display_text = current_sentence + " " + transcript if current_sentence and transcript else current_sentence or transcript
                                    current_targets_list = dynamic_targets.split(',') if isinstance(dynamic_targets, str) else dynamic_targets
                                    await manager.broadcast_json({
                                        "type": "interim", 
                                        "text": display_text.strip(),
                                        "targets": current_targets_list,
                                        "msg_id": current_msg_id
                                    })

                                if is_final and transcript:
                                    if current_sentence: current_sentence += " " + transcript
                                    else: current_sentence = transcript

                                is_semantic_end = current_sentence.strip().endswith(('.', '?', '!'))

                                if (speech_final or len(current_sentence) > max_chars or is_semantic_end) and current_sentence.strip():
                                    final_text = current_sentence.strip()
                                    
                                    if final_text != last_translated_text:
                                        last_translated_text = final_text
                                        await manager.broadcast_json({"type": "status", "text": "⏳ 다국어 번역 중..."})
                                        
                                        asyncio.create_task(translate_and_send(final_text, lang, dynamic_targets, recent_history, summary_state, glossary_text, current_msg_id))
                                    
                                    current_sentence = ""
                                    current_msg_id = secrets.token_hex(4)
                    except: pass
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 에러 발생: {e}")

# ==========================================
# 🧠 6. 메인 LLM 번역 로직
# ==========================================
async def translate_and_send(text: str, source_lang: str, targets: str, recent_history: list, summary_state: dict, glossary_text: str, msg_id: str):
    
    if any(keyword in text for keyword in ["위험", "주의", "낙하", "사고", "멈춰"]):
        await manager.broadcast_json({"type": "alert"})

    ignore_words = ["you", "thank you", "o", "hmm", "uh", "아", "음", "hola", "어"]
    if not text or len(text) < 2 or text.lower() in ignore_words:
        await manager.broadcast_json({"type": "status", "text": "✅ 대기 중..."})
        return

    history_str = "\n".join([f"- {past}" for past in recent_history]) if recent_history else "없음 (No recent context)"
    glossary_section = f"\n[MEETING GLOSSARY / DOMAIN KNOWLEDGE]\n{glossary_text}\n" if glossary_text.strip() else ""

    # 💡 [핵심] 언어가 multi(자동 감지)일 경우, Claude가 혼란스러워하지 않도록 프롬프트(명령어)를 똑똑하게 분기 처리합니다.
    if source_lang == "multi":
        lang_instruction = "The speaker might use various languages during the discussion (e.g., Korean, English, Indonesian, etc.). Detect the spoken language of the CURRENT SENTENCE automatically."
    else:
        lang_instruction = f"The spoken language is strictly '{source_lang}'."

    system_prompt = [
        {
            "type": "text",
            "text": f"""You are a top-tier professional simultaneous interpreter for a multinational construction site.
    
    [PAST CONTEXT SUMMARY]
    {summary_state['text'] if summary_state['text'] else "No summary yet."}
    
    [RECENT CONTEXT]
    {history_str}
    
    {glossary_section}
    
    CRITICAL INSTRUCTIONS:
    1. {lang_instruction}
    2. Fix any STT typos in the CURRENT SENTENCE based on the context.
    3. Translate ONLY the CURRENT SENTENCE into the target language codes: {targets}.
    4. ABSOLUTE RULE: You MUST provide EXACTLY ONE best translation per language. 
       - NEVER use slashes (/) or parentheses to provide alternative options.
       - Pick ONLY ONE natural translation and output it.
    
    Respond EXACTLY in this tag format (DO NOT USE JSON):
    [original]
    clean current sentence
    [lang_code_1]
    result
    [lang_code_2]
    result""",
            "cache_control": {"type": "ephemeral"}
        }
    ]
    
    try:
        stream = await claude_client.messages.create(
                model="claude-haiku-4-5-20251001", 
                max_tokens=500,
                system=system_prompt, 
                messages=[{"role": "user", "content": text}],
                stream=True
        )

        buffer = ""
        lang_text = {}
        
        async for event in stream:
            if event.type == "content_block_delta":
                buffer += event.delta.text
                
                matches = re.finditer(r'\[([a-z]+)\]\s*(.*?)(?=\[|$)', buffer, re.DOTALL)
                for match in matches:
                    lang = match.group(1)
                    text_so_far = match.group(2).strip()
                    
                    lang_text[lang] = text_so_far
                    
                    if lang != 'original':
                        await manager.broadcast_json({
                            "type": "stream_update",
                            "lang": lang,
                            "text": text_so_far,
                            "original_text": lang_text.get('original', ''),
                            "source_lang": source_lang,
                            "msg_id": msg_id
                        })
        
        original_text = lang_text.get('original', text)
        recent_history.append(original_text)
        
        if len(recent_history) >= 5:
            sentences_to_summarize = recent_history[:3]
            del recent_history[:3] 
            asyncio.create_task(update_sliding_summary(summary_state, sentences_to_summarize))
        
        for lang, final_text in lang_text.items():
            if lang != 'original':
                await manager.broadcast_json({
                    "type": "stream_end",
                    "lang": lang,
                    "text": final_text,
                    "original_text": lang_text.get('original', ''),
                    "source_lang": source_lang,
                    "msg_id": msg_id
                })
                
        await manager.broadcast_json({"type": "sentence_complete"})
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