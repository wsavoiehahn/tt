# app/services/evaluator.py
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import tempfile
import os

from ..models.personas import Persona, Behavior
from ..models.test_cases import TestCase, TestQuestion
from ..models.reports import (
    ConversationTurn,
    EvaluationMetrics,
    QuestionEvaluation,
    TestCaseReport,
)
from ..config import config
from .openai_service import openai_service
from .twilio_service import twilio_service
from .s3_service import s3_service

logger = logging.getLogger(__name__)


class EvaluatorService:
    """Service for executing test cases and evaluating AI call center responses."""

    def __init__(self):
        self.active_tests = {}
        self.knowledge_base = config.load_knowledge_base()
        self.personas_data = config.load_personas()

    async def _wait_for_call_status(
        self, call_sid: str, target_statuses: List[str], timeout: int = 30
    ) -> bool:
        """
        Wait for a call to reach a specific status.

        Args:
            call_sid: Call SID
            target_statuses: List of target statuses
            timeout: Timeout in seconds

        Returns:
            True if the status was reached, False if timed out
        """
        from .twilio_service import twilio_service

        logger.info(f"Waiting for call {call_sid} to reach status: {target_statuses}")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Get call status
            call_status_result = twilio_service.get_call_status(call_sid)
            status = call_status_result.get("status")

            logger.info(f"Call {call_sid} current status: {status}")

            if status in target_statuses:
                logger.info(f"Call {call_sid} reached target status: {status}")
                return True

            # Wait before checking again
            await asyncio.sleep(2)

        # If we get here, we timed out
        logger.warning(
            f"Timed out waiting for call {call_sid} to reach status: {target_statuses}"
        )
        return False

    def get_persona(self, persona_name: str) -> Optional[Persona]:
        """Get a persona by name."""
        for persona in self.personas_data.get("personas", []):
            if persona.get("name") == persona_name:
                return Persona(
                    name=persona.get("name", ""), traits=persona.get("traits", [])
                )
        return None

    def get_behavior(self, behavior_name: str) -> Optional[Behavior]:
        """Get a behavior by name."""
        for behavior in self.personas_data.get("behaviors", []):
            if behavior.get("name") == behavior_name:
                return Behavior(
                    name=behavior.get("name", ""),
                    characteristics=behavior.get("characteristics", []),
                )
        return None

    def _mark_test_as_failed_and_update_dynamo(self, test_id, error, action="failed"):
        """
        Mark a test as failed with an error message.

        Args:
            error: Error message
            action: Optional action description
        """
        from ..services.dynamodb_service import dynamodb_service

        self.active_tests[test_id]["status"] = "failed"
        self.active_tests[test_id]["error"] = error
        self.active_tests[test_id]["execution_details"].append(
            {
                "timestamp": datetime.now().isoformat(),
                "action": action,
                "error": error,
            }
        )
        # Update in DynamoDB
        dynamodb_service.update_test_status(test_id, "failed")
        dynamodb_service.save_test(test_id, self.active_tests[test_id])

        logger.warning(
            f"Test status after error: {self.active_tests[test_id]['status']}"
        )

    async def execute_test_case(self, test_case: TestCase) -> TestCaseReport:
        """
        Execute a test case by initiating an outbound call to the target agent.

        Args:
            test_case: Test case to execute

        Returns:
            TestCaseReport containing evaluation results
        """
        logger.info(f"Executing test case: {test_case.name}, ID: {test_case.id}")

        start_time = time.time()
        test_id = str(test_case.id)

        # Add more detailed logging to track the execution flow
        logger.info(
            f"Test case details: {test_case.name}, persona: {test_case.config.persona_name}, behavior: {test_case.config.behavior_name}"
        )
        logger.info(
            f"Test case questions: {[q.text if isinstance(q, dict) else q for q in test_case.config.questions]}"
        )

        # Initialize test tracking in memory with more details
        self.active_tests[test_id] = {
            "test_case": test_case.dict(),
            "status": "starting",
            "start_time": start_time,
            "current_question_index": 0,
            "questions_evaluated": [],
            "execution_details": [],  # Add a list to track detailed execution steps
        }

        # Add execution detail
        self.active_tests[test_id]["execution_details"].append(
            {
                "timestamp": datetime.now().isoformat(),
                "action": "test_initialized",
                "status": "starting",
            }
        )

        # initialize blank test report:
        report = TestCaseReport(
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            persona_name=test_case.config.persona_name,
            behavior_name=test_case.config.behavior_name,
            questions_evaluated=[],
            overall_metrics=EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message=None,
            ),
            execution_time=0.0,
            special_instructions=test_case.config.special_instructions,
        )

        # Also save to DynamoDB for persistence across Lambda executions
        from ..services.dynamodb_service import dynamodb_service

        # Make sure we're explicitly saving everything needed by the call initiation process
        dynamodb_service.save_test(test_id, self.active_tests[test_id])

        # Save test case configuration to S3
        s3_service.save_test_case(test_case.dict(), test_id)

        ##TODO simplify this to make it more prosaic
        # Get persona and behavior
        persona = self.get_persona(test_case.config.persona_name)
        behavior = self.get_behavior(test_case.config.behavior_name)
        if not persona or not behavior:
            logger.error(
                f"Invalid persona or behavior: {test_case.config.persona_name}, {test_case.config.behavior_name}"
            )

            # Update status to failed with error details
            self._mark_test_as_failed_and_update_dynamo(
                self, test_id, error="Invalid persona or behavior", action="failed"
            )

            # update report with error and execution time
            report.overall_metrics.error_message = "Invalid persona or behavior"
            report.execution_time = time.time() - start_time
            return report
        try:
            # Import at function level to avoid circular imports
            from .twilio_service import twilio_service

            # CRITICAL - Explicitly set the status to waiting_for_call BEFORE initiating the call
            self.active_tests[test_id]["status"] = "waiting_for_call"
            self.active_tests[test_id]["execution_details"].append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "action": "status_updated",
                    "status": "waiting_for_call",
                }
            )

            logger.debug(
                f"Set test {test_id} status to waiting_for_call before call initiation"
            )

            # Update in DynamoDB immediately
            dynamodb_service.update_test_status(test_id, "waiting_for_call")
            logger.debug(f"Updated test status in DynamoDB to waiting_for_call")

            # Save the full test data again to DynamoDB
            dynamodb_service.save_test(test_id, self.active_tests[test_id])

            # Initiate the outbound call - with more logging around this critical step
            logger.debug(f"About to initiate Twilio call for test {test_id}")
            try:
                call_result = twilio_service.initiate_call(test_id)
                logger.info(f"Call initiation result: {call_result}")
            except Exception as twilio_error:
                logger.error(
                    f"DEBUG: Twilio call initiation error: {str(twilio_error)}"
                )
                raise  # Re-raise to be caught by outer exception handler

            if "error" in call_result:
                logger.error(f"Failed to initiate call: {call_result['error']}")
                # Mark test as failed
                self._mark_test_as_failed_and_update_dynamo(
                    self, test_id, error=call_result["error"], action="call_failed"
                )

                # update report with error and execution time
                report.overall_metrics.error_message = (
                    f"Failed to initiate call: {call_result['error']}"
                )
                report.execution_time = time.time() - start_time
                return report

            call_sid = call_result["call_sid"]
            logger.error(f"DEBUG: Call initiated with SID: {call_sid}")

            # Verify the test status is still waiting_for_call and explicitly set it if not
            if self.active_tests[test_id]["status"] != "waiting_for_call":
                logger.error(
                    f"DEBUG: Test status changed unexpectedly! Current status: {self.active_tests[test_id]['status']}"
                )
                # Force the status to be set correctly
                self.active_tests[test_id]["status"] = "waiting_for_call"
                self.active_tests[test_id]["execution_details"].append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "action": "status_forced",
                        "status": "waiting_for_call",
                    }
                )
                logger.error(f"DEBUG: Forced test status back to waiting_for_call")

                # Update in DynamoDB
                dynamodb_service.update_test_status(test_id, "waiting_for_call")

            # Add call_sid to test data
            self.active_tests[test_id]["call_sid"] = call_sid
            self.active_tests[test_id]["execution_details"].append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "action": "call_sid_assigned",
                    "call_sid": call_sid,
                }
            )

            # Update in DynamoDB with call_sid and latest status
            try:
                self.active_tests[test_id]["call_sid"] = call_sid
                dynamodb_service.save_test(test_id, self.active_tests[test_id])
                logger.info(f"Updated test in DynamoDB with call_sid")
            except Exception as ddb_error:
                logger.error(
                    f"DEBUG: Error updating DynamoDB with call_sid: {str(ddb_error)}"
                )

            # Log status again to confirm
            logger.error(
                f"DEBUG: Test status after call initiated: {self.active_tests[test_id]['status']}"
            )
            logger.error(
                f"DEBUG: Test {test_id} is waiting for call with call_sid: {call_sid}"
            )

            # Update test with call information
            if "calls" not in self.active_tests[test_id]:
                self.active_tests[test_id]["calls"] = {}

            self.active_tests[test_id]["calls"][call_sid] = {
                "status": "initiated",
                "start_time": time.time(),
            }

            # Update in DynamoDB again to ensure all data is saved
            try:
                dynamodb_service.save_test(test_id, self.active_tests[test_id])
                logger.error(f"DEBUG: Final test data update to DynamoDB complete")
            except Exception as ddb_error:
                logger.error(f"DEBUG: Error in final DynamoDB update: {str(ddb_error)}")

            logger.error(f"DEBUG: Call initiated: {call_sid} for test {test_id}")

            # Save initial report
            report_id = str(report.id)
            s3_service.save_report(report.dict(), report_id)

            # Store report_id in test data and update DynamoDB
            self.active_tests[test_id]["report_id"] = report_id
            dynamodb_service.save_test(test_id, self.active_tests[test_id])

            logger.info(f"Initial report created: {report_id}")
            return report

        except Exception as e:
            logger.error(f"Error in execute_test_case: {str(e)}")
            # Mark test as failed
            self._mark_test_as_failed_and_update_dynamo(
                self, test_id, error=str(e), action="exception"
            )

            # update report with error and execution time
            report.overall_metrics.error_message = (
                f"Error executing test case: {str(e)}"
            )
            report.execution_time = time.time() - start_time
            return report

    async def process_call(
        self, test_id: str, call_sid: str, conversation: List[Dict[str, Any]]
    ) -> None:
        """
        Process a call after it has ended.

        Args:
            test_id: Test case ID
            call_sid: Call SID
            conversation: List of conversation turns
        """
        logger.error(f"DEBUG: Processing call for test {test_id}, call {call_sid}")

        if test_id not in self.active_tests:
            logger.error(
                f"DEBUG: Test {test_id} not found in active tests, checking DynamoDB"
            )
            from .dynamodb_service import dynamodb_service

            test_data = dynamodb_service.get_test(test_id)

            if test_data:
                logger.error(
                    f"DEBUG: Test {test_id} found in DynamoDB, loading into memory"
                )
                self.active_tests[test_id] = test_data
            else:
                logger.error(
                    f"DEBUG: Test {test_id} not found in active tests or DynamoDB"
                )
                return

        # Update test status
        self.active_tests[test_id]["status"] = "processing"
        self.active_tests[test_id]["call_sid"] = call_sid
        self.active_tests[test_id]["end_time"] = time.time()

        # Update in DynamoDB
        from .dynamodb_service import dynamodb_service

        dynamodb_service.update_test_status(test_id, "processing")
        dynamodb_service.save_test(test_id, self.active_tests[test_id])

        # Generate report
        await self.generate_report_from_conversation(test_id, conversation)

    async def generate_report_from_conversation(
        self, test_id: str, conversation: List[Dict[str, Any]]
    ) -> TestCaseReport:
        """
        Generate a report from the actual conversation.

        Args:
            test_id: Test case ID
            conversation: List of conversation turns

        Returns:
            TestCaseReport containing evaluation results
        """
        logger.info(f"Generating report for test {test_id}")

        if test_id not in self.active_tests:
            logger.error(f"Test {test_id} not found in active tests")
            return None

        test_data = self.active_tests[test_id]
        test_case = TestCase(**test_data["test_case"])
        call_sid = test_data.get("call_sid", "unknown")

        end_time = test_data.get("end_time", time.time())
        if isinstance(end_time, datetime):
            end_time = end_time.timestamp()  # Convert to float

        # Convert start_time to a timestamp
        start_time = test_data.get("start_time", time.time())
        if isinstance(start_time, datetime):
            start_time = start_time.timestamp()  # Convert to float

        # Calculate execution time
        execution_time = end_time - start_time

        # Create conversation turns
        conversation_turns = []
        for i, turn in enumerate(conversation):
            # Convert timestamp if needed
            timestamp = turn.get("timestamp")
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp)
                except ValueError:
                    timestamp = datetime.now()
            elif timestamp is None:
                timestamp = datetime.now()

            # Get audio URL if available
            audio_url = turn.get("audio_url")

            # Create conversation turn
            conversation_turns.append(
                ConversationTurn(
                    speaker=turn.get("speaker", "unknown"),
                    text=turn.get("text", ""),
                    timestamp=timestamp,
                    audio_url=audio_url,
                )
            )

        # Process questions
        questions_evaluated = []

        # For simplicity, we'll treat all conversation as related to the first question
        # In a more sophisticated implementation, you would analyze the conversation to
        # determine which parts correspond to which questions
        for question in test_case.config.questions:
            metrics = await self._evaluate_conversation(
                question.text, conversation_turns
            )

            question_eval = QuestionEvaluation(
                question=question.text, conversation=conversation_turns, metrics=metrics
            )

            questions_evaluated.append(question_eval)

        # Calculate overall metrics
        overall_metrics = self._calculate_overall_metrics(questions_evaluated)

        # Create report
        report = TestCaseReport(
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            persona_name=test_case.config.persona_name,
            behavior_name=test_case.config.behavior_name,
            questions_evaluated=questions_evaluated,
            overall_metrics=overall_metrics,
            execution_time=execution_time,
            special_instructions=test_case.config.special_instructions,
        )

        # Save report
        report_id = str(report.id)
        s3_service.save_report(report.dict(), report_id)

        # Update test status
        self.active_tests[test_id]["status"] = "completed"

        logger.info(f"Report generated for test {test_id}, report {report_id}")

        return report

    async def _evaluate_conversation(
        self,
        question: str,
        conversation: List[ConversationTurn],
    ) -> EvaluationMetrics:
        """
        Evaluate a conversation using OpenAI.

        Args:
            question: Original question
            conversation: List of conversation turns

        Returns:
            Evaluation metrics
        """
        try:
            logger.info(f"Evaluating conversation for question: {question}")

            # Calculate response time from conversation
            response_time = 0
            for i, turn in enumerate(conversation):
                if turn.speaker == "agent" and i > 0:
                    # Find the previous evaluator turn
                    prev_turn = None
                    for j in range(i - 1, -1, -1):
                        if conversation[j].speaker == "evaluator":
                            prev_turn = conversation[j]
                            break

                    if prev_turn:
                        # Calculate time difference
                        time_diff = (
                            turn.timestamp - prev_turn.timestamp
                        ).total_seconds()
                        # Only update if this is the first response
                        if response_time == 0:
                            response_time = time_diff

            # Use OpenAI to evaluate the conversation
            metrics = await openai_service.evaluate_conversation(
                question=question,
                conversation=[turn.dict() for turn in conversation],
                knowledge_base=self.knowledge_base,
            )

            # Override with actual response time if available
            if response_time > 0:
                metrics.response_time = response_time

            return metrics

        except Exception as e:
            logger.error(f"Error evaluating conversation: {str(e)}")
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message=f"Error evaluating conversation: {str(e)}",
            )

    def _calculate_overall_metrics(
        self, questions_evaluated: List[QuestionEvaluation]
    ) -> EvaluationMetrics:
        """
        Calculate overall metrics across all evaluated questions.

        Args:
            questions_evaluated: List of question evaluations

        Returns:
            Overall metrics
        """
        successful_evaluations = [
            q.metrics for q in questions_evaluated if q.metrics.successful
        ]

        if not successful_evaluations:
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message="No successful evaluations",
            )

        accuracy = sum(m.accuracy for m in successful_evaluations) / len(
            successful_evaluations
        )
        empathy = sum(m.empathy for m in successful_evaluations) / len(
            successful_evaluations
        )
        response_time = sum(m.response_time for m in successful_evaluations) / len(
            successful_evaluations
        )

        return EvaluationMetrics(
            accuracy=accuracy,
            empathy=empathy,
            response_time=response_time,
            successful=True,
        )

    async def generate_empty_report(
        self, test_id: str, error_message: str
    ) -> Optional[TestCaseReport]:
        """
        Generate an empty report with error message when a test fails.

        Args:
            test_id: Test case ID
            error_message: Error message to include in the report

        Returns:
            Empty TestCaseReport with error information
        """
        logger.info(
            f"Generating empty report for test {test_id} with error: {error_message}"
        )

        if test_id not in self.active_tests:
            logger.error(f"Test {test_id} not found in active tests")
            return None

        test_data = self.active_tests[test_id]
        test_case = TestCase(**test_data["test_case"])
        call_sid = test_data.get("call_sid", "unknown")

        # Calculate execution time
        execution_time = test_data.get("end_time", time.time()) - test_data.get(
            "start_time", time.time()
        )

        # Create error metrics
        error_metrics = EvaluationMetrics(
            accuracy=0.0,
            empathy=0.0,
            response_time=0.0,
            successful=False,
            error_message=error_message,
        )

        # Create an empty question evaluation
        empty_question_eval = QuestionEvaluation(
            question="Test did not complete", conversation=[], metrics=error_metrics
        )

        # Create report
        report = TestCaseReport(
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            persona_name=test_case.config.persona_name,
            behavior_name=test_case.config.behavior_name,
            questions_evaluated=[empty_question_eval],
            overall_metrics=error_metrics,
            execution_time=execution_time,
            special_instructions=test_case.config.special_instructions,
        )

        # Save report
        report_id = str(report.id)
        s3_service.save_report(report.dict(), report_id)

        # Update test status
        self.active_tests[test_id]["status"] = "failed"
        self.active_tests[test_id]["error"] = error_message

        logger.info(f"Empty report generated for test {test_id}, report {report_id}")

        return report

    def record_conversation_turn(
        self,
        test_id: str,
        call_sid: str,
        speaker: str,
        text: str,
        audio_url: Optional[str] = None,
    ):
        """
        Record a turn in the conversation.

        Args:
            test_id: Test case ID
            call_sid: Call SID
            speaker: Speaker identifier (evaluator or agent)
            text: Text of the turn
            audio_url: URL of the audio recording (optional)
        """
        if test_id in self.active_tests:
            if "conversation" not in self.active_tests[test_id]:
                self.active_tests[test_id]["conversation"] = []

            # Create the turn data
            turn = {
                "speaker": speaker,
                "text": text,
                "timestamp": datetime.now().isoformat(),
            }

            if audio_url:
                turn["audio_url"] = audio_url

            # Add to conversation
            self.active_tests[test_id]["conversation"].append(turn)

            logger.info(
                f"Recorded conversation turn for test {test_id}, speaker: {speaker}"
            )

    def record_conversation_turn(
        self,
        test_id: str,
        call_sid: str,
        speaker: str,
        text: str,
        audio_url: Optional[str] = None,
    ):
        """
        Record a turn in the conversation.

        Args:
            test_id: Test case ID
            call_sid: Call SID
            speaker: Speaker identifier (evaluator or agent)
            text: Text of the turn
            audio_url: URL of the audio recording (optional)

        Returns:
            The created turn data
        """
        if test_id in self.active_tests:
            if "conversation" not in self.active_tests[test_id]:
                self.active_tests[test_id]["conversation"] = []

            # Create the turn data
            turn = {
                "speaker": speaker,
                "text": text,
                "timestamp": datetime.now().isoformat(),
            }

            if audio_url:
                turn["audio_url"] = audio_url

            # Add to conversation
            self.active_tests[test_id]["conversation"].append(turn)

            # Update test in DynamoDB to persist conversation state
            try:
                from ..services.dynamodb_service import dynamodb_service

                dynamodb_service.save_test(test_id, self.active_tests[test_id])
            except Exception as e:
                logger.error(f"Error saving conversation turn to DynamoDB: {str(e)}")

            logger.info(
                f"Recorded conversation turn for test {test_id}, speaker: {speaker}"
            )

            # Broadcast update to websocket clients
            try:
                from ..routers.twilio_webhooks import broadcast_update
                import asyncio

                asyncio.create_task(
                    broadcast_update(
                        {
                            "type": "conversation_update",
                            "test_id": test_id,
                            "call_sid": call_sid,
                            "new_turn": turn,
                        }
                    )
                )
            except Exception as e:
                logger.error(f"Error broadcasting conversation update: {str(e)}")

            return turn
        else:
            logger.warning(f"Test {test_id} not found for recording conversation turn")
            return None


# Create a singleton instance
evaluator_service = EvaluatorService()
