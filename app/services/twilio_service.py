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
        self.account_sid = config.get_parameter("/ai-evaluator/twilio_account_sid")
        self.auth_token = config.get_parameter("/ai-evaluator/twilio_auth_token")
        self.client = Client(self.account_sid, self.auth_token)
        self.ai_service_number = config.get_parameter(
            "/ai-evaluator/ai_service_phone_number"
        )
        self.callback_url = config.get_parameter("/ai-evaluator/twilio_callback_url")

        # Track active calls
        self.active_calls = {}

    def initiate_call(self, test_id: str) -> Dict[str, Any]:
        try:
            call = self.client.calls.create(
                url=f"{self.callback_url}/webhooks/call-started?test_id={test_id}",
                to=self.ai_service_number,
                from_=self.get_outbound_number(),
                record=True,
                recording_status_callback=f"{self.callback_url}/webhooks/recording-status",
                recording_status_callback_event=["completed"],
            )

            # Log the call details
            logger.info(f"Call initiated - SID: {call.sid}, Test ID: {test_id}")

            self.active_calls[call.sid] = {
                "test_id": test_id,
                "status": "initiated",
                "start_time": time.time(),
                "recordings": [],
            }

            return {"call_sid": call.sid, "status": call.status, "test_id": test_id}

        except Exception as e:
            logger.error(f"Error initiating call: {str(e)}")
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
                return config.get_parameter("/ai-evaluator/default_outbound_number")
        except Exception as e:
            logger.error(f"Error getting outbound number: {str(e)}")
            return config.get_parameter("/ai-evaluator/default_outbound_number")

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
