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

    async def execute_test_case(self, test_case: TestCase) -> TestCaseReport:
        """
        Execute a test case by initiating an outbound call to the target agent.

        Args:
            test_case: Test case to execute

        Returns:
            TestCaseReport containing evaluation results
        """
        logger.info(f"Executing test case: {test_case.name}")

        start_time = time.time()
        test_id = str(test_case.id)

        # Initialize test tracking
        self.active_tests[test_id] = {
            "test_case": test_case.dict(),
            "status": "starting",
            "start_time": start_time,
            "current_question_index": 0,
            "questions_evaluated": [],
        }

        # Save test case configuration
        s3_service.save_test_case(test_case.dict(), test_id)

        # Get persona and behavior
        persona = self.get_persona(test_case.config.persona_name)
        behavior = self.get_behavior(test_case.config.behavior_name)

        if not persona or not behavior:
            logger.error(
                f"Invalid persona or behavior: {test_case.config.persona_name}, {test_case.config.behavior_name}"
            )
            return TestCaseReport(
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
                    error_message="Invalid persona or behavior",
                ),
                execution_time=time.time() - start_time,
                special_instructions=test_case.config.special_instructions,
            )

        try:
            # Import at function level to avoid circular imports
            from .twilio_service import twilio_service

            # Initiate the outbound call
            call_result = twilio_service.initiate_call(test_id)

            if "error" in call_result:
                logger.error(f"Failed to initiate call: {call_result['error']}")
                # Mark test as failed
                self.active_tests[test_id]["status"] = "failed"
                self.active_tests[test_id]["error"] = call_result["error"]

                return TestCaseReport(
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
                        error_message=f"Failed to initiate call: {call_result['error']}",
                    ),
                    execution_time=time.time() - start_time,
                    special_instructions=test_case.config.special_instructions,
                )

            call_sid = call_result["call_sid"]
            self.active_tests[test_id]["call_sid"] = call_sid
            self.active_tests[test_id]["status"] = "in_progress"

            # Update test with call information
            if "calls" not in self.active_tests[test_id]:
                self.active_tests[test_id]["calls"] = {}
            self.active_tests[test_id]["calls"][call_sid] = {
                "status": "initiated",
                "start_time": time.time(),
            }

            logger.info(f"Call initiated: {call_sid} for test {test_id}")

            # Create a placeholder report - the real report will be generated after the call completes
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
                    successful=True,
                    error_message=None,
                ),
                execution_time=0.0,
                special_instructions=test_case.config.special_instructions,
            )

            # Save initial report
            report_id = str(report.id)
            s3_service.save_report(report.dict(), report_id)

            logger.info(f"Initial report created: {report_id}")

            return report

        except Exception as e:
            logger.error(f"Error in execute_test_case: {str(e)}")
            # Mark test as failed
            self.active_tests[test_id]["status"] = "failed"
            self.active_tests[test_id]["error"] = str(e)

            return TestCaseReport(
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
                    error_message=f"Error executing test case: {str(e)}",
                ),
                execution_time=time.time() - start_time,
                special_instructions=test_case.config.special_instructions,
            )

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
        logger.info(f"Processing call for test {test_id}, call {call_sid}")

        if test_id not in self.active_tests:
            logger.error(f"Test {test_id} not found in active tests")
            return

        # Update test status
        self.active_tests[test_id]["status"] = "processing"
        self.active_tests[test_id]["call_sid"] = call_sid
        self.active_tests[test_id]["end_time"] = time.time()

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

        # Calculate execution time
        execution_time = test_data.get("end_time", time.time()) - test_data.get(
            "start_time", time.time()
        )

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
                question.text, question.expected_topic, conversation_turns
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
        expected_topic: Optional[str],
        conversation: List[ConversationTurn],
    ) -> EvaluationMetrics:
        """
        Evaluate a conversation using OpenAI.

        Args:
            question: Original question
            expected_topic: Expected topic
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
                expected_topic=expected_topic,
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


# Create a singleton instance
evaluator_service = EvaluatorService()
