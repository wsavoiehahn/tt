import os
import json
import base64
import asyncio
import logging
import websockets
from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Get OpenAI API key
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
VOICE = "coral"


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "AI Call Center Evaluator is running"}


@app.api_route("/webhooks/call-started", methods=["GET", "POST"])
async def handle_call_started(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    test_id = request.query_params.get("test_id")

    logger.info(f"Call started - SID: {call_sid}, Test ID: {test_id}")

    # Create TwiML response with WebSocket stream
    response = VoiceResponse()
    response.say("Starting evaluation call.")

    connect = Connect()
    host = request.url.hostname
    stream = Stream(
        name="media_stream",
        url=f"wss://{host}/media-stream?test_id={test_id}&call_sid={call_sid}",
    )
    stream.parameter(name="format", value="audio")
    connect.append(stream)
    response.append(connect)

    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection established")

    # Get query parameters
    test_id = websocket.query_params.get("test_id")
    call_sid = websocket.query_params.get("call_sid")

    logger.info(f"Media stream started - Test ID: {test_id}, Call SID: {call_sid}")

    # Connect to OpenAI
    try:
        async with websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as openai_ws:
            # Initialize OpenAI session
            await initialize_openai_session(openai_ws, test_id)

            # Create tasks for handling incoming and outgoing audio
            await asyncio.gather(
                process_incoming_audio(websocket, openai_ws),
                process_outgoing_audio(websocket, openai_ws, call_sid, test_id),
            )
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {str(e)}")


async def initialize_openai_session(openai_ws, test_id):
    # Import knowledge base
    from app.config import config

    knowledge_base = config.load_knowledge_base()

    # Create system message
    system_message = f"""
    You are an AI evaluator testing a customer service response.
    You are calling to ask questions about insurance plans.
    """

    # Initialize session
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad", "threshold": 0.8},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": system_message,
            "modalities": ["text", "audio"],
            "temperature": 0.7,
        },
    }

    await openai_ws.send(json.dumps(session_update))
    logger.info("OpenAI session initialized")


async def process_incoming_audio(websocket, openai_ws):
    try:
        async for message in websocket.iter_text():
            data = json.loads(message)

            if data["event"] == "media":
                # Send audio to OpenAI
                audio_append = {
                    "type": "input_audio_buffer.append",
                    "audio": data["media"]["payload"],
                }
                await openai_ws.send(json.dumps(audio_append))
            elif data["event"] == "start":
                logger.info(f"Media stream started: {data['start']['streamSid']}")
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")


async def process_outgoing_audio(websocket, openai_ws, call_sid, test_id):
    stream_sid = None

    try:
        async for openai_message in openai_ws:
            response = json.loads(openai_message)

            if response.get("type") == "response.audio.delta" and "delta" in response:
                if not stream_sid:
                    logger.warning("No stream_sid available for response")
                    continue

                audio_payload = response["delta"]
                audio_delta = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_payload},
                }
                await websocket.send_json(audio_delta)

            elif response.get("type") == "input_audio_buffer.transcription":
                text = response.get("text", "")
                logger.info(f"Transcription: {text}")

                # Record in database
                from app.services.evaluator import evaluator_service

                evaluator_service.record_conversation_turn(
                    test_id=test_id, call_sid=call_sid, speaker="agent", text=text
                )
    except Exception as e:
        logger.error(f"Error in outgoing audio: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
