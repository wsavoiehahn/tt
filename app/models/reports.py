# app/models/reports.py
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from uuid import UUID, uuid4
from datetime import datetime


class ConversationTurn(BaseModel):
    speaker: str  # "evaluator" or "agent"
    text: str
    timestamp: datetime
    audio_url: Optional[str] = None


class EvaluationMetrics(BaseModel):
    accuracy: float  # 0-1 scale
    empathy: float  # 0-1 scale
    response_time: float  # in seconds
    successful: bool = True
    error_message: Optional[str] = None


class TestCaseReport(BaseModel):
    id: UUID = uuid4()
    test_case_id: UUID
    test_case_name: str
    persona_name: str
    behavior_name: str
    question: str  # The single question being evaluated
    conversation: List[ConversationTurn]  # Direct conversation list
    metrics: EvaluationMetrics  # Direct metrics
    openai_feedback: Optional[Dict[str, Any]] = None  # Raw feedback from OpenAI
    executed_at: datetime = datetime.now()
    execution_time: float  # Total execution time in seconds
    special_instructions: Optional[str] = None


class AggregateReport(BaseModel):
    id: UUID = uuid4()
    name: str
    description: Optional[str] = None
    test_case_reports: List[TestCaseReport]
    overall_metrics: Dict[str, Any]  # Aggregated metrics across all test cases
    created_at: datetime = datetime.now()
    tags: Optional[List[str]] = None
