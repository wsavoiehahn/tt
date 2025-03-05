# app/services/twilio_service.py
import os
import logging
import json
from typing import Dict, Any, Optional, List
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather, Start, Connect, Stream
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

        # Log initialization
        logger.error(
            f"DEBUG: TwilioService initialized with account_sid: {self.account_sid[:5]}***, target number: {self.ai_service_number}, callback URL: {self.callback_url}"
        )

    def initiate_call(self, test_id: str) -> Dict[str, Any]:
        """
        Initiate a call to the AI service agent with the test questions.
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
                        f"DEBUG: Updated target phone number to: {ai_service_number}"
                    )

            if not ai_service_number:
                error_msg = "AI service phone number not configured"
                logger.error(f"DEBUG: {error_msg}")
                return {"error": error_msg, "test_id": test_id, "status": "failed"}

            # Get outbound number
            from_number = self.get_outbound_number()
            logger.error(f"DEBUG: Using outbound number: {from_number}")

            # Get callback URL from config
            callback_url = self.callback_url
            if not callback_url:
                callback_url = "https://example.com"  # Default for testing
                logger.error("DEBUG: No callback URL found, using default")
            else:
                logger.error(f"DEBUG: Using callback URL: {callback_url}")

            # Import at function level to avoid circular imports
            from ..services.evaluator import evaluator_service

            # Dump active tests before updating
            logger.error(
                f"DEBUG: Active tests before update: {evaluator_service.active_tests}"
            )

            # Check if the test exists in active_tests
            if test_id in evaluator_service.active_tests:
                previous_status = evaluator_service.active_tests[test_id].get(
                    "status", "unknown"
                )
                evaluator_service.active_tests[test_id]["status"] = "waiting_for_call"
                logger.error(
                    f"DEBUG: Updated test {test_id} status from {previous_status} to waiting_for_call"
                )
                logger.error(
                    f"DEBUG: Test data after update: {evaluator_service.active_tests[test_id]}"
                )
            else:
                logger.error(f"DEBUG: Test {test_id} not found in active_tests")
                # Try to get details of all active tests to see what's going on
                logger.error(
                    f"DEBUG: Current active tests: {evaluator_service.active_tests}"
                )

            # Create a simpler TwiML for call initiation
            response = VoiceResponse()
            response.say("Connecting to evaluation system...")

            # Set up call parameters - this is the critical part for passing the test_id
            # We'll include test_id in both the statusCallback and the URL
            status_callback_url = (
                f"{callback_url}/webhooks/call-status?test_id={test_id}"
            )
            call_started_url = f"{callback_url}/webhooks/call-started?test_id={test_id}"

            logger.error(f"DEBUG: Status callback URL: {status_callback_url}")
            logger.error(f"DEBUG: Call started URL: {call_started_url}")

            # Log the TwiML
            logger.error(f"DEBUG: Initial TwiML: {str(response)}")

            # Initiate the call with explicit test_id parameter
            try:
                call = self.client.calls.create(
                    to=ai_service_number,
                    from_=from_number,
                    twiml=str(response),
                    status_callback=status_callback_url,
                    status_callback_event=[
                        "queued",
                        "initiated",
                        "ringing",
                        "answered",
                        "completed",
                    ],
                    status_callback_method="POST",
                    url=call_started_url,
                    method="POST",
                )
                logger.error(
                    f"DEBUG: Call created successfully with SID: {call.sid}, test_id: {test_id}"
                )
            except Exception as call_error:
                logger.error(
                    f"DEBUG: Twilio API error creating call: {str(call_error)}"
                )
                raise  # Re-raise to be caught by outer exception handler

            # Store call information
            self.active_calls[call.sid] = {
                "test_id": test_id,
                "status": "initiated",
                "start_time": time.time(),
                "to": ai_service_number,
                "from": from_number,
            }

            logger.error(
                f"DEBUG: Call initiated: {call.sid} for test {test_id} to {ai_service_number}"
            )

            # Check active tests once more to confirm status update persisted
            if test_id in evaluator_service.active_tests:
                logger.error(
                    f"DEBUG: Test {test_id} status after call initiation: {evaluator_service.active_tests[test_id].get('status')}"
                )

            return {
                "call_sid": call.sid,
                "status": call.status,
                "test_id": test_id,
                "to": ai_service_number,
            }

        except Exception as e:
            logger.error(f"DEBUG: ERROR in initiate_call: {str(e)}")
            # Log detailed exception information including traceback
            import traceback

            logger.error(f"DEBUG: ERROR TRACEBACK: {traceback.format_exc()}")
            return {"error": str(e), "test_id": test_id, "status": "failed"}

        """
        Initiate a call to the AI service agent with the test questions.
        """
        try:
            logger.error(f"DEBUG: Initiating call for test_id: {test_id}")

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
                        f"DEBUG: Updated target phone number to: {ai_service_number}"
                    )

            if not ai_service_number:
                error_msg = "AI service phone number not configured"
                logger.error(f"DEBUG: {error_msg}")
                return {"error": error_msg, "test_id": test_id, "status": "failed"}

            # Get outbound number
            from_number = self.get_outbound_number()
            logger.error(f"DEBUG: Using outbound number: {from_number}")

            # Get callback URL from config
            callback_url = self.callback_url
            if not callback_url:
                callback_url = "https://example.com"  # Default for testing
                logger.error("DEBUG: No callback URL found, using default")
            else:
                logger.error(f"DEBUG: Using callback URL: {callback_url}")

            # Import at function level to avoid circular imports
            from ..services.evaluator import evaluator_service

            # Dump active tests before updating
            logger.error(
                f"DEBUG: Active tests before update: {evaluator_service.active_tests}"
            )

            # Check if the test exists in active_tests
            if test_id in evaluator_service.active_tests:
                previous_status = evaluator_service.active_tests[test_id].get(
                    "status", "unknown"
                )
                evaluator_service.active_tests[test_id]["status"] = "waiting_for_call"
                logger.error(
                    f"DEBUG: Updated test {test_id} status from {previous_status} to waiting_for_call"
                )
                logger.error(
                    f"DEBUG: Test data after update: {evaluator_service.active_tests[test_id]}"
                )
            else:
                logger.error(f"DEBUG: Test {test_id} not found in active_tests")
                # Try to get details of all active tests to see what's going on
                logger.error(
                    f"DEBUG: Current active tests: {evaluator_service.active_tests}"
                )

            # Create a simpler TwiML for call initiation
            response = VoiceResponse()
            response.say("Connecting to evaluation system...")

            # Set up the call status callback
            status_callback_url = (
                f"{callback_url}/webhooks/call-status?test_id={test_id}"
            )
            logger.error(f"DEBUG: Status callback URL: {status_callback_url}")

            # URL for the call-started webhook
            call_started_url = f"{callback_url}/webhooks/call-started?test_id={test_id}"
            logger.error(f"DEBUG: Call started URL: {call_started_url}")

            # Log the TwiML
            logger.error(f"DEBUG: Initial TwiML: {str(response)}")

            # Initiate the call with the simplified TwiML
            try:
                call = self.client.calls.create(
                    to=ai_service_number,
                    from_=from_number,
                    twiml=str(response),
                    status_callback=status_callback_url,
                    status_callback_event=[
                        "queued",
                        "initiated",
                        "ringing",
                        "answered",
                        "completed",
                    ],
                    status_callback_method="POST",
                    url=call_started_url,
                    method="POST",
                )
                logger.error(f"DEBUG: Call created successfully with SID: {call.sid}")
            except Exception as call_error:
                logger.error(
                    f"DEBUG: Twilio API error creating call: {str(call_error)}"
                )
                raise  # Re-raise to be caught by outer exception handler

            # Store call information
            self.active_calls[call.sid] = {
                "test_id": test_id,
                "status": "initiated",
                "start_time": time.time(),
                "to": ai_service_number,
                "from": from_number,
            }

            logger.error(
                f"DEBUG: Call initiated: {call.sid} for test {test_id} to {ai_service_number}"
            )

            # Check active tests once more to confirm status update persisted
            if test_id in evaluator_service.active_tests:
                logger.error(
                    f"DEBUG: Test {test_id} status after call initiation: {evaluator_service.active_tests[test_id].get('status')}"
                )

            return {
                "call_sid": call.sid,
                "status": call.status,
                "test_id": test_id,
                "to": ai_service_number,
            }

        except Exception as e:
            logger.error(f"DEBUG: ERROR in initiate_call: {str(e)}")
            # Log detailed exception information including traceback
            import traceback

            logger.error(f"DEBUG: ERROR TRACEBACK: {traceback.format_exc()}")
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
                default_number = config.get_parameter("/twilio/phone_number")
                logger.error(f"DEBUG: Using default outbound number: {default_number}")
                return default_number
        except Exception as e:
            logger.error(f"DEBUG: Error getting outbound number: {str(e)}")
            default_number = config.get_parameter("/twilio/phone_number")
            logger.error(
                f"DEBUG: Falling back to default outbound number: {default_number}"
            )
            return default_number

    def generate_stream_twiml(self, test_id: str, call_sid: str) -> str:
        """
        Generate TwiML for connecting to a media stream.
        """
        # Get callback URL from config
        callback_url = self.callback_url
        if not callback_url:
            callback_url = "https://example.com"  # Default for testing
            logger.error(
                "DEBUG: No callback URL found in generate_stream_twiml, using default"
            )

        # Create VoiceResponse object
        response = VoiceResponse()

        # Add initial message
        response.say("Starting evaluation call.")

        # Create media stream URL
        stream_url = f"{callback_url}/webhooks/media-stream?test_id={test_id}&call_sid={call_sid}"
        logger.error(f"DEBUG: Media stream URL: {stream_url}")

        # Create Connect and Stream objects
        connect = Connect()
        stream = Stream(url=stream_url)

        # Add Stream to Connect and Connect to response
        connect.append(stream)
        response.append(connect)

        logger.error(f"DEBUG: Generated stream TwiML: {str(response)}")

        return str(response)

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
                    if "recordings" not in self.active_calls[call_sid]:
                        self.active_calls[call_sid]["recordings"] = []
                    self.active_calls[call_sid]["recordings"].append(recording.sid)

            return result
        except Exception as e:
            logger.error(f"Error getting recordings: {str(e)}")
            return []

    def generate_stream_twiml(self, test_id: str, call_sid: str) -> str:
        """
        Generate TwiML for connecting to a media stream.

        Args:
            test_id: Test case ID
            call_sid: Call SID

        Returns:
            TwiML response as string
        """
        # Get callback URL from config
        callback_url = self.callback_url
        if not callback_url:
            callback_url = "https://example.com"  # Default for testing
            logger.warning("No callback URL found, using default")

        # Create VoiceResponse object
        response = VoiceResponse()

        # Add initial message
        response.say("Starting evaluation call.", voice="alice")

        # Create Connect and Stream objects
        connect = Connect()
        stream = Stream(
            url=f"{callback_url}/webhooks/media-stream?test_id={test_id}&call_sid={call_sid}"
        )

        # Add Stream to Connect and Connect to response
        connect.append(stream)
        response.append(connect)

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
