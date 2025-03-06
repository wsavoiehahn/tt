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
from twilio.twiml.voice_response import (
    VoiceResponse,
    Gather,
    Stream,
    Say,
    Pause,
    Record,
)

from ..services.twilio_service import twilio_service
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..services.openai_service import openai_service
from ..services.dynamodb_service import dynamodb_service
from ..config import config

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

    logger.error(f"DEBUG: Call started webhook received - Form data: {dict(form_data)}")
    logger.error(f"DEBUG: Request URL: {request.url}")
    logger.error(f"DEBUG: Query params: {dict(request.query_params)}")

    # Extract test_id from multiple possible sources
    test_id = request.query_params.get("test_id")  # From query params

    # If not in query params, try form data
    if not test_id:
        test_id = form_data.get("test_id")
        logger.error(f"DEBUG: Found test_id in form data: {test_id}")

    # If still not found, try to get from Twilio's active_calls
    if not test_id and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")
        logger.error(f"DEBUG: Found test_id in twilio_service.active_calls: {test_id}")

    logger.error(
        f"DEBUG: Call started webhook - CallSid: {call_sid}, test_id: {test_id}"
    )

    # First check if the test is in memory
    test_in_memory = test_id in evaluator_service.active_tests
    if test_in_memory:
        logger.error(f"DEBUG: Test {test_id} found in memory")
    else:
        logger.error(f"DEBUG: Test {test_id} not in memory, checking DynamoDB")

    # If not in memory, try DynamoDB
    if not test_in_memory and test_id:
        test_data = dynamodb_service.get_test(test_id)

        if test_data:
            logger.error(f"DEBUG: Test {test_id} found in DynamoDB")
            # Load test into memory
            evaluator_service.active_tests[test_id] = test_data
            test_in_memory = True
            logger.error(f"DEBUG: Loaded test data from DynamoDB: {test_data}")
        else:
            logger.error(f"DEBUG: Test {test_id} not found in DynamoDB")

            # Try to find a waiting test as a last resort
            waiting_tests = dynamodb_service.get_waiting_tests()
            if waiting_tests:
                logger.error(f"DEBUG: Found {len(waiting_tests)} waiting tests")
                waiting_test = waiting_tests[0]
                test_id = waiting_test.get("test_id")
                test_data = waiting_test.get("test_data")

                if test_id and test_data:
                    evaluator_service.active_tests[test_id] = test_data
                    test_in_memory = True
                    logger.error(f"DEBUG: Using waiting test {test_id} as fallback")
                else:
                    logger.error(f"DEBUG: Invalid waiting test data")
            else:
                logger.error(f"DEBUG: No waiting tests found")

    # Generate TwiML response based on test data
    if test_id and test_in_memory:
        logger.error(f"DEBUG: Generating TwiML for test {test_id}")

        # Update test status
        previous_status = evaluator_service.active_tests[test_id].get(
            "status", "unknown"
        )
        evaluator_service.active_tests[test_id]["status"] = "in_progress"
        evaluator_service.active_tests[test_id]["call_sid"] = call_sid

        logger.error(
            f"DEBUG: Updated test {test_id} status from {previous_status} to in_progress"
        )

        # Save to DynamoDB
        dynamodb_service.update_test_status(test_id, "in_progress")
        dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

        # Get test case data
        test_case = evaluator_service.active_tests[test_id].get("test_case", {})
        questions = test_case.get("config", {}).get("questions", [])
        special_instructions = test_case.get("config", {}).get("special_instructions")

        # Create TwiML
        response = VoiceResponse()

        # Add introduction
        response.say("Starting evaluation call.")
        response.pause(length=1)

        # Add special instructions if present
        if special_instructions:
            response.say(f"Special instructions: {special_instructions}")
            response.pause(length=1)

            # Record the special instructions as a conversation turn
            evaluator_service.record_conversation_turn(
                test_id=test_id,
                call_sid=call_sid,
                speaker="evaluator",
                text=f"Special instructions: {special_instructions}",
            )

        # Add first question if available
        if questions:
            first_question = questions[0]
            question_text = ""

            if isinstance(first_question, dict):
                question_text = first_question.get("text", "")
            else:
                question_text = first_question

            if question_text:
                response.say(question_text)

                # Record the question in the conversation
                evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="evaluator",
                    text=question_text,
                )

                # Record the response (15 second timeout, no beep)
                response.record(
                    action=f"{config.get_parameter('/ai-evaluator/twilio_callback_url', False) or request.url.scheme + '://' + request.url.netloc}/webhooks/recording?test_id={test_id}",
                    timeout=15,
                    playBeep=False,
                    transcribe=True,
                    transcribeCallback=f"{config.get_parameter('/ai-evaluator/twilio_callback_url', False) or request.url.scheme + '://' + request.url.netloc}/webhooks/transcription?test_id={test_id}",
                )

                # Update current question index
                evaluator_service.active_tests[test_id]["current_question_index"] = 1
                dynamodb_service.save_test(
                    test_id, evaluator_service.active_tests[test_id]
                )
            else:
                response.say("No question text found for this test.")
        else:
            response.say("No questions found for this test.")

        # Add closing
        response.pause(length=1)
        response.say("Thank you for your response. This concludes our evaluation.")

        logger.error(f"DEBUG: Generated TwiML: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")
    else:
        # Default TwiML if no test found
        response = VoiceResponse()
        response.say("Starting simplified evaluation call.")
        response.pause(length=1)
        response.say(f"No test found with the provided ID: {test_id}")
        response.pause(length=2)
        response.say("Thank you for your time. This concludes our test.")

        logger.error(f"DEBUG: Generated default TwiML: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/recording")
