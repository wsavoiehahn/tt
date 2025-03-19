# app/services/twilio_service.py
import os
import logging
import requests
from typing import Dict, Any, Optional, List
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream


import time

logger = logging.getLogger(__name__)


class TwilioService:
    """Service for interacting with Twilio's API for call handling."""

    def __init__(self):
        self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        self.client = Client(self.account_sid, self.auth_token)
        self.ai_service_number = os.environ.get("TARGET_PHONE_NUMBER")
        self.callback_url = os.environ.get("TWILIO_CALLBACK_URL")
        # Track active calls
        self.active_calls = {}

        # Log initialization
        logger.info(
            f"TwilioService initialized with account_sid: {self.account_sid[:5]}***, target number: {self.ai_service_number}, callback URL: {self.callback_url}"
        )

    def download_recording(
        self, recording_url: str, recording_sid: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Download a recording from Twilio with improved error handling.

        Args:
            recording_url: URL of the recording to download
            recording_sid: Optional recording SID if URL isn't complete

        Returns:
            Audio data as bytes, or None if download failed
        """
        try:
            # If we only have the SID, construct the URL
            if not recording_url and recording_sid:
                recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Recordings/{recording_sid}"

            logger.info(f"Downloading recording from: {recording_url}")

            # Check if URL is valid
            if not recording_url:
                logger.error("No recording URL or SID provided")
                return None

            # Ensure the URL is properly formatted
            if not recording_url.startswith("http"):
                # Try to construct a proper URL
                if recording_url.startswith("RE"):
                    # This looks like a Recording SID
                    recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Recordings/{recording_url}"
                else:
                    logger.error(f"Invalid recording URL format: {recording_url}")
                    return None

            # Add .mp3 extension if not present and not already a complete URL
            if not recording_url.endswith(".mp3") and "/Recordings/" in recording_url:
                recording_url = f"{recording_url}.mp3"

            # Set up authentication
            auth = (self.account_sid, self.auth_token)

            # Download the recording with a retry mechanism
            max_retries = 3
            retry_delay = 2  # seconds

            for attempt in range(max_retries):
                response = requests.get(recording_url, auth=auth)

                if response.status_code == 200:
                    # Success
                    audio_data = response.content
                    logger.info(
                        f"Successfully downloaded recording ({len(audio_data)} bytes)"
                    )
                    return audio_data
                elif response.status_code == 404 and attempt < max_retries - 1:
                    # Recordings might not be immediately available - wait and retry
                    logger.warning(
                        f"Recording not found (attempt {attempt+1}/{max_retries}), retrying in {retry_delay}s"
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    # Other error or final retry
                    logger.error(
                        f"Failed to download recording: Status {response.status_code}"
                    )
                    logger.error(f"Response content: {response.text[:200]}")
                    return None

            logger.error(f"Failed to download recording after {max_retries} attempts")
            return None

        except Exception as e:
            logger.error(f"Error downloading recording: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())
            return None

    def initiate_call(self, test_id: str) -> Dict[str, Any]:
        """
        Initiate a call to the AI service agent with the test questions.
        """
        try:
            logger.info(f"Initiating call for test_id: {test_id}")

            # Get outbound number
            from_number = self.get_outbound_number()
            logger.info(f"Using outbound number: {from_number}")
            logger.debug(f"Using callback URL: {self.callback_url}")

            # Import at function level to avoid circular imports
            from ..services.evaluator import evaluator_service

            # Check if the test exists in active_tests before attempting to update

            from ..services.dynamodb_service import dynamodb_service

            # Create a simple TwiML for call initiation with better logging
            response = VoiceResponse()
            response.say("Speak Now.")

            # Log TwiML content
            logger.debug(f"Generated TwiML: {str(response)}")

            # Set up call parameters - ensure test_id is passed in multiple places
            status_callback_url = (
                f"{self.callback_url}/webhooks/call-status?test_id={test_id}"
            )
            websocket_url = os.environ.get("WEBSOCKET_ENDPOINT")
            logger.info(f"Status callback URL: {status_callback_url}")

            # Initiate the call with the simplified TwiML
            try:
                logger.info(
                    f"Initiating call to {self.ai_service_number} from {from_number}"
                )
                # CALL STARTS HERE
                connect = Connect()
                stream = Stream(url=f"{websocket_url}/media-stream")
                stream.parameter(name="test_id", value=test_id)
                connect.append(stream)
                response.append(connect)
                logger.info(f"Generated TwiML: {str(response)}")
                # Create the call with all parameters
                call = self.client.calls.create(
                    to=self.ai_service_number,
                    from_=from_number,
                    twiml=str(response),
                    status_callback=status_callback_url,
                    status_callback_event=[
                        # "queued",
                        "initiated",
                        "ringing",
                        "answered",
                        "completed",
                    ],
                    status_callback_method="POST",
                    method="POST",
                    machine_detection="Enable",
                    machine_detection_timeout=30,
                    machine_detection_speech_threshold=2500,
                    machine_detection_speech_end_threshold=1000,
                    machine_detection_silence_timeout=5000,
                    # Add optional parameters
                    record=True,
                )

                logger.info(f"Call created successfully with SID: {call.sid}")
                logger.info(f"Call direction: {call.direction}")
            except Exception as call_error:
                logger.error(f"Twilio API error creating call: {str(call_error)}")
                return {
                    "error": f"Twilio error: {str(call_error)}",
                    "test_id": test_id,
                    "status": "failed",
                }

            # Store call information
            self.active_calls[call.sid] = {
                "test_id": test_id,
                "status": "initiated",
                "start_time": time.time(),
                "to": self.ai_service_number,
                "from": from_number,
            }

            logger.info(
                f"Call initiated: {call.sid} for test {test_id} to {self.ai_service_number}"
            )

            # Update test data with call information
            try:
                evaluator_service.active_tests[test_id]["call_sid"] = call.sid
                evaluator_service.active_tests[test_id]["call_status"] = call.status

                # Update in DynamoDB
                from ..services.dynamodb_service import dynamodb_service

                dynamodb_service.save_test(
                    test_id, evaluator_service.active_tests[test_id]
                )
            except Exception as update_error:
                logger.error(f"Error updating test with call SID: {str(update_error)}")

            return {
                "call_sid": call.sid,
                "status": call.status,
                "test_id": test_id,
                "to": self.ai_service_number,
            }

        except Exception as e:
            logger.error(f"ERROR in initiate_call: {str(e)}")
            # Log detailed exception information including traceback
            import traceback

            logger.error(f"ERROR TRACEBACK: {traceback.format_exc()}")
            return {"error": str(e), "test_id": test_id, "status": "failed"}

    def get_outbound_number(self) -> str:
        """Get an available Twilio number for outbound calling."""
        # Get first available phone number from account
        incoming_phone_numbers = self.client.incoming_phone_numbers.list(limit=1)
        if incoming_phone_numbers:
            return incoming_phone_numbers[0].phone_number
        else:
            raise ValueError("No phone numbers available")

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
