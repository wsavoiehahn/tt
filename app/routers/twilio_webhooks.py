# app/routers/twilio_webhooks.py
import logging
import asyncio
from fastapi import APIRouter, Request, HTTPException, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any, Optional
import json
import time

from ..services.twilio_service import twilio_service
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..utils.audio import convert_mp3_to_wav, trim_silence

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)


@router.post("/call-started")
async def call_started(request: Request):
    """
    Handle call started webhook from Twilio.

    This webhook is called when a call is connected. It generates
    the TwiML response for the first question to ask.
    """
    # Parse the request form data
    form_data = await request.form()

    # Comprehensive logging
    logger.info("Call Started Webhook Received")
    logger.info("Form Data:")
    for key, value in form_data.items():
        logger.info(f"{key}: {value}")

    call_sid = form_data.get("CallSid")
    test_id = form_data.get("test_id")

    logger.info(f"Parsed Call SID: {call_sid}")
    logger.info(f"Parsed Test ID: {test_id}")

    # Check if test_id is a string
    if test_id:
        test_id = str(test_id)  # Ensure it's a string

    logger.info("Active Tests:")
    logger.info(json.dumps(list(evaluator_service.active_tests.keys()), indent=2))

    # Check if test_id exists in active tests
    if test_id in evaluator_service.active_tests:
        test_data = evaluator_service.active_tests[test_id]
        logger.info("Test Data Found:")
        logger.info(json.dumps(test_data, indent=2, default=str))

        current_question_index = test_data.get("current_question_index", 0)
        questions = (
            test_data.get("test_case", {}).get("config", {}).get("questions", [])
        )

        logger.info(f"Current Question Index: {current_question_index}")
        logger.info(f"Total Questions: {len(questions)}")

        if current_question_index < len(questions):
            question = questions[current_question_index]["text"]
            logger.info(f"Selected Question: {question}")

            twiml = twilio_service.generate_twiml_for_question(
                question, test_id, call_sid
            )
            return HTMLResponse(content=twiml, media_type="application/xml")
        else:
            logger.warning("No more questions to ask")

    # Default response with more specific logging
    logger.error(f"No active test found for test_id: {test_id}")
    return HTMLResponse(
        content="<Response><Say>No active test found. Goodbye.</Say><Hangup/></Response>",
        media_type="application/xml",
    )


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

    logger.info(
        f"Response recorded: {recording_sid} for call {call_sid}, test {test_id}"
    )

    # Handle the recording in the background
    if recording_sid and call_sid and test_id:
        background_tasks.add_task(
            handle_recording,
            recording_sid=recording_sid,
            recording_url=recording_url,
            call_sid=call_sid,
            test_id=test_id,
        )

    # Continue with the next question or end the call
    if test_id and test_id in evaluator_service.active_tests:
        test_data = evaluator_service.active_tests[test_id]
        current_question_index = test_data.get("current_question_index", 0)

        # Move to the next question
        current_question_index += 1
        evaluator_service.active_tests[test_id][
            "current_question_index"
        ] = current_question_index

        if current_question_index < len(test_data["test_case"]["config"]["questions"]):
            # Ask the next question
            question = test_data["test_case"]["config"]["questions"][
                current_question_index
            ]["text"]
            twiml = twilio_service.generate_twiml_for_question(
                question, test_id, call_sid
            )
            return HTMLResponse(content=twiml, media_type="application/xml")

    # End the call if no more questions
    return HTMLResponse(
        content="<Response><Say>Thank you for your responses. Goodbye.</Say><Hangup/></Response>",
        media_type="application/xml",
    )


@router.post("/response-gathered")
async def response_gathered(request: Request):
    """
    Handle speech response gathered from Twilio.

    This webhook is called when speech is gathered using <Gather>.
    It processes the speech and continues with the next question or ends the call.
    """
    # Parse the request form data
    form_data = await request.form()
    speech_result = form_data.get("SpeechResult")
    call_sid = form_data.get("CallSid")
    test_id = form_data.get("test_id")

    logger.info(
        f"Speech gathered: '{speech_result}' for call {call_sid}, test {test_id}"
    )

    # Save the transcription
    if speech_result and call_sid and test_id:
        if test_id in evaluator_service.active_tests:
            test_data = evaluator_service.active_tests[test_id]
            current_turn = test_data.get("current_turn", 0)
            s3_service.save_transcription(
                speech_result, test_id, call_sid, current_turn, "agent"
            )

    # Continue with the next question or end the call (similar to response-recorded)
    if test_id and test_id in evaluator_service.active_tests:
        test_data = evaluator_service.active_tests[test_id]
        current_question_index = test_data.get("current_question_index", 0)

        # Move to the next question
        current_question_index += 1
        evaluator_service.active_tests[test_id][
            "current_question_index"
        ] = current_question_index

        if current_question_index < len(test_data["test_case"]["config"]["questions"]):
            # Ask the next question
            question = test_data["test_case"]["config"]["questions"][
                current_question_index
            ]["text"]
            twiml = twilio_service.generate_twiml_for_question(
                question, test_id, call_sid
            )
            return HTMLResponse(content=twiml, media_type="application/xml")

    # End the call if no more questions
    return HTMLResponse(
        content="<Response><Say>Thank you for your responses. Goodbye.</Say><Hangup/></Response>",
        media_type="application/xml",
    )


@router.post("/recording-status")
async def recording_status(request: Request):
    """
    Handle recording status webhook from Twilio.

    This webhook is called when a recording status changes.
    It processes completed recordings.
    """
    # Parse the request form data
    form_data = await request.form()
    recording_sid = form_data.get("RecordingSid")
    recording_status = form_data.get("RecordingStatus")
    recording_url = form_data.get("RecordingUrl")
    call_sid = form_data.get("CallSid")

    logger.info(
        f"Recording status: {recording_status} for recording {recording_sid}, call {call_sid}"
    )

    if recording_status == "completed" and recording_url and call_sid:
        # Find the test ID associated with this call
        test_id = None
        for test_id_candidate, test_data in evaluator_service.active_tests.items():
            if "calls" in test_data and call_sid in test_data["calls"]:
                test_id = test_id_candidate
                break

        if test_id:
            # Save the recording to S3
            s3_url = s3_service.save_recording(recording_url, test_id, call_sid)
            logger.info(f"Saved recording to {s3_url}")

    return JSONResponse(content={"status": "success"})


async def handle_recording(
    recording_sid: str, recording_url: str, call_sid: str, test_id: str
):
    """
    Handle a completed recording.

    This function is called as a background task to process recordings.
    It downloads the recording, converts it to WAV, and saves it to S3.
    """
    try:
        # Wait a moment for the recording to be available
        await asyncio.sleep(2)

        # Save the recording to S3
        s3_url = s3_service.save_recording(recording_url, test_id, call_sid)

        # Process and transcribe the recording if needed
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
                    "timestamp": time.time(),
                }
            )

            # Use OpenAI for transcription (can be implemented in openai_service)
            # This would typically be done in the evaluator service

    except Exception as e:
        logger.error(f"Error handling recording {recording_sid}: {str(e)}")
