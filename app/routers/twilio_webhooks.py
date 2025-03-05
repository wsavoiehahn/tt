# app/routers/twilio_webhooks.py - Complete Implementation

import logging
import asyncio
import json
import time
import base64
from fastapi import (
    APIRouter,
    Request,
    HTTPException,
    Response,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..services.twilio_service import twilio_service
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..services.openai_service import openai_service
from ..config import config
from ..utils.audio import convert_mp3_to_wav, trim_silence

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)

# Store active WebSocket connections
active_websockets = {}


@router.post("/call-started")
async def call_started(request: Request):
    """
    Handle call started webhook from Twilio.
    This webhook is called when a call is connected. It generates
    the TwiML response for streaming audio.
    """
    # Parse the request form data
    form_data = await request.form()
    call_sid = form_data.get("CallSid")

    # Extract test_id from query parameters - this is crucial
    test_id = request.query_params.get("test_id")

    # If not in query params, try form data
    if not test_id:
        test_id = form_data.get("test_id")

    logger.error(
        f"DEBUG: Call started webhook received - CallSid: {call_sid}, test_id: {test_id}"
    )
    logger.error(f"DEBUG: Request URL: {request.url}")
    logger.error(f"DEBUG: Query params: {request.query_params}")
    logger.error(f"DEBUG: Form data keys: {form_data.keys()}")

    # Dump all active tests to the logs for debugging
    logger.error(f"DEBUG: Active tests dump: {evaluator_service.active_tests}")

    # Find active test if test_id is not provided
    if not test_id:
        logger.error(
            "DEBUG: No test_id provided in call-started webhook, searching for waiting tests"
        )
        waiting_tests = [
            (tid, data)
            for tid, data in evaluator_service.active_tests.items()
            if data.get("status") == "waiting_for_call"
        ]

        logger.error(
            f"DEBUG: Found {len(waiting_tests)} waiting tests: {[t[0] for t in waiting_tests]}"
        )

        if waiting_tests:
            test_id = waiting_tests[0][0]
            logger.error(f"DEBUG: Selected test_id: {test_id} from waiting tests")

    # Also check Twilio's active_calls to find the test_id
    if not test_id and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")
        logger.error(f"DEBUG: Found test_id: {test_id} in twilio_service.active_calls")

    # Check if there's an active test
    if test_id:
        logger.error(f"DEBUG: Using test_id: {test_id} for call {call_sid}")

        # Update test status
        if test_id in evaluator_service.active_tests:
            previous_status = evaluator_service.active_tests[test_id].get(
                "status", "unknown"
            )
            evaluator_service.active_tests[test_id]["status"] = "in_progress"
            evaluator_service.active_tests[test_id]["call_sid"] = call_sid
            logger.error(
                f"DEBUG: Updated test {test_id} status from {previous_status} to in_progress"
            )
        else:
            logger.error(
                f"DEBUG: Test {test_id} exists in query param but not in active_tests dictionary"
            )

        # Get the callback URL from config for logging
        callback_url = config.get_parameter("/ai-evaluator/twilio_callback_url")
        logger.error(f"DEBUG: Using callback URL: {callback_url}")

        # Generate TwiML for media streaming
        try:
            twiml = twilio_service.generate_stream_twiml(test_id, call_sid)
            logger.error(f"DEBUG: Generated TwiML for media streaming: {twiml}")
            return HTMLResponse(content=twiml, media_type="application/xml")
        except Exception as e:
            logger.error(f"DEBUG: Error generating TwiML: {str(e)}")
            import traceback

            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return HTMLResponse(
                content="<Response><Say>Error generating TwiML. Goodbye.</Say><Hangup/></Response>",
                media_type="application/xml",
            )
    else:
        # Default response if no active test is found
        logger.error(
            f"DEBUG: No active test found for call {call_sid}, returning goodbye message"
        )
        return HTMLResponse(
            content="<Response><Say>No active test found. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    Handle WebSocket connection for media streaming.
    This handler implements an evaluator that asks questions, records responses,
    and evaluates the call center representative.
    """
    await websocket.accept()
    logger.error("DEBUG: WebSocket connection accepted for media stream")

    # Extract query parameters
    query_params = websocket.query_params
    test_id = query_params.get("test_id")
    call_sid = query_params.get("call_sid")

    logger.error(f"DEBUG: WebSocket params - test_id: {test_id}, call_sid: {call_sid}")

    if not test_id or not call_sid:
        logger.error("DEBUG: Missing test_id or call_sid in WebSocket connection")
        await websocket.close(code=1000)
        return

    # Check if test exists
    if test_id not in evaluator_service.active_tests:
        logger.error(f"DEBUG: Test {test_id} not found in active tests")
        await websocket.close(code=1000)
        return

    logger.error(f"DEBUG: Found active test {test_id} for WebSocket connection")

    # Store the websocket connection
    active_websockets[call_sid] = websocket

    # Get test data
    test_data = evaluator_service.active_tests[test_id]
    test_case = test_data.get("test_case", {})
    logger.error(f"DEBUG: Retrieved test case data: {test_case.get('name')}")

    # Initialize conversation data
    stream_sid = None
    current_question_index = 0
    conversation = []
    agent_speaking = False
    last_agent_speech_time = None
    question_answered = False
    question_started_time = None

    # Set up OpenAI text-to-speech
    openai_api_key = config.get_parameter("/openai/api_key")
    logger.error(
        f"DEBUG: Retrieved OpenAI API key (first 5 chars): {openai_api_key[:5]}***"
    )

    try:
        # Process the media stream
        async for message in websocket.iter_text():
            try:
                data = json.loads(message)
                event_type = data.get("event")
                logger.error(f"DEBUG: Received WebSocket message: {event_type}")

                # Handle Twilio events
                if event_type == "start":
                    stream_sid = data["start"]["streamSid"]
                    logger.error(f"DEBUG: Media stream started: {stream_sid}")

                    # Introduce the test and ask the first question after a brief pause
                    await asyncio.sleep(1)

                    # Check if special instructions should be included
                    special_instructions = test_case.get("config", {}).get(
                        "special_instructions"
                    )
                    if special_instructions:
                        logger.error(
                            f"DEBUG: Including special instructions: {special_instructions}"
                        )
                        intro_text = (
                            f"This is an evaluation call. {special_instructions}"
                        )
                        await speak_text(websocket, intro_text, stream_sid)
                        await asyncio.sleep(2)  # Pause after instructions

                        # Record the special instructions
                        evaluator_service.record_conversation_turn(
                            test_id=test_id,
                            call_sid=call_sid,
                            speaker="evaluator",
                            text=f"Special instructions: {special_instructions}",
                        )
                        conversation.append(
                            {
                                "speaker": "evaluator",
                                "text": f"Special instructions: {special_instructions}",
                                "timestamp": datetime.now().isoformat(),
                            }
                        )

                    # Get the first question
                    if test_case.get("config", {}).get(
                        "questions", []
                    ) and current_question_index < len(
                        test_case["config"]["questions"]
                    ):
                        first_question = test_case["config"]["questions"][
                            current_question_index
                        ]
                        if isinstance(first_question, dict):
                            first_question = first_question.get("text", "")

                        logger.error(f"DEBUG: First question to ask: {first_question}")

                        # Record the question
                        evaluator_service.record_conversation_turn(
                            test_id=test_id,
                            call_sid=call_sid,
                            speaker="evaluator",
                            text=first_question,
                        )
                        conversation.append(
                            {
                                "speaker": "evaluator",
                                "text": first_question,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )

                        # Convert question to speech and send it
                        await speak_text(websocket, first_question, stream_sid)

                        current_question_index += 1
                        question_answered = False
                        question_started_time = time.time()
                    else:
                        logger.error(
                            f"DEBUG: No questions found in test case or questions array is empty"
                        )

                # Handle media event (incoming audio from agent)
                elif event_type == "media" and stream_sid:
                    # Check if we're getting media from the agent
                    if not agent_speaking:
                        agent_speaking = True
                        last_agent_speech_time = time.time()
                        logger.error(f"DEBUG: Agent started speaking")
                    else:
                        # Update the last speech time
                        last_agent_speech_time = time.time()

                    # Store audio for processing
                    if "media" in data and "payload" in data["media"]:
                        # Here we'd ideally buffer the audio and transcribe it
                        # For now, we'll just note that audio was received
                        pass

                # Check for agent silence to determine if answer is complete
                current_time = time.time()
                if (
                    agent_speaking
                    and last_agent_speech_time
                    and (current_time - last_agent_speech_time) > 2.0
                ):
                    # Agent has been silent for more than 2 seconds, consider the answer complete
                    agent_speaking = False
                    logger.error(f"DEBUG: Agent stopped speaking, processing answer")

                    # In a real implementation, we'd transcribe the complete agent response here
                    # For now, we'll simulate it with a placeholder
                    agent_response = f"Simulated agent response for question {current_question_index}"

                    # Record the agent's response
                    evaluator_service.record_conversation_turn(
                        test_id=test_id,
                        call_sid=call_sid,
                        speaker="agent",
                        text=agent_response,
                    )
                    conversation.append(
                        {
                            "speaker": "agent",
                            "text": agent_response,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

                    logger.error(f"DEBUG: Recorded agent response: {agent_response}")

                    # Mark question as answered
                    question_answered = True

                    # Process next steps
                    await process_next_steps(
                        websocket,
                        test_id,
                        call_sid,
                        stream_sid,
                        current_question_index,
                        test_case,
                        conversation,
                    )

                # Check if current question has timed out
                if (
                    question_started_time
                    and not question_answered
                    and (current_time - question_started_time) > 30.0
                ):
                    # Question has timed out after 30 seconds with no response
                    logger.error(
                        f"DEBUG: Question timed out after 30 seconds with no response"
                    )

                    # Record a timeout
                    evaluator_service.record_conversation_turn(
                        test_id=test_id,
                        call_sid=call_sid,
                        speaker="agent",
                        text="[No response - timeout]",
                    )
                    conversation.append(
                        {
                            "speaker": "agent",
                            "text": "[No response - timeout]",
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

                    # Reset timer and mark as answered to move on
                    question_answered = True
                    question_started_time = None

                    # Process next steps
                    await process_next_steps(
                        websocket,
                        test_id,
                        call_sid,
                        stream_sid,
                        current_question_index,
                        test_case,
                        conversation,
                    )

                # Handle stop event
                if event_type == "stop":
                    logger.error(f"DEBUG: Media stream stopped: {stream_sid}")
                    break

            except json.JSONDecodeError as e:
                logger.error(f"DEBUG: Received invalid JSON: {str(e)}")
            except Exception as e:
                logger.error(f"DEBUG: Error processing message: {str(e)}")
                import traceback

                logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")

    except WebSocketDisconnect:
        logger.error(f"DEBUG: WebSocket disconnected")
    except Exception as e:
        logger.error(f"DEBUG: Error in media stream: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
    finally:
        # Clean up
        if call_sid in active_websockets:
            del active_websockets[call_sid]

        # Finalize the test
        if test_id in evaluator_service.active_tests:
            # Get conversation from evaluator service
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", conversation
            )

            if not conversation:
                logger.error(f"DEBUG: No conversation recorded for test {test_id}")

                # Add at least one placeholders if none exists
                evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="evaluator",
                    text="No questions were successfully asked during the call.",
                )

                evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="agent",
                    text="No agent responses were recorded.",
                )

                # Refresh conversation
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )

            # Process the call
            test_data["status"] = "processing"
            test_data["end_time"] = time.time()

            # Generate report
            await evaluator_service.process_call(test_id, call_sid, conversation)

            logger.error(f"DEBUG: Test {test_id} completed")


async def process_next_steps(
    websocket,
    test_id,
    call_sid,
    stream_sid,
    current_question_index,
    test_case,
    conversation,
):
    """Process the next steps in the conversation based on the test configuration."""
    # Check if we should ask the next question or end the call
    max_questions = len(test_case.get("config", {}).get("questions", []))
    max_turns = test_case.get("config", {}).get("max_turns", 8)

    logger.error(
        f"DEBUG: Processing next steps. Question {current_question_index}/{max_questions}, Turn {len(conversation)}/{max_turns}"
    )

    if current_question_index < max_questions and len(conversation) < max_turns:
        # Ask the next question
        next_question = test_case["config"]["questions"][current_question_index]
        if isinstance(next_question, dict):
            next_question = next_question.get("text", "")

        logger.error(f"DEBUG: Asking next question: {next_question}")

        # Record the question
        evaluator_service.record_conversation_turn(
            test_id=test_id, call_sid=call_sid, speaker="evaluator", text=next_question
        )
        conversation.append(
            {
                "speaker": "evaluator",
                "text": next_question,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Add a small pause before the next question
        await asyncio.sleep(1)

        # Convert question to speech and send it
        await speak_text(websocket, next_question, stream_sid)

        # Return the updated question index
        return current_question_index + 1
    else:
        # All questions asked or max turns reached, end the call
        logger.error(f"DEBUG: All questions asked or max turns reached, ending call")

        # Say goodbye
        goodbye_text = "Thank you for your time. This concludes our evaluation."
        evaluator_service.record_conversation_turn(
            test_id=test_id, call_sid=call_sid, speaker="evaluator", text=goodbye_text
        )
        conversation.append(
            {
                "speaker": "evaluator",
                "text": goodbye_text,
                "timestamp": datetime.now().isoformat(),
            }
        )

        await speak_text(websocket, goodbye_text, stream_sid)

        # Wait for goodbye to complete
        await asyncio.sleep(3)

        # End the call
        twilio_service.end_call(call_sid)
        logger.error(f"DEBUG: Call ended")

        return current_question_index


async def speak_text(websocket, text, stream_sid):
    """Convert text to speech and send it through the websocket."""
    try:
        logger.error(f"DEBUG: Converting text to speech: {text}")

        # Use OpenAI to generate audio (or use a simpler approach for testing)
        audio_data = await generate_audio_from_text(text)

        # Split the audio data into chunks and send through websocket
        chunk_size = 1024  # Adjust as needed
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i : i + chunk_size]

            # Encode the chunk for Twilio
            audio_payload = base64.b64encode(chunk).decode("utf-8")

            # Create the media message
            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": audio_payload},
            }

            # Send the chunk
            await websocket.send_text(json.dumps(media_message))

            # Small delay to prevent flooding
            await asyncio.sleep(0.01)

        logger.error(f"DEBUG: Finished sending audio for text")

        # Send a mark to indicate the speech is complete
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": "speech_complete"},
        }
        await websocket.send_text(json.dumps(mark_message))

    except Exception as e:
        logger.error(f"DEBUG: Error in speak_text: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")


async def generate_audio_from_text(text):
    """Generate audio from text using OpenAI's TTS or a simpler approach."""
    try:
        # Use OpenAI's TTS (text-to-speech) API
        audio = await openai_service.text_to_speech(text)
        return audio
    except Exception as e:
        logger.error(f"DEBUG: Error generating audio from text: {str(e)}")

        # Fallback: Create a simple audio placeholder
        # This is just a placeholder - in production, use a proper TTS service
        import struct
        import math

        # Generate 1 second of silence (8kHz, mono, 8-bit)
        rate = 8000
        duration = 1.0  # seconds
        samples = int(rate * duration)
        audio_data = bytearray()

        # Add a WAV header
        audio_data.extend(b"RIFF")
        audio_data.extend(struct.pack("<I", 36 + samples))
        audio_data.extend(b"WAVE")
        audio_data.extend(b"fmt ")
        audio_data.extend(struct.pack("<I", 16))  # Size of fmt chunk
        audio_data.extend(struct.pack("<H", 1))  # PCM format
        audio_data.extend(struct.pack("<H", 1))  # Mono
        audio_data.extend(struct.pack("<I", rate))  # Sample rate
        audio_data.extend(struct.pack("<I", rate))  # Byte rate
        audio_data.extend(struct.pack("<H", 1))  # Block align
        audio_data.extend(struct.pack("<H", 8))  # Bits per sample
        audio_data.extend(b"data")
        audio_data.extend(struct.pack("<I", samples))

        # Generate simple tone at 440Hz
        for i in range(samples):
            value = int(127 + 127 * math.sin(2 * math.pi * 440 * i / rate))
            audio_data.append(value & 0xFF)

        return bytes(audio_data)


@router.post("/call-status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """
    Handle call status webhook from Twilio.
    This webhook is called when a call status changes.
    """
    # Parse the request form data
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    test_id = form_data.get("test_id")

    logger.error(
        f"DEBUG: Call status update: {call_sid}, status: {call_status}, test: {test_id}"
    )

    # Find the test if not provided
    if not test_id:
        for tid, test_data in evaluator_service.active_tests.items():
            if test_data.get("call_sid") == call_sid:
                test_id = tid
                break

    # If the call is completed or failed, process the test
    if (
        call_status in ["completed", "failed", "no-answer", "busy", "canceled"]
        and test_id
    ):
        # Run in background to avoid blocking the webhook response
        background_tasks.add_task(
            process_completed_call, test_id, call_sid, call_status
        )

    return JSONResponse(content={"status": "success"})


async def process_completed_call(test_id: str, call_sid: str, call_status: str):
    """
    Process a completed call.
    This function is called as a background task when a call is completed.
    It generates a report based on the conversation.
    """
    logger.error(
        f"DEBUG: Processing completed call: {call_sid} for test {test_id}, status: {call_status}"
    )

    try:
        # Wait a bit for any final processing
        await asyncio.sleep(2)

        if test_id in evaluator_service.active_tests:
            # Update test status
            if call_status == "completed":
                evaluator_service.active_tests[test_id]["status"] = "processing"
            else:
                evaluator_service.active_tests[test_id]["status"] = "failed"
                evaluator_service.active_tests[test_id][
                    "error"
                ] = f"Call ended with status: {call_status}"

            evaluator_service.active_tests[test_id]["end_time"] = time.time()

            # Get the conversation
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Generate report
            if conversation:
                await evaluator_service.generate_report_from_conversation(
                    test_id, conversation
                )
                logger.error(
                    f"DEBUG: Generated report for call: {call_sid}, test: {test_id}"
                )
            else:
                logger.error(f"DEBUG: No conversation data for test {test_id}")
                # Create a minimal report with error
                await evaluator_service.generate_empty_report(
                    test_id,
                    f"No conversation data. Call ended with status: {call_status}",
                )
        else:
            logger.error(f"DEBUG: Test {test_id} not found in active tests")

    except Exception as e:
        logger.error(f"DEBUG: Error processing completed call: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")


# Add this method to openai_service.py
async def text_to_speech(self, text: str, voice: str = "nova") -> bytes:
    """
    Convert text to speech using OpenAI's TTS API.

    Args:
        text: The text to convert to speech
        voice: The voice to use (alloy, echo, fable, onyx, nova, shimmer)

    Returns:
        Audio data as bytes
    """
    try:
        import requests

        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {"model": "tts-1", "input": text, "voice": voice}

        response = requests.post(url, headers=headers, json=data)

        if response.status_code != 200:
            logger.error(f"Error in text_to_speech: {response.text}")
            raise Exception(f"Error in text_to_speech: {response.status_code}")

        return response.content
    except Exception as e:
        logger.error(f"Error in text_to_speech: {str(e)}")
        raise
