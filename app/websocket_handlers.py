# app/websocket_handlers.py
from datetime import datetime
import os
import json
import base64
import asyncio
import logging
import time
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Any, Optional, List
from twilio.rest import Client
from .config import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get OpenAI API key
OPENAI_API_KEY = config.get_parameter("/openai/api_key")
ACCOUNT_SID = config.get_parameter("/twilio/account_sid")
AUTH_TOKEN = config.get_parameter("/twilio/auth_token")
client = Client(ACCOUNT_SID, AUTH_TOKEN)
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
    logger.info("WebSocket connection established")

    # Get query parameters
    test_id = websocket.query_params.get("test_id")
    call_sid = websocket.query_params.get("call_sid")

    if not test_id or not call_sid:
        logger.error("Missing test_id or call_sid query parameters")
        await websocket.close(code=1000, reason="Missing required parameters")
        return

    logger.info(f"Media stream started - Test ID: {test_id}, Call SID: {call_sid}")

    # Register connection
    connection_id = await register_connection(websocket, test_id, call_sid)
    async with websockets.connect(
        "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
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

        async def incoming_audio():
            nonlocal stream_sid, latest_media_timestamp, caller_phone, call_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data["event"] == "start":
                        call_sid = data["start"]["callSid"]
                        print(f"callersid:{call_sid}")
                        custom_parameters = data["start"].get("customParameters", {})
                        caller_phone = custom_parameters.get("caller_phone", None)
                        client.calls(call_sid).recordings.create()
                        now = datetime.datetime.utcnow()
                        result = {
                            "phone_no": int(caller_phone),
                            "date": now.isoformat(),
                        }
                        # PUT IN DB
                        # answered_table.put_item(Item=result)

                    if data["event"] == "media" and openai_ws.open:
                        latest_media_timestamp = int(data["media"]["timestamp"])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"],
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data["event"] == "start":
                        stream_sid = data["start"]["streamSid"]
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data["event"] == "mark":
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def outgoing_audio():
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response["type"] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    if (
                        response.get("type") == "response.audio.delta"
                        and "delta" in response
                    ):
                        audio_payload = base64.b64encode(
                            base64.b64decode(response["delta"])
                        ).decode("utf-8")
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": audio_payload},
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp

                        # Update last_assistant_item safely
                        if response.get("item_id"):
                            last_assistant_item = response["item_id"]

                        await send_mark(websocket, stream_sid)

                    if response.get("type") == "input_audio_buffer.speech_started":
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(
                                f"Interrupting response with id: {last_assistant_item}"
                            )
                            await handle_speech_started_event()

                    if response.get("type") == "response.done" and any(
                        item.get("type") == "function_call"
                        for item in response.get("response", {}).get("output", [])
                    ):
                        output = response["response"]["output"]
                        try:
                            call_id = output[0]["call_id"]
                            pos = 0
                        except:
                            call_id = output[1]["call_id"]
                            pos = 1
                        if output[pos]["name"] == "member":
                            new_conversation_item = {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": str(call_id),
                                    "output": f"Respond with (in user's language): Thank you. Due to an extremely high number of calls, hold times and call-back times are longer than usual. I can help answer most questions about your Id, Member Portal and more, and if I can't answer a question, I can help you schedule a call-back with our team. How can I help you today? Feel free to talk in full sentences.",
                                },
                            }

                            await openai_ws.send(json.dumps(new_conversation_item))
                            await openai_ws.send(
                                json.dumps({"type": "response.create"})
                            )

                    if response.get("type") == "response.done" and any(
                        "bye" in content.get("transcript", "")
                        for item in response.get("response", {}).get("output", [])
                        for content in item.get("content", [])
                        if content.get("type") == "audio"
                    ):
                        time.sleep(5)
                        print("ENDING CALL")
                        print(call_sid)
                        call = client.calls(call_sid).update(status="completed")

            except Exception as e:
                print(f"Error in outgoing_audio: {e}")

        async def handle_speech_started_event():
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio

                if last_assistant_item:
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

        async def redirect_call_with_new_twiml(
            call_sid, identifier, caller_phone, language=""
        ):
            try:
                if identifier == "pay":
                    client.calls(call_sid).recordings("Twilio.CURRENT").update(
                        status="stopped"
                    )
                    client.calls(call_sid).update(
                        twiml="<Response><Say voice='Google.en-US-Wavenet-F'>Please hold while I connect you.</Say><Dial><Number>+18778174636</Number></Dial></Response>"
                    )
                elif identifier == "provider":
                    client.calls(call_sid).recordings("Twilio.CURRENT").update(
                        status="stopped"
                    )
                    client.calls(call_sid).update(
                        twiml="<Response><Say voice='Google.en-US-Wavenet-F'>Please hold.</Say><Redirect method='POST'>https://sendero-3.hahn.agency/incoming-call</Redirect></Response>"
                    )
                else:
                    if language.lower() == "spanish":
                        formatted_caller_phone = " ".join(caller_phone)
                        client.calls(call_sid).update(
                            twiml=f"<Response><Say voice='Google.es-US-Wavenet-A'>Muy bien, he registrado una solicitud de devolución de llamada para {formatted_caller_phone}. Te llamaremos en el orden en que se recibió tu llamada. Ten en cuenta que solo te llamaremos una vez. Hasta luego</Say><Hangup/></Response>"
                        )
                    else:
                        formatted_caller_phone = " ".join(caller_phone)
                        client.calls(call_sid).update(
                            twiml=f"<Response><Say voice='Google.en-US-Wavenet-F'>Alright, I have created a call-back request for {formatted_caller_phone}. We will call you back in the order your call was received. Please note: we will only call you back once. Goodbye.</Say><Hangup/></Response>"
                        )

            except Exception as e:
                print(f"Error redirecting call: {e}")

        await asyncio.gather(incoming_audio(), outgoing_audio())


async def handle_media_stream2(websocket: WebSocket):
    """Handle a media stream WebSocket connection"""
    await websocket.accept()
    logger.info("WebSocket connection established")

    # Get query parameters
    test_id = websocket.query_params.get("test_id")
    call_sid = websocket.query_params.get("call_sid")

    if not test_id or not call_sid:
        logger.error("Missing test_id or call_sid query parameters")
        await websocket.close(code=1000, reason="Missing required parameters")
        return

    logger.info(f"Media stream started - Test ID: {test_id}, Call SID: {call_sid}")

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


async def initialize_session(openai_ws):
    # TODO update so that knowledge base is the instructions
    session_update = {
        "type": "session.update",
        "session": {
            "tools": [
                {
                    "type": "function",
                    "name": "respond_to_startup_question",
                    "description": "Call this function to respond to the initial greeting or startup message",
                },
                {
                    "type": "function",
                    "name": "evaluation_questions",
                    "description": "Call this function when you begin the asked about the reason for calling today",
                },
            ],
            "tool_choice": "auto",
            "turn_detection": {"type": "server_vad", "threshold": 0.8},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            # "instructions": knowledge_base,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        },
    }

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