async def recording_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle recording webhook from Twilio.
    This webhook is called when a recording is completed.
    """
    form_data = await request.form()
    logger.error(f"DEBUG: Recording webhook received - Form data: {dict(form_data)}")

    recording_sid = form_data.get("RecordingSid")
    recording_url = form_data.get("RecordingUrl")
    call_sid = form_data.get("CallSid")
    test_id = request.query_params.get("test_id") or form_data.get("test_id")

    if not test_id and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")

    logger.error(
        f"DEBUG: Recording completed - SID: {recording_sid}, URL: {recording_url}, test_id: {test_id}"
    )

    # Save the recording to S3
    if test_id:
        background_tasks.add_task(
            process_recording,
            test_id=test_id,
            call_sid=call_sid,
            recording_sid=recording_sid,
            recording_url=recording_url,
        )

    # Generate TwiML for the next question
    try:
        if test_id and test_id in evaluator_service.active_tests:
            test_data = evaluator_service.active_tests[test_id]
            test_case = test_data.get("test_case", {})
            questions = test_case.get("config", {}).get("questions", [])
            current_index = test_data.get("current_question_index", 1)

            response = VoiceResponse()

            # Check if there are more questions
            if current_index < len(questions):
                next_question = questions[current_index]
                question_text = ""

                if isinstance(next_question, dict):
                    question_text = next_question.get("text", "")
                else:
                    question_text = next_question

                if question_text:
                    response.say(question_text)

                    # Record the question in the conversation
                    evaluator_service.record_conversation_turn(
                        test_id=test_id,
                        call_sid=call_sid,
                        speaker="evaluator",
                        text=question_text,
                    )

                    # Record the response
                    response.record(
                        action=f"{config.get_parameter('/ai-evaluator/twilio_callback_url', False) or request.url.scheme + '://' + request.url.netloc}/webhooks/recording?test_id={test_id}",
                        timeout=15,
                        playBeep=False,
                        transcribe=True,
                        transcribeCallback=f"{config.get_parameter('/ai-evaluator/twilio_callback_url', False) or request.url.scheme + '://' + request.url.netloc}/webhooks/transcription?test_id={test_id}",
                    )

                    # Update current question index
                    evaluator_service.active_tests[test_id][
                        "current_question_index"
                    ] = (current_index + 1)
                    dynamodb_service.save_test(
                        test_id, evaluator_service.active_tests[test_id]
                    )

                    return HTMLResponse(
                        content=str(response), media_type="application/xml"
                    )

            # If no more questions, end the call
            response.say("Thank you for your responses. This concludes our evaluation.")
            response.hangup()

            return HTMLResponse(content=str(response), media_type="application/xml")
        else:
            # Default response if test not found
            response = VoiceResponse()
            response.say("Recording received, but no active test found.")
            response.hangup()

            return HTMLResponse(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"DEBUG: Error generating next question: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. This concludes our evaluation.")
        response.hangup()

        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/transcription")
async def transcription_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle transcription webhook from Twilio.
    This webhook is called when a transcription is completed.
    """
    form_data = await request.form()
    logger.error(
        f"DEBUG: Transcription webhook received - Form data: {dict(form_data)}"
    )

    transcription_sid = form_data.get("TranscriptionSid")
    transcription_text = form_data.get("TranscriptionText")
    recording_sid = form_data.get("RecordingSid")
    call_sid = form_data.get("CallSid")
    test_id = request.query_params.get("test_id") or form_data.get("test_id")

    if not test_id and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")

    logger.error(
        f"DEBUG: Transcription completed - SID: {transcription_sid}, text: {transcription_text}, test_id: {test_id}"
    )

    # Save the transcription
    if test_id and transcription_text:
        if test_id in evaluator_service.active_tests:
            # Record agent's response in the conversation
            evaluator_service.record_conversation_turn(
                test_id=test_id,
                call_sid=call_sid,
                speaker="agent",
                text=transcription_text,
            )

            # Save to S3
            s3_service.save_transcription(
                transcription=transcription_text,
                test_id=test_id,
                call_sid=call_sid,
                turn_number=evaluator_service.active_tests[test_id].get(
                    "current_question_index", 1
                )
                - 1,
                speaker="agent",
            )

            # Save to DynamoDB
            dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

            logger.error(f"DEBUG: Saved transcription for test {test_id}")
        else:
            logger.error(f"DEBUG: Test {test_id} not found for transcription")

    # Return a success response (Twilio doesn't do anything with this)
    return JSONResponse(content={"status": "received"})


