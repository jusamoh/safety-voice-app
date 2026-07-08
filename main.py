<!-- ... existing code ... -->
import os
import json
import asyncio
import re
from fastapi import FastAPI, WebSocket, Query
from fastapi.responses import FileResponse
<!-- ... existing code ... -->
```

```python:현장 통역 서버 (타임아웃 및 무전기 최종 복구본):main.py
<!-- ... existing code ... -->
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
        
        # 💡 [정규식 파싱] 4.5 모델이 불필요한 설명을 덧붙여도 순수 JSON만 정확히 추출
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
        
        # 번역 완료 시 버튼 원상복구
        await manager.broadcast_json({"type": "status", "text": "방송 중...", "role": role})

    except Exception as e:
        # 💡 flush=True 를 추가하여 에러 로그가 즉시 렌더 터미널에 찍히도록 강제
        print(f"🚨 Translation Error: {e}", flush=True)
        await manager.broadcast_json({"type": "status", "text": "❌ 번역 에러 (로그 확인)", "role": role})

# ==========================================
# ⚡ 5. 다국적 웹소켓 파이프라인 (STT 연동)
# ==========================================
<!-- ... existing code ... -->
```

```python:현장 통역 서버 (타임아웃 및 무전기 최종 복구본):main.py
<!-- ... existing code ... -->
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
                                        print(f"🎵 [정상] 브라우저로부터 오디오 데이터 수신 중... ({audio_packet_count} 패킷)", flush=True)
                                    await dg_ws.send(message.get("bytes"))
                                elif message.get("text"):
                                    try:
                                        config = json.loads(message.get("text"))
                                        if config.get("type") == "config":
                                            glossary_text = config.get("glossary", "")
                                            print(f"✅ [용어집 수신 완료] {len(glossary_text)}자", flush=True)
                                    except:
                                        pass
                    except Exception as e:
                        print(f"🚨 [에러] 오디오 전송 중단: {e}", flush=True)

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
                                    print(f"✅ [문장 절단 감지] 번역기 전송: {final_text}", flush=True)
                                    await manager.broadcast_json({"type": "status", "text": "다국어 번역 중...", "role": role})
                                    asyncio.create_task(translate_and_send(final_text, lang, targets, context_memory, glossary_text, role))
                    except Exception as e:
                        print(f"🚨 [에러] 딥그램 수신 중단: {e}", flush=True)
                
                await asyncio.gather(sender(), receiver())
                
    except Exception as e:
        print(f"🚨 웹소켓/Deepgram 에러 발생: {e}", flush=True)
    finally:
        manager.disconnect(websocket)
<!-- ... existing code ... -->
```

이 3부분을 수정(저장) 하신 후, 렌더가 재배포되면 다시 한 번 마이크를 켜서 테스트해 보세요. 
만약 이번에도 번역이 안 된다면, 이제는 **렌더(Render) 로그 화면에 빨간색으로 무조건 진짜 원인(`🚨 Translation Error: ...`)이 명확하게 찍혀있을 것**입니다. 그 에러 메시지만 확인하면 백발백중 잡아낼 수 있습니다!