# app/websocket_handlers.py
from datetime import datetime, timezone
import os
import json
import base64
import asyncio
import logging
import time
import websockets
from websockets.protocol import State
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Any, Optional, List
from twilio.rest import Client
from ..config import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = Client(
    username=os.environ.get("TWILIO_ACCOUNT_SID"),
    password=os.environ.get("TWILIO_AUTH_TOKEN"),
)
VOICE = "coral"  # OpenAI voice model
LOG_EVENT_TYPES = [
    "response.content.done",
    "rate_limits.updated",
    "response.done",
    "input_audio_buffer.committed",
    "input_audio_buffer.speech_stopped",
    "input_audio_buffer.speech_started",
    "session_created",
]

# Track active WebSocket connections
active_connections = {}


async def register_connection(websocket: WebSocket, test_id: str, call_sid: str):
    """Register a new WebSocket connection"""
    connection_id = f"{call_sid}_{test_id}"
    active_connections[connection_id] = {
        "websocket": websocket,
        "test_id": test_id,
        "call_sid": call_sid,
        "connected_at": "connected",
        "openai_ws": None,
    }
    logger.info(f"Registered new connection: {connection_id}")
    return connection_id


async def connect_to_openai(test_id: str, client) -> websockets.WebSocketClientProtocol:
    """Connect to OpenAI realtime API and set up a session"""
    try:
        # Import knowledge base
        from app.services.evaluator import evaluator_service

        # Get test data if available
        test_data = {}
        if test_id in evaluator_service.active_tests:
            test_data = evaluator_service.active_tests[test_id]

        # Load knowledge base
        knowledge_base = config.load_knowledge_base()

        # Connect to OpenAI
        logger.error("Connecting to OpenAI realtime API")
        openai_ws = await websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
            additional_headers={
                "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
                "OpenAI-Beta": "realtime=v1",
            },
        )

        # Create system message
        persona_name = test_data.get("config", {}).get(
            "persona_name", "Default Persona"
        )
        behavior_name = test_data.get("config", {}).get(
            "behavior_name", "Default Behavior"
        )

        # Get persona and behavior details
        persona = evaluator_service.get_persona(persona_name)
        behavior = evaluator_service.get_behavior(behavior_name)

        # Create system message
        system_message = f"""
        You are an AI evaluator testing a customer service response.
        You are calling as a {persona_name} persona with {behavior_name} behavior traits.
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
        logger.error("OpenAI session initialized")

        return openai_ws

    except Exception as e:
        logger.error(f"Error connecting to OpenAI: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        raise


async def process_outgoing_audio(
    websocket: WebSocket,
    openai_ws: websockets.WebSocketClientProtocol,
    call_sid: str,
    test_id: str,
):
    """Process outgoing audio from OpenAI and send to Twilio"""
    stream_sid = None

    try:
        async for openai_message in openai_ws:
            response = json.loads(openai_message)

            if response.get("type") == "response.audio.delta" and "delta" in response:
                # Extract stream_sid from connection data or existing messages
                if not stream_sid:
                    # Look for stream_sid in active connections
                    connection_id = f"{call_sid}_{test_id}"
                    if connection_id in active_connections:
                        stream_sid = active_connections[connection_id].get("stream_sid")
                        logger.info("stream_sid not found")

                if stream_sid:
                    audio_payload = response["delta"]
                    audio_delta = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": audio_payload},
                    }
                    await websocket.send_json(audio_delta)
                else:
                    logger.warning("No stream_sid available for response")

            elif response.get("type") == "input_audio_buffer.transcription":
                text = response.get("text", "")
                logger.error(f"Transcription: {text}")

                # Record conversation turn
                from app.services.evaluator import evaluator_service

                evaluator_service.record_conversation_turn(
                    test_id=test_id, call_sid=call_sid, speaker="agent", text=text
                )
    except WebSocketDisconnect:
        logger.error("WebSocket disconnected during outgoing audio processing")
    except Exception as e:
        logger.error(f"Error processing outgoing audio: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())


async def handle_media_stream(websocket: WebSocket):
    """Handle a media stream WebSocket connections"""
    await websocket.accept()
    logger.info("WebSocket connection established")
    async with websockets.connect(
        "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
        additional_headers={
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
            "OpenAI-Beta": "realtime=v1",
        },
    ) as openai_ws:
        await initialize_session(openai_ws)
        stream_sid = None

        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None

        caller_phone = None
        call_sid = ""
        test_id = None

        async def agent_audio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp, caller_phone, call_sid, test_id
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data["event"] == "media" and openai_ws.state == State.OPEN:
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"],
                        }
                        await openai_ws.send(json.dumps(audio_append))

                    elif data["event"] == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"Incoming stream has started {stream_sid}")
                        latest_media_timestamp = 0
                        last_assistant_item = None
                        response_start_timestamp_twilio = None
                        call_sid = data["start"]["callSid"]
                        logger.info(f"callersid:{call_sid}")
                        custom_parameters = data["start"].get("customParameters", {})
                        test_id = custom_parameters.get("test_id")
                        connection_id = await register_connection(
                            websocket, test_id, call_sid
                        )
                        logger.info(
                            f"Received start event: call_sid={call_sid}, test_id={test_id}"
                        )

                        # client.calls(call_sid).recordings.create()
                        now = datetime.now(timezone.utc)
                    elif data["event"] == "mark":
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                logger.warning("Client disconnected.")
                if openai_ws.state == State.OPEN:
                    await openai_ws.close()

        async def evaluator_audio():
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio, test_id
            try:
                response_audio = b""
                response_text = ""
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    if response["type"] in LOG_EVENT_TYPES:
                        logger.info(f"Received event: {response['type']}", response)

                    # if response.get("type") == "input_audio_buffer.transcription":
                    #     text = response.get("text", "")
                    #     logger.error(f"Transcription: {text}")

                    #     # Record conversation turn
                    #     from app.services.evaluator import evaluator_service

                    #     evaluator_service.record_conversation_turn(
                    #         test_id=test_id,
                    #         call_sid=call_sid,
                    #         speaker="evaluator",
                    #         text=text,
                    #     )
                    if response["type"] == "response.audio.delta" and response.get(
                        "delta"
                    ):
                        try:
                            audio_payload = base64.b64encode(
                                base64.b64decode(response["delta"])
                            ).decode("utf-8")
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_payload},
                            }
                            # response_audio += audio_payload
                            # TODO send audio to s3
                            await websocket.send_json(audio_delta)
                        except Exception as e:
                            logger.error(f"Error processing audio data: {e}")

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            logger.info(
                                f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms"
                            )

                        # Update last_assistant_item safely
                        if response.get("item_id"):
                            last_assistant_item = response["item_id"]

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get("type") == "input_audio_buffer.speech_started":
                        logger.info("Speech started detected.")
                        if last_assistant_item:
                            print(
                                f"Interrupting response with id: {last_assistant_item}"
                            )
                            await handle_speech_started_event()

            except Exception as e:
                logging.error(f"Error in evaluator_audio: {e}")
            except Exception as e:
                logging.error(f"Error in evaluator_audio: {e}")

        async def handle_speech_started_event():
            nonlocal response_start_timestamp_twilio, last_assistant_item
            logging.info("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio

                logger.info(
                    f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms"
                )
                if last_assistant_item:
                    logger.info(
                        f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms"
                    )

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time,
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({"event": "clear", "streamSid": stream_sid})

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"},
                }
                await connection.send_json(mark_event)
                mark_queue.append("responsePart")

        await asyncio.gather(agent_audio(), evaluator_audio())


# Function to capture stream_sid from stream start events
async def update_stream_sid(connection_id: str, stream_sid: str):
    """Update the stream_sid for a connection"""
    if connection_id in active_connections:
        active_connections[connection_id]["stream_sid"] = stream_sid
        logger.error(f"Updated stream_sid for connection {connection_id}: {stream_sid}")


async def initialize_session(openai_ws):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": "Be polite to me and tell some jokes!",
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        },
    }

    # "tools": [
    #     {
    #         "type": "function",
    #         "name": "respond_to_startup_question",
    #         "description": "Call this function to respond to the initial greeting or startup message",
    #     },
    #     {
    #         "type": "function",
    #         "name": "evaluation_questions",
    #         "description": "Call this function when you begin the asked about the reason for calling today",
    #     },
    # ],
    # "tool_choice": "auto",

    await openai_ws.send(json.dumps(session_update))
    await send_initial_conversation_item(openai_ws)


async def send_initial_conversation_item(openai_ws):
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Greet with Hello",
                }
            ],
        },
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))
