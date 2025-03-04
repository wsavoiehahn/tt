# app/services/mock_twilio_service.py
import logging
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MockTwilioService:
    """Mock service for Twilio operations in local mode."""

    def __init__(self):
        self.active_calls = {}

    def initiate_call(self, test_id: str) -> Dict[str, Any]:
        """Mock initiating a call."""
        call_sid = f"mock-call-{int(time.time())}"

        self.active_calls[call_sid] = {
            "test_id": test_id,
            "status": "in-progress",
            "start_time": time.time(),
            "recordings": [],
        }

        logger.info(f"Mock call initiated: {call_sid} for test {test_id}")

        return {"call_sid": call_sid, "status": "in-progress", "test_id": test_id}

    def get_call_status(self, call_sid: str) -> Dict[str, Any]:
        """Get status of a mock call."""
        if call_sid in self.active_calls:
            return {
                "call_sid": call_sid,
                "status": self.active_calls[call_sid]["status"],
                "duration": time.time() - self.active_calls[call_sid]["start_time"],
                "direction": "outbound-api",
                "answered_by": "machine_start",
            }

        return {"call_sid": call_sid, "error": "Call not found", "status": "unknown"}

    def end_call(self, call_sid: str) -> Dict[str, Any]:
        """End a mock call."""
        if call_sid in self.active_calls:
            self.active_calls[call_sid]["status"] = "completed"
            self.active_calls[call_sid]["end_time"] = time.time()

            logger.info(f"Mock call ended: {call_sid}")

            return {"call_sid": call_sid, "status": "completed"}

        return {"call_sid": call_sid, "error": "Call not found", "status": "error"}

    def generate_twiml_for_question(
        self, question: str, test_id: str, call_sid: str
    ) -> str:
        """Generate TwiML for asking a question (mock version)."""
        logger.info(f"Mock TwiML generated for question: {question}")

        # For local testing, we'll include the question in the TwiML for debugging
        twiml = f"""
        <Response>
            <Say>MOCK TWIML: {question}</Say>
            <Pause length="1"/>
            <Record maxLength="30" playBeep="false" timeout="5"/>
        </Response>
        """

        return twiml
