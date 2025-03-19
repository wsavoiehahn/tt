# app/websocket_handlers.py
from datetime import datetime
import os
import json
import base64
import asyncio
import logging
import time
import websockets
import websockets.connection
from websockets.protocol import State
from fastapi import WebSocket, WebSocketDisconnect
from twilio.rest import Client
from app.config import config
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
    "conversation.item.input_audio_transcription.failed",
    "conversation.item.input_audio_transcription.completed",
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


async def handle_media_stream(websocket: WebSocket):
    """WebSocket endpoint for media streaming."""
    await websocket.accept()
    logger.info("WebSocket connection established")

    # Create buffers to accumulate audio data by speaker
    evaluator_audio_buffer = bytearray()
    agent_audio_buffer = bytearray()
    full_text_conversation = []
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

    async def send_mark(connection, stream_sid):
        if stream_sid:
            mark_event = {
                "event": "mark",
                "streamSid": stream_sid,
                "mark": {"name": "responsePart"},
            }
            await connection.send_json(mark_event)
            mark_queue.append("responsePart")

    try:
        # Connect to OpenAI
        async with websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
            additional_headers={
                "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as openai_ws:
            # Initialize the session with a default prompt (we don't have test_id yet)

            logger.info("OpenAI session initialized")

            # this is a hack to get the test_id customParameter early on, since in twilio it can only be found
            async for message in websocket.iter_text():
                data = json.loads(message)
                if data["event"] == "start":
                    stream_sid = data["start"]["streamSid"]
                    call_sid = data["start"]["callSid"]
                    test_id = data["start"].get("customParameters", {}).get("test_id")
                    break
            await initialize_session(openai_ws, test_id)

            async def agent_audio():
                """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
                nonlocal stream_sid, latest_media_timestamp, call_sid, test_id, current_speaker, agent_turn_count, full_text_conversation
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
                except (WebSocketDisconnect, RuntimeError) as e:
                    logger.warning(f"agent_audio: WebSocket error: {e}")
                    if openai_ws.state == State.OPEN:
                        await openai_ws.close()
                        await websocket.close()
                except Exception as e:
                    logger.error(f"Error in agent_audio: {str(e)}")
                finally:
                    await websocket.close()
                    await openai_ws.close()
                    logger.info(f"agent_audio task completed for call {call_sid}")

            async def evaluator_audio():
                """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
                nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio, test_id
                nonlocal current_speaker, evaluator_audio_buffer, full_conversation_audio
                nonlocal evaluator_turn_count, agent_turn_count, last_transcription_time, full_text_conversation

                response_text_buffer = ""

                try:

                    async for openai_message in openai_ws:
                        response = json.loads(openai_message)
                        if response["type"] in LOG_EVENT_TYPES:
                            logger.info(f"Received event: {response['type']}")

                        # Handle transcribed input from the agent
                        if response.get("type") in [
                            "conversation.item.input_audio_transcription.completed",
                        ]:
                            text = response.get("transcript", "")
                            if text:
                                current_speaker = "agent"
                                logger.info(f"Agent transcription from OpenAI: {text}")

                                # Save transcription
                                transcript_url = await save_transcription(
                                    text,
                                    test_id,
                                    call_sid,
                                    current_speaker,
                                    agent_turn_count,
                                )

                                # Append to full conversation
                                full_text_conversation.append(
                                    {
                                        "speaker": current_speaker,
                                        "text": text,
                                        "timestamp": datetime.now().isoformat(),
                                    }
                                )

                                # Save audio chunk if available
                                audio_url = None
                                if len(agent_audio_buffer) > 100:
                                    audio_url = await save_audio_chunk(
                                        bytes(agent_audio_buffer),
                                        test_id,
                                        call_sid,
                                        current_speaker,
                                        agent_turn_count,
                                    )

                                    agent_audio_buffer.clear()

                                # Add to conversation history once
                                turn_data = {
                                    "speaker": current_speaker,
                                    "text": text,
                                    "timestamp": datetime.now().isoformat(),
                                    "transcription_url": transcript_url,
                                }

                                if audio_url:
                                    turn_data["audio_url"] = audio_url

                                from app.services.evaluator import evaluator_service
                                from app.services.dynamodb_service import (
                                    dynamodb_service,
                                )

                                if test_id in evaluator_service.active_tests:
                                    evaluator_service.active_tests[test_id].setdefault(
                                        "conversation", []
                                    ).append(turn_data)
                                    dynamodb_service.save_test(
                                        test_id,
                                        evaluator_service.active_tests[test_id],
                                    )
                                    logger.info(f"Saved agent turn: {text[:50]}...")
                                    agent_turn_count += 1

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
                                logger.info("*****saving evaluator text*****")
                                full_text_conversation.append(
                                    {
                                        "speaker": "evaluator",
                                        "text": response_text_buffer,
                                        "timestamp": datetime.now().isoformat(),
                                    }
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

                            if any(
                                "bye" in content.get("transcript", "")
                                for item in response.get("response", {}).get(
                                    "output", []
                                )
                                for content in item.get("content", [])
                                if content.get("type") == "audio"
                            ):
                                time.sleep(1)
                                logger.info(f"ending call: {call_sid}")
                                call = client.calls(call_sid).update(status="completed")
                                await websocket.close()

                except (
                    websockets.exceptions.ConnectionClosed,
                    WebSocketDisconnect,
                ) as e:
                    logger.error(f"Client disconnected error: {e}")
                    if (
                        openai_ws.state == State.OPEN
                        or not websocket.client_state.name == "closed"
                    ):
                        await websocket.close()
                        await openai_ws.close()
                except Exception as e:
                    logger.error(f"Error in evaluator_audio: {e}")
                    import traceback

                    logger.error(traceback.format_exc())
                finally:
                    await websocket.close()
                    await openai_ws.close()

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
            if full_text_conversation:
                try:
                    # Save full text conversation to S3
                    from app.services.s3_service import s3_service
                    from app.services.evaluator import evaluator_service

                    # Convert to formatted text
                    formatted_text = "\n\n".join(
                        [
                            f"{turn['timestamp']} - {turn['speaker']}:\n{turn['text']}"
                            for turn in full_text_conversation
                        ]
                    )

                    # Save to S3
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    key = f"tests/{test_id}/calls/{call_sid}/full_conversation_text_{timestamp}.txt"
                    s3_service.s3_client.put_object(
                        Bucket=s3_service.bucket_name,
                        Key=key,
                        Body=formatted_text.encode("utf-8"),
                        ContentType="text/plain",
                    )

                    # Also save structured data for easier processing
                    json_key = f"tests/{test_id}/calls/{call_sid}/full_conversation_text_{timestamp}.json"
                    s3_service.s3_client.put_object(
                        Bucket=s3_service.bucket_name,
                        Key=json_key,
                        Body=json.dumps(full_text_conversation),
                        ContentType="application/json",
                    )

                    # Add the text conversation URLs to the test data
                    if test_id in evaluator_service.active_tests:
                        evaluator_service.active_tests[test_id][
                            "full_text_conversation"
                        ] = {
                            "text_url": f"s3://{s3_service.bucket_name}/{key}",
                            "json_url": f"s3://{s3_service.bucket_name}/{json_key}",
                        }

                        # Save to DynamoDB
                        dynamodb_service.save_test(
                            test_id, evaluator_service.active_tests[test_id]
                        )

                    logger.info(
                        f"Full text conversation saved to: s3://{s3_service.bucket_name}/{key}"
                    )
                except Exception as e:
                    logger.error(f"Error saving full text conversation: {str(e)}")

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


# Function to capture stream_sid from stream start events
async def update_stream_sid(connection_id: str, stream_sid: str):
    """Update the stream_sid for a connection"""
    if connection_id in active_connections:
        active_connections[connection_id]["stream_sid"] = stream_sid
        logger.error(f"Updated stream_sid for connection {connection_id}: {stream_sid}")


async def initialize_session(openai_ws, test_id):

    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": _create_system_prompt(test_id),
            "modalities": ["text", "audio"],
            "temperature": 0.7,
            "input_audio_transcription": {"model": "whisper-1", "language": "en"},
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
    # await send_initial_conversation_item(openai_ws)


def _create_system_prompt(test_id) -> str:
    """Create a system prompt based on persona, behavior, and question."""
    # Import knowledge base
    from app.services.evaluator import evaluator_service

    # I decided I was going to use only a single test ever and I don't want to refactor the entire codebase so this ugly bit of code will remain
    # TODO figure out how to get the testid so I dont need to do this hack
    # test_case = list(evaluator_service.active_tests.values())[0]["test_case"]
    test_case = evaluator_service.active_tests[test_id]["test_case"]
    persona_name = test_case["config"]["persona_name"]
    behavior_name = test_case["config"]["behavior_name"]
    question = test_case["config"]["questions"][0]  # assume only 1 question

    persona_traits = ", ".join(config.get_persona_traits(persona_name))
    behavior_chars = ", ".join(config.get_behavior_characteristics(behavior_name))
    special_instructions = test_case["config"]["special_instructions"]
    return f"""
        You are simulating a customer with the following persona: {persona_name}
        Traits: {persona_traits}
        
        You are currently exhibiting the following behavior: {behavior_name}
        Characteristics: {behavior_chars}
        
        You have hidden special instructions: {special_instructions}
        You are calling an AI customer service agent.
        You need to ask about the following question: "{question}"
        
        Use natural, conversational language appropriate for your persona and behavior.
        Respond to the agent's questions and provide information as needed, but stay in character.
        """


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
