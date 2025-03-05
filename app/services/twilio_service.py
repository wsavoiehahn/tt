# app/services/twilio_service.py
import os
import logging
from typing import Dict, Any, Optional, List
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
import uuid
import time
import asyncio
from ..config import config

logger = logging.getLogger(__name__)


class TwilioService:
    """Service for interacting with Twilio's API for call handling."""

    def __init__(self):
        self.account_sid = config.get_parameter("/twilio/account_sid")
        self.auth_token = config.get_parameter("/twilio/auth_token")
        self.client = Client(self.account_sid, self.auth_token)
        self.ai_service_number = config.get_parameter(
            "/twilio/target_phone_number", False
        )
        if not self.ai_service_number:
            self.ai_service_number = config.get_parameter(
                "/ai-evaluator/ai_service_phone_number", False
            )
        self.callback_url = config.get_parameter("/ai-evaluator/twilio_callback_url")

        # Track active calls
        self.active_calls = {}

    # Update the initiate_call method with detailed ERROR logging
    def initiate_call(self, test_id: str) -> Dict[str, Any]:
        """
        Initiate a call to the AI service agent with the test questions.

        Args:
            test_id: Unique identifier for the test case

        Returns:
            Dictionary with call SID and status
        """
        try:
            # Get the AI service phone number from parameter store
            ai_service_number = self.ai_service_number

            # If not set, try to get it directly from parameter store
            if not ai_service_number:
                ai_service_number = config.get_parameter(
                    "/twilio/target_phone_number", False
                )
                if ai_service_number:
                    self.ai_service_number = ai_service_number
                    logger.error(
                        f"DEBUG - Updated target phone number to: {ai_service_number}"
                    )

            if not ai_service_number:
                error_msg = "AI service phone number not configured at /twilio/target_phone_number"
                logger.error(error_msg)
                return {"error": error_msg, "test_id": test_id, "status": "failed"}

            # Get outbound number
            from_number = self.get_outbound_number()
            logger.error(f"DEBUG - Using outbound number: {from_number}")

            # Get callback URL from config
            callback_url = self.callback_url
            if not callback_url:
                callback_url = "https://example.com"  # Default for testing
                logger.error("DEBUG - No callback URL found, using default")
            else:
                logger.error(f"DEBUG - Using callback URL: {callback_url}")

            # Import at function level to avoid circular imports
            from ..services.evaluator import evaluator_service

            # Get test questions from evaluator service
            test_questions = []
            if test_id in evaluator_service.active_tests:
                test_data = evaluator_service.active_tests[test_id]
                test_case = test_data.get("test_case", {})
                config_data = test_case.get("config", {})
                questions = config_data.get("questions", [])

                # Extract questions
                for q in questions:
                    if isinstance(q, dict) and "text" in q:
                        test_questions.append(q["text"])
                    elif isinstance(q, str):
                        test_questions.append(q)

            # Fallback if no questions found
            if not test_questions:
                logger.error(
                    f"DEBUG - No questions found for test {test_id}, using default"
                )
                test_questions = ["How can I help you today?"]
            else:
                logger.error(
                    f"DEBUG - Found {len(test_questions)} questions for test {test_id}"
                )

            # Apply special instructions if they exist
            special_instructions = None
            if test_id in evaluator_service.active_tests:
                test_data = evaluator_service.active_tests[test_id]
                test_case = test_data.get("test_case", {})
                config_data = test_case.get("config", {})
                special_instructions = config_data.get("special_instructions")
                if special_instructions:
                    logger.error(
                        f"DEBUG - Using special instructions: {special_instructions}"
                    )

            # Create TwiML for the call with questions
            twiml = "<Response>"
            twiml += "<Say>This is an automated call for evaluation purposes.</Say>"
            twiml += "<Pause length='2'/>"

            # Add each question with pauses between them
            for i, question in enumerate(test_questions):
                # Apply special instructions if needed
                if special_instructions and i == 0:  # Only for first question
                    modified_question = f"{question} {special_instructions}"
                else:
                    modified_question = question

                twiml += f"<Say>{modified_question}</Say>"
                twiml += "<Pause length='1'/>"

                # Record the response
                record_action = f"{callback_url}/webhooks/response-recorded?test_id={test_id}&question_index={i}"
                logger.error(
                    f"DEBUG - Record action URL for question {i}: {record_action}"
                )

                twiml += f"""
                <Record 
                    action="{record_action}" 
                    maxLength="120" 
                    playBeep="false"
                    timeout="5"
                />
                """
                twiml += "<Pause length='1'/>"

            # Close the response
            twiml += "<Say>Thank you for your responses. Goodbye.</Say>"
            twiml += "</Response>"

            logger.error(
                f"DEBUG - Initiating call from {from_number} to {ai_service_number} for test {test_id}"
            )

            # Log a simplified version of the TwiML to avoid excessive log length
            logger.error(
                f"DEBUG - TwiML Length: {len(twiml)} chars, First 100 chars: {twiml[:100]}..."
            )

            # Set up callback URLs
            recording_status_callback = f"{callback_url}/webhooks/recording-status"
            status_callback = f"{callback_url}/webhooks/call-status?test_id={test_id}"

            logger.error(
                f"DEBUG - Recording status callback: {recording_status_callback}"
            )
            logger.error(f"DEBUG - Call status callback: {status_callback}")

            # Initiate the call
            try:
                call = self.client.calls.create(
                    twiml=twiml,
                    to=ai_service_number,
                    from_=from_number,
                    record=True,
                    recording_status_callback=recording_status_callback,
                    recording_status_callback_event=["completed"],
                    status_callback=status_callback,
                    status_callback_event=["answered", "completed"],
                    status_callback_method="POST",
                )
                logger.error(f"DEBUG - Call created successfully with SID: {call.sid}")
            except Exception as call_error:
                logger.error(
                    f"DEBUG - Twilio API error creating call: {str(call_error)}"
                )
                raise  # Re-raise to be caught by outer exception handler

            # Store call information
            self.active_calls[call.sid] = {
                "test_id": test_id,
                "status": "initiated",
                "start_time": time.time(),
                "to": ai_service_number,
                "from": from_number,
                "recordings": [],
                "questions": test_questions,
            }

            logger.error(
                f"DEBUG - Call initiated: {call.sid} for test {test_id} to {ai_service_number}"
            )

            return {
                "call_sid": call.sid,
                "status": call.status,
                "test_id": test_id,
                "to": ai_service_number,
            }

        except Exception as e:
            logger.error(f"ERROR in initiate_call: {str(e)}")
            # Log detailed exception information including traceback
            import traceback

            logger.error(f"ERROR TRACEBACK: {traceback.format_exc()}")
            return {"error": str(e), "test_id": test_id, "status": "failed"}

    def get_outbound_number(self) -> str:
        """Get an available Twilio number for outbound calling."""
        try:
            # Get first available phone number from account
            incoming_phone_numbers = self.client.incoming_phone_numbers.list(limit=1)
            if incoming_phone_numbers:
                return incoming_phone_numbers[0].phone_number
            else:
                # Fallback to default number
                return config.get_parameter("/twilio/phone_number")
        except Exception as e:
            logger.error(f"Error getting outbound number: {str(e)}")
            return config.get_parameter("/twilio/phone_number")

    def get_call_status(self, call_sid: str) -> Dict[str, Any]:
        """Get status of a call by SID."""
        try:
            call = self.client.calls(call_sid).fetch()

            # Update local tracking
            if call_sid in self.active_calls:
                self.active_calls[call_sid]["status"] = call.status

            return {
                "call_sid": call_sid,
                "status": call.status,
                "duration": call.duration,
                "direction": call.direction,
                "answered_by": call.answered_by,
            }
        except Exception as e:
            logger.error(f"Error getting call status: {str(e)}")
            return {"call_sid": call_sid, "error": str(e), "status": "unknown"}

    def end_call(self, call_sid: str) -> Dict[str, Any]:
        """End an active call."""
        try:
            call = self.client.calls(call_sid).update(status="completed")

            # Update local tracking
            if call_sid in self.active_calls:
                self.active_calls[call_sid]["status"] = "completed"
                self.active_calls[call_sid]["end_time"] = time.time()

            return {"call_sid": call_sid, "status": call.status}
        except Exception as e:
            logger.error(f"Error ending call: {str(e)}")
            return {"call_sid": call_sid, "error": str(e), "status": "error"}

    def get_recordings(self, call_sid: str) -> List[Dict[str, Any]]:
        """Get all recordings for a call."""
        try:
            recordings = self.client.recordings.list(call_sid=call_sid)

            result = []
            for recording in recordings:
                recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Recordings/{recording.sid}.mp3"
                result.append(
                    {
                        "recording_sid": recording.sid,
                        "duration": recording.duration,
                        "url": recording_url,
                        "date_created": recording.date_created,
                        "status": recording.status,
                    }
                )

                # Update local tracking
                if call_sid in self.active_calls:
                    self.active_calls[call_sid]["recordings"].append(recording.sid)

            return result
        except Exception as e:
            logger.error(f"Error getting recordings: {str(e)}")
            return []

    def generate_twiml_for_question(
        self, question: str, test_id: str, call_sid: str
    ) -> str:
        """
        Generate TwiML for asking a question.

        Args:
            question: The question to ask
            test_id: Test case ID
            call_sid: Call SID

        Returns:
            TwiML response as string
        """
        response = VoiceResponse()

        # Pause for a moment to simulate natural conversation
        response.pause(length=1)

        # Say the question
        response.say(question, voice="alice")

        # Record the response
        response.record(
            action=f"{self.callback_url}/webhooks/response-recorded?test_id={test_id}&call_sid={call_sid}",
            timeout=10,
            maxLength=60,
            playBeep=False,
            trim="trim-silence",
        )

        # Add a failsafe gather in case recording doesn't trigger
        gather = Gather(
            action=f"{self.callback_url}/webhooks/response-gathered?test_id={test_id}&call_sid={call_sid}",
            timeout=15,
            input="speech",
        )
        gather.say("I'm waiting for your response.", voice="alice")
        response.append(gather)

        return str(response)

    def handle_recording_completed(
        self, recording_sid: str, recording_url: str, call_sid: str
    ) -> Dict[str, Any]:
        """Handle a completed recording callback."""
        try:
            # Download recording
            recording = self.client.recordings(recording_sid).fetch()
            recording_uri = recording.uri.replace(".json", ".mp3")
            recording_url = f"https://api.twilio.com{recording_uri}"

            # Update local tracking
            if call_sid in self.active_calls:
                if "recordings" not in self.active_calls[call_sid]:
                    self.active_calls[call_sid]["recordings"] = []

                self.active_calls[call_sid]["recordings"].append(
                    {
                        "sid": recording_sid,
                        "url": recording_url,
                        "duration": recording.duration,
                    }
                )

            return {
                "recording_sid": recording_sid,
                "call_sid": call_sid,
                "url": recording_url,
                "duration": recording.duration,
                "status": "completed",
            }
        except Exception as e:
            logger.error(f"Error handling recording completion: {str(e)}")
            return {
                "recording_sid": recording_sid,
                "call_sid": call_sid,
                "error": str(e),
                "status": "error",
            }


# Create a singleton instance
twilio_service = TwilioService()
