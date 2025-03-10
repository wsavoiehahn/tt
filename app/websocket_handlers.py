# app/websocket_handlers.py
import os
import json
import base64
import asyncio
import logging
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Any, Optional, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get OpenAI API key
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
VOICE = "coral"  # OpenAI voice model

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
    logger.error(f"Registered new connection: {connection_id}")
    return connection_id


async def remove_connection(connection_id: str):
    """Remove a WebSocket connection and clean up resources"""
    if connection_id in active_connections:
        connection = active_connections[connection_id]
        openai_ws = connection.get("openai_ws")

        # Close OpenAI WebSocket if it exists
        if openai_ws and not openai_ws.close_code:
            try:
                await openai_ws.close()
                logger.error(f"Closed OpenAI WebSocket for connection: {connection_id}")
            except Exception as e:
                logger.error(f"Error closing OpenAI WebSocket: {str(e)}")

        # Remove from active connections
        del active_connections[connection_id]
        logger.error(f"Removed connection: {connection_id}")


async def connect_to_openai(test_id: str) -> websockets.WebSocketClientProtocol:
    """Connect to OpenAI realtime API and set up a session"""
    try:
        # Import knowledge base
        from app.config import config
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
                "Authorization": f"Bearer {OPENAI_API_KEY}",
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


async def process_incoming_audio(
    websocket: WebSocket, openai_ws: websockets.WebSocketClientProtocol
):
    """Process incoming audio from Twilio and send to OpenAI"""
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
                logger.debug("Sent audio chunk to OpenAI")
            elif data["event"] == "start":
                logger.error(f"Media stream started: {data['start']['streamSid']}")
            elif data["event"] == "stop":
                logger.error("Media stream stopped")
                break
    except WebSocketDisconnect:
        logger.error("WebSocket disconnected during incoming audio processing")
    except Exception as e:
        logger.error(f"Error processing incoming audio: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())


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
    """Handle a media stream WebSocket connection"""
    await websocket.accept()
    logger.error("WebSocket connection established")

    # Get query parameters
    test_id = websocket.query_params.get("test_id")
    call_sid = websocket.query_params.get("call_sid")

    if not test_id or not call_sid:
        logger.error("Missing test_id or call_sid query parameters")
        await websocket.close(code=1000, reason="Missing required parameters")
        return

    logger.error(f"Media stream started - Test ID: {test_id}, Call SID: {call_sid}")

    # Register connection
    connection_id = await register_connection(websocket, test_id, call_sid)

    try:
        # Connect to OpenAI
        openai_ws = await connect_to_openai(test_id)

        # Store OpenAI WebSocket in connection
        if connection_id in active_connections:
            active_connections[connection_id]["openai_ws"] = openai_ws

        # Process audio streams concurrently
        await asyncio.gather(
            process_incoming_audio(websocket, openai_ws),
            process_outgoing_audio(websocket, openai_ws, call_sid, test_id),
        )
    except WebSocketDisconnect:
        logger.error(f"WebSocket disconnected - Test ID: {test_id}")
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
    finally:
        # Clean up connection
        await remove_connection(connection_id)


# Function to capture stream_sid from stream start events
async def update_stream_sid(connection_id: str, stream_sid: str):
    """Update the stream_sid for a connection"""
    if connection_id in active_connections:
        active_connections[connection_id]["stream_sid"] = stream_sid
        logger.error(f"Updated stream_sid for connection {connection_id}: {stream_sid}")
