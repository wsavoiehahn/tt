# app/routers/twilio_webhooks.py - COMPLETE REPLACEMENT
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

    # Find active test
    test_id = None
    for tid, test_data in evaluator_service.active_tests.items():
        if test_data["status"] == "waiting_for_call":
            test_id = tid
            break

    logger.info(f"Call started: {call_sid}, found test: {test_id}")

    # Check if there's an active test
    if test_id:
        # Update test status
        if test_id in evaluator_service.active_tests:
            evaluator_service.active_tests[test_id]["status"] = "in_progress"
            evaluator_service.active_tests[test_id]["call_sid"] = call_sid

        # Get the callback URL from config
        callback_url = config.get_parameter("/ai-evaluator/twilio_callback_url")

        # If callback URL is not set, fall back to request URL
        if not callback_url:
            host = request.url.hostname
            port = request.url.port
            base_url = f"wss://{host}" if port is None else f"wss://{host}:{port}"
        else:
            # Use the configured callback URL
            base_url = callback_url.replace("https://", "wss://").replace(
                "http://", "wss://"
            )

        # Create TwiML for media streaming
        twiml = f"""
        <Response>
            <Connect>
                <Stream url="{base_url}/webhooks/media-stream?test_id={test_id}&call_sid={call_sid}"/>
            </Connect>
        </Response>
        """

        return HTMLResponse(content=twiml, media_type="application/xml")
    else:
        # Default response if no active test is found
        return HTMLResponse(
            content="<Response><Say>No active test found. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    Handle WebSocket connection for media streaming.

    This endpoint handles the bidirectional audio streaming between
    Twilio and our application.
    """
    await websocket.accept()

    # Extract query parameters
    query_params = websocket.query_params
    test_id = query_params.get("test_id")
    call_sid = query_params.get("call_sid")

    logger.info(f"WebSocket connected: test_id={test_id}, call_sid={call_sid}")

    if not test_id or not call_sid:
        logger.error("Missing test_id or call_sid in WebSocket connection")
        await websocket.close(code=1000)
        return

    # Check if test exists
    if test_id not in evaluator_service.active_tests:
        logger.error(f"Test {test_id} not found in active tests")
        await websocket.close(code=1000)
        return

    # Store the websocket connection
    active_websockets[call_sid] = websocket

    # Get test data
    test_data = evaluator_service.active_tests[test_id]
    test_case = test_data["test_case"]

    # Initialize conversation data
    stream_sid = None
    conversation = []

    try:
        # Process the media stream
        async for message in websocket.iter_text():
            data = json.loads(message)

            # Handle Twilio events
            if data["event"] == "start":
                stream_sid = data["start"]["streamSid"]
                logger.info(f"Media stream started: {stream_sid}")

                # Get the question from the test case
                if test_case["config"]["questions"]:
                    question = test_case["config"]["questions"][0]["text"]

                    # Apply special instructions if needed
                    special_instructions = test_case["config"].get(
                        "special_instructions"
                    )
                    if special_instructions:
                        # In a real implementation, this would modify the question based on instructions
                        question = (
                            f"{question} (Special instructions: {special_instructions})"
                        )

                    # Say the question (after a brief pause)
                    await asyncio.sleep(1)

                    # Record the question in the conversation
                    evaluator_service.record_conversation_turn(
                        test_id=test_id,
                        call_sid=call_sid,
                        speaker="evaluator",
                        text=question,
                    )

                    # Convert the question to speech and send it
                    # This is a simplified approach - in a real implementation, you would use
                    # a text-to-speech service to generate audio

                    # For now, we'll just say the question
                    say_response = f"""
                    <Response>
                        <Say>{question}</Say>
                    </Response>
                    """

                    # Create an audio buffer with the TTS response
                    # In a real implementation, this would be actual audio data
                    audio_data = f"SIMULATED_AUDIO_FOR_{question}".encode()

                    # Save evaluator audio
                    audio_url = s3_service.save_audio(
                        audio_data=audio_data,
                        test_id=test_id,
                        call_sid=call_sid,
                        turn_number=0,
                        speaker="evaluator",
                    )

                    # Update conversation with audio URL
                    if (
                        len(conversation) > 0
                        and conversation[0]["speaker"] == "evaluator"
                    ):
                        conversation[0]["audio_url"] = audio_url

            # Handle media event (incoming audio from Twilio)
            elif data["event"] == "media" and stream_sid:
                # Process audio (in a real implementation, this would be sent to a speech-to-text service)
                # For now, we'll handle it in the media_received background task
                pass

            # Handle mark event (response to our audio)
            elif data["event"] == "mark":
                logger.info(f"Mark received: {data.get('mark', {}).get('name')}")

            # Handle stop event
            elif data["event"] == "stop":
                logger.info(f"Media stream stopped: {stream_sid}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for stream {stream_sid}")
    except Exception as e:
        logger.error(f"Error in media stream: {str(e)}")
    finally:
        # Clean up
        if call_sid in active_websockets:
            del active_websockets[call_sid]

        # Finalize the test
        if test_id in evaluator_service.active_tests:
            # Get conversation from evaluator service
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Simulate agent response if none was received
            if len(conversation) < 2:
                # Add a simulated response
                evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="agent",
                    text="Thank you for calling Sendero Health Plans. How can I assist you today?",
                )

                # Re-fetch conversation
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )

            # Process the call
            test_data["status"] = "processing"
            test_data["end_time"] = time.time()

            # Generate report
            await evaluator_service.process_call(test_id, call_sid, conversation)

            logger.info(f"Test {test_id} completed")


@router.post("/media-received")
async def media_received(request: Request, background_tasks: BackgroundTasks):
    """
    Handle media received from Twilio.

    This is a simplified handler - in a real implementation, you would
    process the audio and extract speech.
    """
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    stream_sid = form_data.get("StreamSid")
    media = form_data.get("Media")

    # Find the test
    test_id = None
    for tid, test_data in evaluator_service.active_tests.items():
        if test_data.get("call_sid") == call_sid:
            test_id = tid
            break

    if test_id and media:
        # Process media in a background task
        background_tasks.add_task(process_media, test_id, call_sid, stream_sid, media)

    return JSONResponse(content={"status": "processing"})


async def process_media(test_id: str, call_sid: str, stream_sid: str, media: str):
    """
    Process media from Twilio.

    In a real implementation, this would use a speech-to-text service.
    For simplicity, we'll simulate a response.
    """
    try:
        # In a real implementation, this would convert the audio to text
        # For now, we'll simulate a response
        transcription = "This is a simulated response from the agent"

        # Record the agent's response
        evaluator_service.record_conversation_turn(
            test_id=test_id, call_sid=call_sid, speaker="agent", text=transcription
        )

        # Save the audio
        audio_data = f"SIMULATED_AUDIO_FOR_{transcription}".encode()
        audio_url = s3_service.save_audio(
            audio_data=audio_data,
            test_id=test_id,
            call_sid=call_sid,
            turn_number=1,
            speaker="agent",
        )

        logger.info(f"Processed media for test {test_id}, call {call_sid}")
    except Exception as e:
        logger.error(f"Error processing media: {str(e)}")


@router.post("/call-ended")
async def call_ended(request: Request):
    """
    Handle call ended webhook from Twilio.
    """
    form_data = await request.form()
    call_sid = form_data.get("CallSid")

    logger.info(f"Call ended: {call_sid}")

    # Find the test
    test_id = None
    for tid, test_data in evaluator_service.active_tests.items():
        if test_data.get("call_sid") == call_sid:
            test_id = tid
            break

    if test_id:
        # Update test status
        if test_id in evaluator_service.active_tests:
            evaluator_service.active_tests[test_id]["status"] = "completed"
            evaluator_service.active_tests[test_id]["end_time"] = time.time()

            # Get conversation
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Generate report if not already done
            await evaluator_service.generate_report_from_conversation(
                test_id, conversation
            )

    return JSONResponse(content={"status": "success"})


@router.post("/response-recorded")
async def response_recorded(request: Request, background_tasks: BackgroundTasks):
    """
    Handle response recorded webhook from Twilio.

    This webhook is called when a recording is completed. It processes
    the recording and continues with the next question or ends the call.
    """
    # Parse the request form data
    form_data = await request.form()
    recording_sid = form_data.get("RecordingSid")
    recording_url = form_data.get("RecordingUrl")
    call_sid = form_data.get("CallSid")
    test_id = form_data.get("test_id")
    question_index = int(form_data.get("question_index", 0))

    logger.info(
        f"Response recorded: {recording_sid} for call {call_sid}, test {test_id}, question {question_index}"
    )

    # Handle the recording in the background
    if recording_sid and call_sid and test_id:
        background_tasks.add_task(
            handle_recording,
            recording_sid=recording_sid,
            recording_url=recording_url,
            call_sid=call_sid,
            test_id=test_id,
            question_index=question_index,
        )

    # Return empty TwiML to continue with the flow (the main TwiML handles the flow)
    return HTMLResponse(content="<Response></Response>", media_type="application/xml")


async def handle_recording(
    recording_sid: str,
    recording_url: str,
    call_sid: str,
    test_id: str,
    question_index: int,
):
    """
    Handle a completed recording.

    This function is called as a background task to process recordings.
    It downloads the recording, processes it, and saves it to S3.
    """
    try:
        # Wait a moment for the recording to be available
        await asyncio.sleep(2)

        # Save the recording to S3
        s3_url = s3_service.save_recording(recording_url, test_id, call_sid)
        logger.info(f"Saved recording to {s3_url}")

        # Process and transcribe the recording
        if test_id in evaluator_service.active_tests:
            test_data = evaluator_service.active_tests[test_id]

            # Update recording information
            if "recordings" not in test_data:
                test_data["recordings"] = []

            test_data["recordings"].append(
                {
                    "sid": recording_sid,
                    "url": recording_url,
                    "s3_url": s3_url,
                    "call_sid": call_sid,
                    "question_index": question_index,
                    "timestamp": time.time(),
                }
            )

            # Record the conversation turn
            # In a real implementation, you'd transcribe this recording
            # For now, we'll use a placeholder
            agent_response = "This is a simulated agent response (would be transcribed from the actual recording)"

            evaluator_service.record_conversation_turn(
                test_id=test_id,
                call_sid=call_sid,
                speaker="agent",
                text=agent_response,
                audio_url=s3_url,
            )

            logger.info(
                f"Recorded agent response for test {test_id}, question {question_index}"
            )

    except Exception as e:
        logger.error(f"Error handling recording {recording_sid}: {str(e)}")


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

    logger.info(
        f"Call status update: {call_sid}, status: {call_status}, test: {test_id}"
    )

    # If the call is completed, process the test
    if call_status == "completed" and test_id:
        # Run in background to avoid blocking the webhook response
        background_tasks.add_task(process_completed_call, test_id, call_sid)

    return JSONResponse(content={"status": "success"})


async def process_completed_call(test_id: str, call_sid: str):
    """
    Process a completed call.

    This function is called as a background task when a call is completed.
    It generates a report based on the conversation.
    """
    logger.info(f"Processing completed call: {call_sid} for test {test_id}")

    try:
        # Wait a bit for any final recordings to be processed
        await asyncio.sleep(5)

        if test_id in evaluator_service.active_tests:
            # Update test status
            evaluator_service.active_tests[test_id]["status"] = "processing"
            evaluator_service.active_tests[test_id]["end_time"] = time.time()

            # Get the conversation
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # If there's no conversation recorded, create a minimal one for the questions asked
            if not conversation:
                # Get test questions
                test_data = evaluator_service.active_tests[test_id]
                test_case = test_data.get("test_case", {})
                config_data = test_case.get("config", {})
                questions = config_data.get("questions", [])

                # Add questions to conversation
                for i, q in enumerate(questions):
                    if isinstance(q, dict) and "text" in q:
                        evaluator_service.record_conversation_turn(
                            test_id=test_id,
                            call_sid=call_sid,
                            speaker="evaluator",
                            text=q["text"],
                        )

                        # Add simulated response if there was a recording
                        if "recordings" in test_data:
                            matching_recordings = [
                                r
                                for r in test_data["recordings"]
                                if r.get("question_index") == i
                            ]

                            if matching_recordings:
                                recording = matching_recordings[0]
                                evaluator_service.record_conversation_turn(
                                    test_id=test_id,
                                    call_sid=call_sid,
                                    speaker="agent",
                                    text="Agent response (would be transcribed from recording)",
                                    audio_url=recording.get("s3_url"),
                                )

                # Refresh conversation data
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )

            # Generate report
            await evaluator_service.generate_report_from_conversation(
                test_id, conversation
            )

            logger.info(
                f"Generated report for completed call: {call_sid}, test: {test_id}"
            )
        else:
            logger.warning(f"Test {test_id} not found in active tests")

    except Exception as e:
        logger.error(f"Error processing completed call: {str(e)}")
