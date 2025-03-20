# app/services/evaluator.py
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests
import os

from app.models.personas import Persona, Behavior
from app.models.test_cases import TestCase
from app.models.reports import (
    ConversationTurn,
    EvaluationMetrics,
    TestCaseReport,
)
from app.config import config
from app.services.s3_service import s3_service

logger = logging.getLogger(__name__)


class EvaluatorService:
    """Service for executing test cases and evaluating AI call center responses."""

    def __init__(self):
        self.active_tests = {}
        self.knowledge_base = config.load_knowledge_base()
        self.personas_data = config.load_personas()
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.realtime_url = "wss://api.openai.com/v1/realtime"
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.realtime_model = "gpt-4o-realtime-preview-2024-12-17"
        self.evaluation_model = "gpt-4o-2024-05-13"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "realtime=v1",
        }

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
                return persona
        return None

    def get_behavior(self, behavior_name: str) -> Optional[Behavior]:
        """Get a behavior by name."""
        for behavior in self.personas_data.get("behaviors", []):
            if behavior.get("name") == behavior_name:
                return behavior
        return None

    def _mark_test_as_failed_and_update_dynamo(self, test_id, error, action="failed"):
        """
        Mark a test as failed with an error message.

        Args:
            error: Error message
            action: Optional action description
        """
        from app.services.dynamodb_service import dynamodb_service

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

        question_text = ""
        if hasattr(test_case.config, "question"):
            question_text = test_case.config.question

        logger.info(f"Test case question: {question_text}")

        # Initialize test tracking in memory with more details
        self.active_tests[test_id] = {
            "test_case": test_case.dict(),
            "status": "starting",
            "start_time": start_time,
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

        # Initialize blank test report with the new structure
        from app.models.reports import EvaluationMetrics

        # initialize blank test report
        report = TestCaseReport(
            id=uuid.uuid4(),
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            persona_name=test_case.config.persona_name,
            behavior_name=test_case.config.behavior_name,
            question=question_text,
            conversation=[],  # Empty conversation list
            metrics=EvaluationMetrics(  # Initialize metrics
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
        from app.services.dynamodb_service import dynamodb_service

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
                logger.error(f"Twilio call initiation error: {str(twilio_error)}")
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
            logger.info(f"Call initiated with SID: {call_sid}")

            # Verify the test status is still waiting_for_call and explicitly set it if not
            if self.active_tests[test_id]["status"] != "waiting_for_call":
                logger.warning(
                    f"Test status changed unexpectedly! Current status: {self.active_tests[test_id]['status']}"
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
                logger.warning(f"Forced test status back to waiting_for_call")

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
            self.active_tests[test_id]["call_sid"] = call_sid
            dynamodb_service.save_test(test_id, self.active_tests[test_id])
            logger.info(f"Updated test in DynamoDB with call_sid")

            # Log status again to confirm
            logger.debug(
                f"Test status after call initiated: {self.active_tests[test_id]['status']}"
            )
            logger.info(f"Test {test_id} is waiting for call with call_sid: {call_sid}")

            # Update test with call information
            if "calls" not in self.active_tests[test_id]:
                self.active_tests[test_id]["calls"] = {}

            self.active_tests[test_id]["calls"][call_sid] = {
                "status": "initiated",
                "start_time": time.time(),
            }

            # Update in DynamoDB again to ensure all data is saved

            successful = dynamodb_service.save_test(test_id, self.active_tests[test_id])
            if successful:
                logger.info(f"Final test data update to DynamoDB complete")
            else:
                logger.error(f"Error in final DynamoDB update for test {test_id}")

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
        Process a call after it has ended with improved conversation handling.
        """
        logger.info(f"Processing call for test {test_id}, call {call_sid}")

        try:
            if test_id not in self.active_tests:
                logger.info(
                    f"Test {test_id} not found in active tests, checking DynamoDB"
                )
                from .dynamodb_service import dynamodb_service

                test_data = dynamodb_service.get_test(test_id)

                if test_data:
                    logger.info(f"Test {test_id} found in DynamoDB, loading")
                    self.active_tests[test_id] = test_data
                else:
                    logger.error(f"Test {test_id} not found in DynamoDB")
                    return

            # Ensure we have conversation data
            if not conversation or len(conversation) == 0:
                logger.warning(f"No conversation data provided for test {test_id}")
                # Check if we have conversation data in active_tests
                if "conversation" in self.active_tests[test_id]:
                    conversation = self.active_tests[test_id]["conversation"]
                    logger.info(
                        f"Using conversation data from active_tests: {len(conversation)} turns"
                    )
                else:
                    logger.error(f"No conversation data available for test {test_id}")
                    return

            # Log conversation data for debugging
            logger.info(f"Processing conversation with {len(conversation)} turns")

            # Update test status
            self.active_tests[test_id]["status"] = "processing"
            self.active_tests[test_id]["call_sid"] = call_sid
            self.active_tests[test_id]["end_time"] = time.time()

            # Update in DynamoDB
            from .dynamodb_service import dynamodb_service

            dynamodb_service.update_test_status(test_id, "processing")
            dynamodb_service.save_test(test_id, self.active_tests[test_id])

            # Generate report
            report = await self.generate_report_from_conversation(test_id, conversation)
            logger.info(f"Generated report with ID: {report.id}")

            # Update test with report ID
            self.active_tests[test_id]["report_id"] = str(report.id)
            dynamodb_service.save_test(test_id, self.active_tests[test_id])
        except Exception as e:
            logger.error(f"Error processing call: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())

    async def generate_report_from_conversation(
        self, test_id: str, conversation: List[Dict[str, Any]]
    ) -> TestCaseReport:
        """Generate a report from the conversation with better error handling."""
        logger.info(
            f"Generating report for test {test_id} with {len(conversation)} turns"
        )

        try:
            # Load test case data
            if test_id not in self.active_tests:
                logger.error(f"Test {test_id} not found in active tests")

                # Try to load from DynamoDB
                from .dynamodb_service import dynamodb_service

                test_data = dynamodb_service.get_test(test_id)

                if test_data:
                    logger.info(f"Loaded test {test_id} from DynamoDB")
                    self.active_tests[test_id] = test_data
                else:
                    logger.error(f"Test {test_id} not found in DynamoDB")
                    # Create a default test case
                    from ..models.test_cases import TestCase, TestCaseConfig

                    test_case = TestCase(
                        id=uuid.UUID(test_id),
                        name="Unknown Test",
                        config=TestCaseConfig(
                            persona_name="Unknown",
                            behavior_name="Unknown",
                            question="Unknown question",
                        ),
                    )

            test_data = self.active_tests[test_id]

            # Convert the test case dictionary to a TestCase object
            from app.models.test_cases import TestCase

            if isinstance(test_data.get("test_case"), dict):
                test_case = TestCase(**test_data["test_case"])
            else:
                # Fallback to a default test case
                from app.models.test_cases import TestCase, TestCaseConfig

                test_case = TestCase(
                    id=uuid.UUID(test_id),
                    name="Unknown Test",
                    config=TestCaseConfig(
                        persona_name="Unknown",
                        behavior_name="Unknown",
                        question="Unknown question",
                    ),
                )

            # Check for conversation turns, using the provided conversation or loading from the test data
            if not conversation or len(conversation) == 0:
                logger.info(f"No conversation provided, checking test data")
                if "conversation" in test_data and test_data["conversation"]:
                    conversation = test_data["conversation"]
                    logger.info(f"Found {len(conversation)} turns in test data")
                else:
                    logger.error(f"No conversation found for test {test_id}")
                    conversation = []

            # Calculate execution time
            end_time = test_data.get("end_time", time.time())
            start_time = test_data.get(
                "start_time", time.time() - 300
            )  # Default to 5 minutes ago

            # Convert to numeric if needed
            if isinstance(end_time, str):
                try:
                    from dateutil import parser

                    end_time = parser.parse(end_time).timestamp()
                except:
                    end_time = time.time()

            if isinstance(start_time, str):
                try:
                    from dateutil import parser

                    start_time = parser.parse(start_time).timestamp()
                except:
                    start_time = time.time() - 300

            execution_time = end_time - start_time

            # Convert conversation turns to standard objects for OpenAI evaluation
            from app.models.reports import ConversationTurn

            conversation_turns = []

            # Log conversation for debugging
            logger.info(
                f"Processing {len(conversation)} conversation turns for evaluation"
            )
            for i, turn in enumerate(conversation):
                logger.info(
                    f"Turn {i}: {turn.get('speaker')} - {turn.get('text', '')[:50]}..."
                )

                # Convert timestamp if needed
                timestamp = turn.get("timestamp")
                if isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(
                            timestamp.replace("Z", "+00:00")
                        )
                    except ValueError:
                        timestamp = datetime.now()
                elif timestamp is None:
                    timestamp = datetime.now()

                conversation_turns.append(
                    ConversationTurn(
                        speaker=turn.get("speaker", "unknown"),
                        text=turn.get("text", ""),
                        timestamp=timestamp,
                        audio_url=turn.get("audio_url"),
                    )
                )

            logger.info(f"Converted {len(conversation_turns)} conversation turns")

            # Get question text
            question_text = "Default Question"
            if hasattr(test_case.config, "question"):
                question_text = test_case.config.question

            logger.info(f"Using question: {question_text}")
            # Evaluate the conversation

            try:
                metrics = await self.evaluate_conversation(
                    question=question_text,
                    conversation=[turn.dict() for turn in conversation_turns],
                    knowledge_base=self.knowledge_base,
                    test_case=test_data.get("test_case"),
                )
                logger.info(f"Evaluation metrics: {metrics}")
            except Exception as eval_error:
                logger.error(f"Error evaluating conversation: {str(eval_error)}")
                # Create default metrics with error
                from ..models.reports import EvaluationMetrics

                metrics = EvaluationMetrics(
                    accuracy=0.5,
                    empathy=0.5,
                    response_time=3.0,
                    successful=False,
                    error_message=f"Evaluation error: {str(eval_error)}",
                )

            # Create final report - simplified for single question
            from ..models.reports import TestCaseReport

            existing_report_id = self.active_tests[test_id]["report_id"]
            if existing_report_id:
                report_id = existing_report_id
                logger.info(f"Updating existing report: {report_id}")
            else:
                # Generate new report ID if none exists
                report_id = str(uuid.uuid4())
                logger.info(f"Creating new report: {report_id}")

            report = TestCaseReport(
                id=uuid.UUID(report_id),  # Use existing ID if available
                test_case_id=test_case.id,
                test_case_name=test_case.name,
                persona_name=test_case.config.persona_name,
                behavior_name=test_case.config.behavior_name,
                question=question_text,
                conversation=conversation_turns,
                metrics=metrics,
                execution_time=execution_time,
                special_instructions=test_case.config.special_instructions,
            )
            # Save report
            from ..services.s3_service import s3_service

            report_id = str(report.id)
            report_dict = report.dict()

            # Add debug info
            report_dict["debug_info"] = {
                "conversation_count": len(conversation),
                "evaluation_time": datetime.now().isoformat(),
                "has_knowledge_base": bool(self.knowledge_base),
            }

            s3_service.save_report(report_dict, report_id)
            logger.info(
                f"Report {report_id} saved with {len(conversation_turns)} turns"
            )

            # Update test status
            self.active_tests[test_id]["status"] = "completed"
            self.active_tests[test_id]["report_id"] = report_id

            # Update in DynamoDB
            from ..services.dynamodb_service import dynamodb_service

            dynamodb_service.save_test(test_id, self.active_tests[test_id])

            return report
        except Exception as e:
            logger.error(f"Error generating report: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())

            # Generate a basic report even if there's an error
            from ..models.reports import TestCaseReport, EvaluationMetrics

            report = TestCaseReport(
                test_case_id=uuid.UUID(test_id) if test_id else uuid.uuid4(),
                test_case_name="Error Report",
                persona_name="Unknown",
                behavior_name="Unknown",
                question="Unknown question",
                conversation=[],
                metrics=EvaluationMetrics(
                    accuracy=0.0,
                    empathy=0.0,
                    response_time=0.0,
                    successful=False,
                    error_message=f"Error generating report: {str(e)}",
                ),
                execution_time=60.0,
            )

            # Save error report
            report_id = str(report.id)
            from ..services.s3_service import s3_service

            s3_service.save_report(report.dict(), report_id)

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
            metrics = await self.evaluate_conversation(
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

    def record_conversation_turn(
        self,
        test_id: str,
        call_sid: str,
        speaker: str,
        text: str,
        audio_url: Optional[str] = None,
    ):
        """
        Record a turn in the conversation with enhanced audio support and better error handling.
        Only adds a new turn if it doesn't match the last turn for this speaker.
        """
        if not test_id:
            logger.error("Attempted to record conversation turn with missing test_id")
            return None

        # Ensure the test exists in active_tests
        if test_id not in self.active_tests:
            logger.warning(f"Test {test_id} not found in active_tests, initializing")
            self.active_tests[test_id] = {
                "status": "in_progress",
                "call_sid": call_sid,
                "start_time": datetime.now().isoformat(),
                "conversation": [],
            }

            # Save to DynamoDB immediately to establish record
            from app.services.dynamodb_service import dynamodb_service

            dynamodb_service.save_test(test_id, self.active_tests[test_id])

        # Initialize conversation array if needed
        if "conversation" not in self.active_tests[test_id]:
            self.active_tests[test_id]["conversation"] = []

        # Check if we already have this turn (prevent duplication)
        conversation = self.active_tests[test_id]["conversation"]

        # Use timestamp with microseconds for uniqueness
        timestamp = datetime.now().isoformat(timespec="microseconds")

        # Create a new turn with timestamp
        turn = {"speaker": speaker, "text": text, "timestamp": timestamp}

        if audio_url:
            turn["audio_url"] = audio_url

        # Add to conversation
        self.active_tests[test_id]["conversation"].append(turn)

        # Update test in DynamoDB to persist conversation state
        try:
            from app.services.dynamodb_service import dynamodb_service

            dynamodb_service.save_test(test_id, self.active_tests[test_id])
            logger.info(f"Saved new conversation turn to DynamoDB for test {test_id}")
        except Exception as e:
            logger.error(f"Error saving conversation turn to DynamoDB: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")

        return turn

    def _create_evaluation_prompt(
        self,
        question: str,
        conversation: List[Dict[str, str]],
        knowledge_base: Dict[str, Any],
        test_case: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create an evaluation prompt based on the conversation and knowledge base."""
        conversation_text = ""
        nl = "\n"  # necessary because you shouldn't have \ in f-strings
        for turn in conversation:
            speaker = turn["speaker"]
            text = turn["text"]
            conversation_text += f"{speaker}: {text}{nl}{nl}"

        # Check if there's a specific FAQ question and answer to evaluate against
        faq_question = None
        expected_answer = None

        if test_case and "config" in test_case:
            config = test_case.get("config", {})
            faq_question = config.get("faq_question")
            expected_answer = config.get("expected_answer")

        # Build the accuracy evaluation section based on whether we have a specific FAQ
        accuracy_section = ""
        if faq_question and expected_answer:
            accuracy_section = f"""
            1. Accuracy (0-1 scale): 
            - Specifically evaluate the agent's response to: "{faq_question}"
            - The expected answer is: "{expected_answer}"
            - Did the agent provide correct information based on this expected answer?
            - Did they address the customer's question completely?
            - Did they avoid providing incorrect information?
            """
        else:
            accuracy_section = """
            1. Accuracy (0-1 scale): 
            - Did the agent provide correct information based on the knowledge base?
            - Did they address the customer's question completely?
            - Did they avoid providing incorrect information?
            """

        return f"""
            Please evaluate this customer service conversation between an AI agent and a customer.
            
            Original customer question: "{question}"
            
            Conversation transcript:
            {conversation_text}
            
            {f'Knowledge base information: {nl} {json.dumps(knowledge_base, indent=2)}' if not (faq_question and expected_answer) else ''}
            
            Evaluate the conversation on the following metrics:
            
            {accuracy_section}
            
            2. Empathy (0-1 scale):
            - Did the agent acknowledge the customer's feelings and situation?
            - Did they use appropriate tone and language for the customer's behavior?
            - Did they show understanding and patience?
            
            3. Response time:
            - Estimate the average response time in seconds (based on conversation flow)
            
            Provide your evaluation in JSON format with ratings and brief explanations:
            
            {{
                "accuracy": 0.0-1.0,
                "accuracy_explanation": "brief explanation",
                "empathy": 0.0-1.0,
                "empathy_explanation": "brief explanation",
                "response_time": seconds,
                "overall_feedback": "brief summary feedback"
            }}
            """

    async def evaluate_conversation(
        self,
        question: str,
        conversation: List[Dict[str, str]],
        knowledge_base: Dict[str, Any],
        test_case: Optional[Dict[str, Any]] = None,
    ) -> EvaluationMetrics:
        """
        Evaluate a conversation using OpenAI.

        Args:
            question: The original question asked
            conversation: List of conversation turns with speaker and text
            knowledge_base: The knowledge base for reference
            test_case: Optional test case data that may contain faq_question and expected_answer

        Returns:
            EvaluationMetrics with accuracy and empathy scores
        """
        # Construct the prompt for evaluation
        prompt = self._create_evaluation_prompt(
            question, conversation, knowledge_base, test_case
        )

        response = requests.post(
            self.api_url,
            headers=self.headers,
            json={
                "model": self.evaluation_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert evaluator of customer service AI agents.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
        )

        if response.status_code != 200:
            logger.error(f"Error in evaluation: {response.text}")
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message=f"Evaluation failed with status {response.status_code}",
            )

        result = response.json()
        content = result["choices"][0]["message"]["content"]
        try:
            evaluation = json.loads(content)

            return EvaluationMetrics(
                accuracy=float(evaluation.get("accuracy", 0.0)),
                empathy=float(evaluation.get("empathy", 0.0)),
                response_time=float(evaluation.get("response_time", 0.0)),
                successful=True,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error parsing evaluation result: {str(e)}")
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message=f"Failed to parse evaluation result: {str(e)}",
            )


# Create a singleton instance
evaluator_service = EvaluatorService()
