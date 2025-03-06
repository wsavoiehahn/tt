import logging
from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Pause, Say
from ..services.evaluator import evaluator_service

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)


@router.get("/test")
async def test_endpoint():
    logger.error("TEST ENDPOINT REACHED!")
    return {"message": "Test endpoint working"}


@router.post("/call-started")
async def call_started(request: Request):
    """Handle call started webhook with a simplified approach."""
    logger.error("CALL STARTED WEBHOOK INVOKED!")
    # Parse the form data
    form_data = await request.form()
    logger.error(f"Form data: {dict(form_data)}")

    # Get path params
    logger.error(f"Path params: {request.path_params}")

    # Parse the request form data
    call_sid = form_data.get("CallSid")

    # Extract test_id from query parameters
    test_id = request.query_params.get("test_id")

    # If not in query params, try form data
    if not test_id:
        test_id = form_data.get("test_id")

    logger.error(
        f"DEBUG: Simplified call-started handler - CallSid: {call_sid}, test_id: {test_id}"
    )

    # Import VoiceResponse here to avoid import issues
    from twilio.twiml.voice_response import VoiceResponse, Pause

    # Generate a simple voice response
    response = VoiceResponse()
    response.say("Starting simplified evaluation call.")
    response.pause(length=1)

    # Get the test data
    if test_id and test_id in evaluator_service.active_tests:
        test_data = evaluator_service.active_tests[test_id]
        test_case = test_data.get("test_case", {})
        questions = test_case.get("config", {}).get("questions", [])

        # Update status
        previous_status = test_data.get("status", "unknown")
        test_data["status"] = "in_progress"
        logger.error(
            f"DEBUG: Updated test {test_id} status from {previous_status} to in_progress"
        )

        # Add special instructions if available
        special = test_case.get("config", {}).get("special_instructions")
        if special:
            response.say(special)
            response.pause(length=1)

            # Record the special instructions
            evaluator_service.record_conversation_turn(
                test_id=test_id, call_sid=call_sid, speaker="evaluator", text=special
            )

        # Say the question
        if questions:
            first_question = questions[0]
            if isinstance(first_question, dict):
                question_text = first_question.get("text", "")
            else:
                question_text = first_question

            response.say(question_text)

            # Record the question
            evaluator_service.record_conversation_turn(
                test_id=test_id,
                call_sid=call_sid,
                speaker="evaluator",
                text=question_text,
            )

            # Add a recording
            response.record(
                action=f"https://5apclmbos2.execute-api.us-east-2.amazonaws.com/webhooks/recording?test_id={test_id}",
                method="POST",
                maxLength=30,
                timeout=5,
                transcribe=True,
                transcribeCallback=f"https://5apclmbos2.execute-api.us-east-2.amazonaws.com/webhooks/transcription?test_id={test_id}",
            )
    else:
        response.say("No test found with the provided ID.")

    # End the call
    response.pause(length=2)
    response.say("Thank you for your response. This concludes our test.")

    logger.error(f"DEBUG: Generated TwiML: {str(response)}")

    return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/recording")
async def recording_webhook(request: Request):
    """Handle recording webhook."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    recording_url = form_data.get("RecordingUrl")
    test_id = request.query_params.get("test_id")

    logger.error(
        f"DEBUG: Recording webhook - CallSid: {call_sid}, test_id: {test_id}, url: {recording_url}"
    )

    # Store the recording URL
    if test_id and test_id in evaluator_service.active_tests and recording_url:
        # Add to conversation record
        evaluator_service.record_conversation_turn(
            test_id=test_id,
            call_sid=call_sid,
            speaker="agent",
            text="[Recording - awaiting transcription]",
            audio_url=recording_url,
        )

    return JSONResponse(content={"status": "success"})


@router.post("/transcription")
async def transcription_webhook(request: Request):
    """Handle transcription webhook."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    transcription_text = form_data.get("TranscriptionText")
    recording_sid = form_data.get("RecordingSid")
    test_id = request.query_params.get("test_id")

    logger.error(
        f"DEBUG: Transcription webhook - CallSid: {call_sid}, test_id: {test_id}"
    )
    logger.error(f"DEBUG: Transcription text: {transcription_text}")

    # Store the transcription
    if test_id and test_id in evaluator_service.active_tests and transcription_text:
        # Find and update the agent response
        conversation = evaluator_service.active_tests[test_id].get("conversation", [])
        for turn in conversation:
            if (
                turn.get("speaker") == "agent"
                and turn.get("text") == "[Recording - awaiting transcription]"
            ):
                turn["text"] = transcription_text
                logger.error(
                    f"DEBUG: Updated agent response with transcription: {transcription_text}"
                )
                break

    return JSONResponse(content={"status": "success"})


@router.post("/call-status")
async def call_status(request: Request):
    """Handle call status updates."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    test_id = form_data.get("test_id") or request.query_params.get("test_id")

    logger.error(
        f"DEBUG: Call status update: {call_sid}, status: {call_status}, test: {test_id}"
    )

    # Find the test if not provided
    if not test_id:
        for tid, test_data in evaluator_service.active_tests.items():
            if test_data.get("call_sid") == call_sid:
                test_id = tid
                break

    # Process completed calls
    if (
        call_status in ["completed", "failed", "no-answer", "busy", "canceled"]
        and test_id
    ):
        # Process the completed call
        await process_completed_call(test_id, call_sid, call_status)

    return JSONResponse(content={"status": "success"})


async def process_completed_call(test_id: str, call_sid: str, call_status: str):
    """Process a completed call."""
    logger.error(
        f"DEBUG: Processing completed call: {call_sid} for test {test_id}, status: {call_status}"
    )

    try:
        if test_id in evaluator_service.active_tests:
            # Update test status
            if call_status == "completed":
                evaluator_service.active_tests[test_id]["status"] = "completed"
            else:
                evaluator_service.active_tests[test_id]["status"] = "failed"
                evaluator_service.active_tests[test_id][
                    "error"
                ] = f"Call ended with status: {call_status}"

            evaluator_service.active_tests[test_id][
                "end_time"
            ] = evaluator_service.time.time()

            # Get the conversation
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Generate report if we have conversation data
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
    except Exception as e:
        logger.error(f"DEBUG: Error processing completed call: {str(e)}")
