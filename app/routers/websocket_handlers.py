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
from app.config import config
from app.services.evaluator import evaluator_service
from app.services.dynamodb_service import dynamodb_service

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


# Add this function to save audio to S3
async def save_audio_chunk(audio_data, test_id, call_sid, speaker, turn_number=None):
    """Save an audio chunk to S3 and return the S3 URL."""
    try:
        from app.services.s3_service import s3_service

        # If turn_number is not provided, try to determine it
        if turn_number is None:
            from app.services.evaluator import evaluator_service

            if test_id in evaluator_service.active_tests:
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )
                # Count turns by this speaker
                turn_number = sum(
                    1 for turn in conversation if turn.get("speaker") == speaker
                )
            else:
                turn_number = 0

        # Save the audio to S3
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_url = s3_service.save_audio(
            audio_data=audio_data,
            test_id=test_id,
            call_sid=call_sid,
            turn_number=turn_number,
            speaker=speaker,
        )

        logger.info(f"Saved audio to S3: {s3_url}")
        return s3_url
    except Exception as e:
        logger.error(f"Error saving audio to S3: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        return None


async def save_transcription(text, test_id, call_sid, speaker, turn_number=None):
    """Save a transcription to S3 and return the S3 URL, with improved error handling."""
    try:
        if not text or len(text.strip()) == 0:
            logger.warning(f"Empty transcription provided for {speaker}")
            return None

        from app.services.s3_service import s3_service

        # If turn_number is not provided, try to determine it
        if turn_number is None:
            from app.services.evaluator import evaluator_service

            if test_id in evaluator_service.active_tests:
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )
                # Count turns by this speaker
                turn_number = sum(
                    1 for turn in conversation if turn.get("speaker") == speaker
                )
            else:
                turn_number = 0

        # Save the transcription to S3
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_url = s3_service.save_transcription(
            transcription=text,
            test_id=test_id,
            call_sid=call_sid,
            turn_number=turn_number,
            speaker=speaker,
        )

        if not s3_url:
            logger.error(f"Failed to save transcription to S3")
            return None

        logger.info(f"Saved transcription to S3: {s3_url}")

        # Also save to conversation directly
        from app.services.evaluator import evaluator_service

        if test_id in evaluator_service.active_tests:
            # Look for the turn from this speaker without text
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )
            for turn in reversed(conversation):
                if turn.get("speaker") == speaker and not turn.get("text"):
                    # Update the turn with text
                    turn["text"] = text
                    # Also add the transcription URL
                    turn["transcription_url"] = s3_url
                    break

            # If we didn't find a turn to update, add a new one
            else:
                new_turn = {
                    "speaker": speaker,
                    "text": text,
                    "timestamp": datetime.now().isoformat(),
                    "transcription_url": s3_url,
                }
                if "conversation" not in evaluator_service.active_tests[test_id]:
                    evaluator_service.active_tests[test_id]["conversation"] = []
                evaluator_service.active_tests[test_id]["conversation"].append(new_turn)

            # Save to DynamoDB
            from app.services.dynamodb_service import dynamodb_service

            dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

        return s3_url
    except Exception as e:
        logger.error(f"Error saving transcription to S3: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        return None


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


async def handle_media_stream(websocket: WebSocket):
    """Handle a media stream WebSocket connections"""
    await websocket.accept()
    logger.info("WebSocket connection established")

    # Create buffers to accumulate audio data by speaker
    evaluator_audio_buffer = bytearray()
    agent_audio_buffer = bytearray()

    test_id = None
    call_sid = None
    current_speaker = None
    last_transcription_time = datetime.now()

    # Track conversation turns by speaker
    evaluator_turn_count = 0
    agent_turn_count = 0

    # Flag to track if full conversation is being recorded
    is_recording_full_conversation = True
    full_conversation_audio = bytearray()

    stream_sid = None
    latest_media_timestamp = 0
    last_assistant_item = None
    mark_queue = []
    response_start_timestamp_twilio = None

    # async def receive_from_twilio():
    #     """receive from twilio send to openai"""
    #     nonlocal stream_sid, latest_media_timestamp, test_id, call_sid, current_speaker, agent_turn_count
    #     try:
    #         agent_turn_count +=1
    #         async for message in websocket.iter_text():
    #             logger.error()
    #             data = json.loads(message)
    #             if data['event'] == 'media' and openai_ws.open:
    #                 latest_media_timestamp = int(data['media']['timestamp'])
    #                 audio_append = {
    #                     "type": "input_audio_buffer.append",
    #                     "audio": data['media']['payload']
    #                 }
    #                 await openai_ws.send(json.dumps(audio_append))
    #             elif data['event'] == 'start':
    #                 stream_sid = data['start']['streamSid']
    #                 print(f"Incoming stream has started {stream_sid}")
    #                 response_start_timestamp_twilio = None
    #                 latest_media_timestamp = 0
    #                 last_assistant_item = None
    #             elif data['event'] == 'mark':
    #                 if mark_queue:
    #                     mark_queue.pop(0)
    #     except WebSocketDisconnect:
    #         logger.error("Client disconnected.")
    #         if openai_ws.state == State.OPEN:
    #             await openai_ws.close()

    # async def send_to_twilio():
    #     """receive from openai send audio back to twilio"""

    async def send_mark(connection, stream_sid):
        if stream_sid:
            mark_event = {
                "event": "mark",
                "streamSid": stream_sid,
                "mark": {"name": "responsePart"},
            }
            await connection.send_json(mark_event)
            mark_queue.append("responsePart")

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

    try:
        # Connect to OpenAI
        async with websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
            additional_headers={
                "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as openai_ws:
            # Initialize the session
            await initialize_session(openai_ws)
            logger.info("OpenAI session initialized")

            async def agent_audio():
                """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
                nonlocal stream_sid, latest_media_timestamp, call_sid, test_id, current_speaker
                try:
                    async for message in websocket.iter_text():
                        from app.services.evaluator import evaluator_service

                        data = json.loads(message)
                        if data["event"] == "media" and openai_ws.state == State.OPEN:
                            current_speaker = "agent"
                            audio_payload = data["media"]["payload"]
                            try:
                                audio_bytes = base64.b64decode(audio_payload)
                                # Append to agent audio buffer
                                agent_audio_buffer.extend(audio_bytes)
                            except:
                                logger.error("Error decoding audio payload")
                            audio_append = {
                                "type": "input_audio_buffer.append",
                                "audio": audio_payload,
                            }
                            await openai_ws.send(json.dumps(audio_append))
                        elif data["event"] == "start":
                            stream_sid = data["start"]["streamSid"]
                            call_sid = data["start"]["callSid"]
                            test_id = (
                                data["start"].get("customParameters", {}).get("test_id")
                            )
                            client.calls(call_sid).recordings.create()
                            logger.info(
                                f"Incoming stream has started stream_sid: {stream_sid}, call_sid: {call_sid}, test_id:{test_id}"
                            )
                            latest_media_timestamp = 0
                            last_assistant_item = None
                            response_start_timestamp_twilio = None

                            if test_id not in evaluator_service.active_tests:
                                logger.warning(
                                    f"Test {test_id} not found in active_tests, initializing"
                                )
                                active_test = evaluator_service.active_tests[
                                    test_id
                                ] = {
                                    "status": "in_progress",
                                    "call_sid": call_sid,
                                    "start_time": datetime.now().isoformat(),
                                    "conversation": [],
                                }
                                dynamodb_service.save_test(test_id, active_test)

                            logger.info(
                                f"Received start event: call_sid={call_sid}, test_id={test_id}"
                            )
                        elif data["event"] == "mark":
                            if mark_queue:
                                mark_queue.pop(0)
                        elif data["event"] == "stop" and test_id and call_sid:
                            logging.info("This only happens when I hangup the call")

                            agent_turn_count += 1
                            # Save the accumulated agent audio
                            if len(agent_audio_buffer) > 0:
                                # Save agent's final audio
                                s3_url = await save_audio_chunk(
                                    bytes(agent_audio_buffer),
                                    test_id,
                                    call_sid,
                                    current_speaker,
                                )
                                # Find the last agent turn and update with audio URL
                                if test_id in evaluator_service.active_tests:
                                    conversation = evaluator_service.active_tests[
                                        test_id
                                    ].get("conversation", [])
                                    # Find the last agent turn without an audio URL
                                    for turn in reversed(conversation):
                                        if turn["speaker"] == "agent" and not turn.get(
                                            "audio_url"
                                        ):
                                            turn["audio_url"] = s3_url
                                            break

                                    dynamodb_service.save_test(
                                        test_id,
                                        evaluator_service.active_tests[test_id],
                                    )
                except WebSocketDisconnect:
                    logger.warning("Client disconnected.")
                    if openai_ws.state == State.OPEN:
                        await openai_ws.close()
                except Exception as e:
                    logger.error(f"Error in agent_audio: {str(e)}")
                finally:
                    logger.info(f"agent_audio task completed for call {call_sid}")

            async def evaluator_audio():
                """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
                nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio, test_id
                nonlocal current_speaker, evaluator_audio_buffer, full_conversation_audio
                nonlocal evaluator_turn_count, agent_turn_count, last_transcription_time

                response_text_buffer = ""

                try:
                    async for openai_message in openai_ws:
                        response = json.loads(openai_message)
                        if response["type"] in LOG_EVENT_TYPES:
                            logger.info(f"Received event: {response['type']}")
                        # Handle transcribed input from the agent
                        if response.get("type") == "input_audio_buffer.transcription":
                            current_speaker = "agent"  # Set correct speaker
                            text = response.get("text", "")
                            logger.error(f"Agent transcription: {text}")
                            if test_id and text and len(text.strip()) > 0:
                                # Save the transcription
                                transcript_url = await save_transcription(
                                    text,
                                    test_id,
                                    call_sid,
                                    "agent",  # Use explicit "agent" here
                                    agent_turn_count,
                                )

                                # Get the audio URL if we've accumulated audio data
                                audio_url = None
                                if len(agent_audio_buffer) > 100:
                                    # Save the audio chunk
                                    audio_url = await save_audio_chunk(
                                        bytes(agent_audio_buffer),
                                        test_id,
                                        call_sid,
                                        "agent",  # Use explicit "agent" here
                                        agent_turn_count,
                                    )

                                    # Also add to full conversation recording
                                    if is_recording_full_conversation:
                                        full_conversation_audio.extend(
                                            agent_audio_buffer
                                        )

                                    # Clear the buffer for the next chunk
                                    agent_audio_buffer.clear()

                                # Create single conversation turn with all data
                                turn_data = {
                                    "speaker": "agent",  # Explicitly use "agent"
                                    "text": text,
                                    "timestamp": datetime.now().isoformat(),
                                    "transcription_url": transcript_url,
                                }

                                if audio_url:
                                    turn_data["audio_url"] = audio_url

                                # Only save this turn ONCE to the conversation
                                from app.services.evaluator import evaluator_service

                                if test_id in evaluator_service.active_tests:
                                    if (
                                        "conversation"
                                        not in evaluator_service.active_tests[test_id]
                                    ):
                                        evaluator_service.active_tests[test_id][
                                            "conversation"
                                        ] = []

                                    # Add the turn directly to the conversation array
                                    evaluator_service.active_tests[test_id][
                                        "conversation"
                                    ].append(turn_data)

                                    # Save to DynamoDB
                                    from app.services.dynamodb_service import (
                                        dynamodb_service,
                                    )

                                    dynamodb_service.save_test(
                                        test_id, evaluator_service.active_tests[test_id]
                                    )
                                    logger.info(
                                        f"Saved agent turn to conversation: {text[:50]}..."
                                    )

                                # Update last transcription time
                                last_transcription_time = datetime.now()

                        # Handle audio response from AI evaluator
                        elif response[
                            "type"
                        ] == "response.audio.delta" and response.get("delta"):
                            try:
                                # Set current speaker to evaluator - this is FROM OpenAI TO the call
                                current_speaker = "evaluator"

                                # Decode audio data
                                audio_payload = base64.b64decode(response["delta"])

                                # Accumulate evaluator audio data
                                evaluator_audio_buffer.extend(audio_payload)

                                # Also add to full conversation recording
                                if is_recording_full_conversation:
                                    full_conversation_audio.extend(audio_payload)

                                # Forward to Twilio
                                audio_delta = {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": response["delta"]},
                                }
                                await websocket.send_json(audio_delta)

                            except Exception as e:
                                logger.error(f"Error processing audio data: {e}")

                            if response_start_timestamp_twilio is None:
                                response_start_timestamp_twilio = latest_media_timestamp

                            # Update last_assistant_item safely
                            if response.get("item_id"):
                                last_assistant_item = response["item_id"]

                            await send_mark(websocket, stream_sid)

                        # Handle text/content from the evaluator
                        elif response.get("type") == "response.content_part.added":
                            # This is from OpenAI (evaluator)
                            current_speaker = "evaluator"

                            if "content_part" in response:
                                content_part = response["content_part"]
                                content = ""

                                if isinstance(content_part, str):
                                    content = content_part
                                else:
                                    content = content_part.get("text", "")

                                if content:
                                    logger.info(f"Evaluator content: {content}")
                                    response_text_buffer += content

                        # Handle audio transcript (the text OpenAI is saying)
                        elif response.get("type") == "response.audio_transcript.delta":
                            # This is from OpenAI (evaluator)
                            current_speaker = "evaluator"

                            if "delta" in response:
                                # Check if delta is a string or object
                                delta = response["delta"]
                                if isinstance(delta, str):
                                    transcript = delta
                                else:
                                    transcript = delta.get("text", "")

                                if transcript:
                                    response_text_buffer += transcript

                        # When a response is completed
                        elif response.get("type") == "response.done":
                            current_speaker = "evaluator"
                            logger.info("Response marked as done")

                            # Save accumulated evaluator audio if we have any
                            audio_url = None
                            if (
                                len(evaluator_audio_buffer) > 100
                            ):  # Only save if we have meaningful audio
                                # Save the audio chunk
                                audio_url = await save_audio_chunk(
                                    bytes(evaluator_audio_buffer),
                                    test_id,
                                    call_sid,
                                    "evaluator",  # Explicitly use "evaluator" here
                                    evaluator_turn_count,
                                )

                                # Clear the buffer for the next chunk
                                evaluator_audio_buffer.clear()

                                # Increment turn counter
                                evaluator_turn_count += 1

                            # Record the turn with the accumulated text if we have any
                            if response_text_buffer and test_id:
                                # Save transcription
                                transcript_url = await save_transcription(
                                    response_text_buffer,
                                    test_id,
                                    call_sid,
                                    "evaluator",  # Explicitly use "evaluator" here
                                    evaluator_turn_count - 1,  # Use the last turn count
                                )

                                # Create turn data with all information
                                turn_data = {
                                    "speaker": "evaluator",  # Explicitly "evaluator"
                                    "text": response_text_buffer,
                                    "timestamp": datetime.now().isoformat(),
                                    "transcription_url": transcript_url,
                                }

                                if audio_url:
                                    turn_data["audio_url"] = audio_url

                                # Save the turn data ONCE directly to the conversation
                                from app.services.evaluator import evaluator_service

                                if test_id in evaluator_service.active_tests:
                                    if (
                                        "conversation"
                                        not in evaluator_service.active_tests[test_id]
                                    ):
                                        evaluator_service.active_tests[test_id][
                                            "conversation"
                                        ] = []

                                    # Add the turn
                                    evaluator_service.active_tests[test_id][
                                        "conversation"
                                    ].append(turn_data)

                                    # Save to DynamoDB
                                    from app.services.dynamodb_service import (
                                        dynamodb_service,
                                    )

                                    dynamodb_service.save_test(
                                        test_id, evaluator_service.active_tests[test_id]
                                    )
                                    logger.info(
                                        f"Saved evaluator turn to conversation: {response_text_buffer[:50]}..."
                                    )

                                response_text_buffer = ""

                                # Update last transcription time
                                last_transcription_time = datetime.now()

                            # Clear markers
                            mark_queue.clear()
                            last_assistant_item = None
                            response_start_timestamp_twilio = None

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
                    logger.error(f"Error in evaluator_audio: {e}")
                    import traceback

                except WebSocketDisconnect:
                    logger.warning("Client disconnected.")
                    if openai_ws.state == State.OPEN:
                        await openai_ws.close()
                    logger.error(traceback.format_exc())

            # Important - run both functions concurrently
            await asyncio.gather(agent_audio(), evaluator_audio())

    except Exception as e:
        logger.error(f"Error in handle_media_stream: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        # When done, save any remaining audio and complete the test
        if test_id and call_sid:
            logger.info(
                f"WebSocket connection ended for test_id={test_id}, call_sid={call_sid}"
            )

            # Save any remaining audio in buffers
            if len(agent_audio_buffer) > 100:
                await save_audio_chunk(
                    bytes(agent_audio_buffer),
                    test_id,
                    call_sid,
                    "agent",
                    agent_turn_count,
                )

            if len(evaluator_audio_buffer) > 100:
                await save_audio_chunk(
                    bytes(evaluator_audio_buffer),
                    test_id,
                    call_sid,
                    "evaluator",
                    evaluator_turn_count,
                )

            # Save the full conversation recording if available
            if is_recording_full_conversation and len(full_conversation_audio) > 1000:
                try:
                    from app.services.s3_service import s3_service

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    key = f"tests/{test_id}/calls/{call_sid}/full_conversation_{timestamp}.wav"
                    s3_service.s3_client.put_object(
                        Bucket=s3_service.bucket_name,
                        Key=key,
                        Body=bytes(full_conversation_audio),
                        ContentType="audio/wav",
                    )
                    full_recording_url = f"s3://{s3_service.bucket_name}/{key}"
                    logger.info(
                        f"Full conversation recording saved to: {full_recording_url}"
                    )
                except Exception as e:
                    logger.error(f"Error saving full conversation recording: {str(e)}")

            # Process the call to generate evaluation report
            try:
                from app.services.evaluator import evaluator_service

                if test_id in evaluator_service.active_tests:
                    conversation = evaluator_service.active_tests[test_id].get(
                        "conversation", []
                    )

                    if conversation:
                        logger.info(
                            f"Found {len(conversation)} conversation turns for test {test_id}"
                        )

                        # Debug log the conversation content
                        for i, turn in enumerate(conversation):
                            logger.info(
                                f"Turn {i}: {turn.get('speaker')} - {turn.get('text')[:50]}..."
                            )
                            if "audio_url" in turn:
                                logger.info(f"  Audio URL: {turn.get('audio_url')}")
                            if "transcription_url" in turn:
                                logger.info(
                                    f"  Transcript URL: {turn.get('transcription_url')}"
                                )

                        # Update test status
                        evaluator_service.active_tests[test_id]["status"] = "completed"
                        evaluator_service.active_tests[test_id][
                            "end_time"
                        ] = datetime.now().isoformat()

                        # Force a successful report generation
                        logger.info(f"Generating final report for test {test_id}")
                        report = (
                            await evaluator_service.generate_report_from_conversation(
                                test_id, conversation
                            )
                        )
                        logger.info(f"Final report generated with ID: {report.id}")
                    else:
                        logger.error(f"No conversation turns found for test {test_id}")
                else:
                    logger.error(f"Test {test_id} not found in active_tests")
            except Exception as eval_error:
                logger.error(f"Error during final report generation: {str(eval_error)}")
                import traceback

                logger.error(f"Evaluation error traceback: {traceback.format_exc()}")


# Function to capture stream_sid from stream start events
async def update_stream_sid(connection_id: str, stream_sid: str):
    """Update the stream_sid for a connection"""
    if connection_id in active_connections:
        active_connections[connection_id]["stream_sid"] = stream_sid
        logger.error(f"Updated stream_sid for connection {connection_id}: {stream_sid}")


async def initialize_session(openai_ws):

    # Import knowledge base
    from app.services.evaluator import evaluator_service

    # Get test data if available

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