async def process_recording(
    test_id: str, call_sid: str, recording_sid: str, recording_url: str
):
    """
    Process a recording from Twilio.
    This function is called as a background task.
    """
    logger.error(
        f"DEBUG: Processing recording - SID: {recording_sid}, URL: {recording_url}, test_id: {test_id}"
    )

    try:
        # Download the recording using Twilio client
        recording_data = twilio_service.handle_recording_completed(
            recording_sid, recording_url, call_sid
        )

        if recording_data and "url" in recording_data:
            # Save to S3
            s3_url = s3_service.save_recording(recording_data["url"], test_id, call_sid)

            if s3_url and test_id in evaluator_service.active_tests:
                # Update test data with recording URL
                current_index = (
                    evaluator_service.active_tests[test_id].get(
                        "current_question_index", 1
                    )
                    - 1
                )
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )

                # Find the most recent agent turn
                for i in range(len(conversation) - 1, -1, -1):
                    if conversation[i].get("speaker") == "agent":
                        conversation[i]["audio_url"] = s3_url
                        break

                # Save updated conversation
                evaluator_service.active_tests[test_id]["conversation"] = conversation
                dynamodb_service.save_test(
                    test_id, evaluator_service.active_tests[test_id]
                )

                logger.error(f"DEBUG: Updated test with recording URL: {s3_url}")
        else:
            logger.error(f"DEBUG: Failed to download recording: {recording_data}")
    except Exception as e:
        logger.error(f"DEBUG: Error processing recording: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")


@router.post("/call-status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """
    Handle call status webhook from Twilio.
    This webhook is called when a call status changes.
    """
    form_data = await request.form()
    logger.error(f"DEBUG: Call status webhook received - Form data: {dict(form_data)}")

    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    test_id = request.query_params.get("test_id") or form_data.get("test_id")

    if not test_id and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")

    logger.error(
        f"DEBUG: Call status update - SID: {call_sid}, status: {call_status}, test_id: {test_id}"
    )

    # If call is completed or failed, process the test
    if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
        if test_id:
            # Run in background to avoid blocking
            background_tasks.add_task(
                process_completed_call, test_id, call_sid, call_status
            )

    # Return a success response
    return JSONResponse(content={"status": "received"})


async def process_completed_call(test_id: str, call_sid: str, call_status: str):
    """
    Process a completed call.
    This function is called as a background task.
    """
    logger.error(
        f"DEBUG: Processing completed call - SID: {call_sid}, status: {call_status}, test_id: {test_id}"
    )

    try:
        # Wait a moment for any final transcriptions
        await asyncio.sleep(2)

        # Update test status in memory
        if test_id in evaluator_service.active_tests:
            if call_status == "completed":
                evaluator_service.active_tests[test_id]["status"] = "completed"
            else:
                evaluator_service.active_tests[test_id]["status"] = "failed"
                evaluator_service.active_tests[test_id][
                    "error"
                ] = f"Call ended with status: {call_status}"

            evaluator_service.active_tests[test_id]["end_time"] = time.time()

            # Update in DynamoDB
            dynamodb_service.update_test_status(
                test_id, evaluator_service.active_tests[test_id]["status"]
            )
            dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

            # Get conversation
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Generate report
            if conversation:
                await evaluator_service.generate_report_from_conversation(
                    test_id, conversation
                )
                logger.error(f"DEBUG: Generated report for test {test_id}")
            else:
                logger.error(f"DEBUG: No conversation data for test {test_id}")
                await evaluator_service.generate_empty_report(
                    test_id,
                    f"No conversation data recorded. Call ended with status: {call_status}",
                )
        else:
            logger.error(
                f"DEBUG: Test {test_id} not found in memory for completed call"
            )

            # Try to get from DynamoDB
            test_data = dynamodb_service.get_test(test_id)

            if test_data:
                logger.error(
                    f"DEBUG: Found test {test_id} in DynamoDB for completed call"
                )
                evaluator_service.active_tests[test_id] = test_data

                # Update status
                evaluator_service.active_tests[test_id]["status"] = (
                    "completed" if call_status == "completed" else "failed"
                )
                evaluator_service.active_tests[test_id]["end_time"] = time.time()

                # Save to DynamoDB
                dynamodb_service.update_test_status(
                    test_id, evaluator_service.active_tests[test_id]["status"]
                )
                dynamodb_service.save_test(
                    test_id, evaluator_service.active_tests[test_id]
                )

                # Generate empty report
                await evaluator_service.generate_empty_report(
                    test_id,
                    f"Test completed but conversation data was lost. Call ended with status: {call_status}",
                )
            else:
                logger.error(
                    f"DEBUG: Test {test_id} not found in DynamoDB for completed call"
                )
    except Exception as e:
        logger.error(f"DEBUG: Error processing completed call: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
