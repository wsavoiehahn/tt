# app/services/reporting.py
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import uuid

from ..models.reports import (
    TestCaseReport,
    AggregateReport,
    QuestionEvaluation,
    EvaluationMetrics,
)
from .s3_service import s3_service

logger = logging.getLogger(__name__)


class ReportingService:
    """Service for generating and managing reports."""

    def __init__(self):
        self.cached_reports = {}

    def generate_aggregate_report(
        self, report_ids: List[str], name: str, description: Optional[str] = None
    ) -> AggregateReport:
        """
        Generate an aggregate report from multiple test case reports.

        Args:
            report_ids: List of report IDs to aggregate
            name: Name of the aggregate report
            description: Description of the aggregate report

        Returns:
            AggregateReport containing aggregated metrics
        """
        logger.info(
            f"Generating aggregate report for {len(report_ids)} reports: {name}"
        )

        # Load individual reports
        test_case_reports = []
        for report_id in report_ids:
            report_data = s3_service.get_json(f"reports/{report_id}.json")
            if report_data:
                report = TestCaseReport(**report_data)
                test_case_reports.append(report)
            else:
                logger.warning(f"Could not load report {report_id}")

        if not test_case_reports:
            logger.error("No valid reports found for aggregation")
            # Return empty report
            return AggregateReport(
                name=name,
                description=description,
                test_case_reports=[],
                overall_metrics={
                    "accuracy": 0.0,
                    "empathy": 0.0,
                    "response_time": 0.0,
                    "total_questions": 0,
                    "success_rate": 0.0,
                    "error": "No valid reports found for aggregation",
                },
            )

        # Calculate overall metrics
        overall_metrics = self._calculate_aggregate_metrics(test_case_reports)

        # Create aggregate report
        aggregate_report = AggregateReport(
            name=name,
            description=description,
            test_case_reports=test_case_reports,
            overall_metrics=overall_metrics,
        )

        # Save report
        report_id = str(aggregate_report.id)
        s3_service.save_report(aggregate_report.dict(), report_id)

        return aggregate_report

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a report by ID with improved path handling.

        Args:
            report_id: Report ID

        Returns:
            Report data as dictionary, or None if not found
        """
        # Check cache first
        if report_id in self.cached_reports:
            try:
                # Verify the report still exists in S3 (lightweight check)
                s3_service.s3_client.head_object(
                    Bucket=s3_service.bucket_name,
                    Key=f"reports/{datetime.now().strftime('%Y%m%d')}/{report_id}.json",
                )
                return self.cached_reports[report_id]
            except Exception:
                # If report doesn't exist in today's folder, continue with full search
                # But don't remove from cache yet - it might be in a different date folder
                pass

        # Try to load from S3 with different possible paths
        report_data = None

        # First try current date folder (most likely location)
        current_date = datetime.now().strftime("%Y%m%d")
        possible_paths = [
            f"reports/{current_date}/{report_id}.json",  # Main format with date folder
            f"reports/{report_id}.json",  # Legacy/fallback with no date folder
        ]

        # If report not found, try looking in date folders from the past week
        if not report_data:
            # Add date folders from the past 7 days
            for i in range(1, 8):
                past_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
                possible_paths.append(f"reports/{past_date}/{report_id}.json")

        # Try all possible paths
        for path in possible_paths:
            try:
                data = s3_service.get_json(path)
                if data:
                    logger.info(f"Found report at path: {path}")
                    report_data = data
                    # Cache the report
                    self.cached_reports[report_id] = report_data
                    return report_data
            except Exception as e:
                # Just continue to the next path
                pass

        # Special case: If the report is in cache but not found in S3, still return it
        # This is useful for recently generated reports that might not be synced to S3 yet
        if report_id in self.cached_reports:
            logger.warning(f"Report {report_id} not found in S3, using cached version")
            return self.cached_reports[report_id]

        # If no report found, log a warning and return None
        logger.warning(f"Report {report_id} not found in any location")
        return None

    def list_reports(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        List available reports.

        Args:
            limit: Maximum number of reports to list

        Returns:
            List of report metadata
        """
        return s3_service.list_reports(limit)

    def generate_html_report(self, report_id: str) -> str:
        """
        Generate an HTML report from a report ID.

        Args:
            report_id: Report ID

        Returns:
            HTML report as string
        """
        report_data = self.get_report(report_id)
        if not report_data:
            return "<html><body><h1>Report Not Found</h1></body></html>"

        # Check if it's an aggregate or test case report
        if "test_case_reports" in report_data:
            return self._generate_aggregate_html_report(report_data)
        else:
            return self._generate_test_case_html_report(report_data)

    def _generate_test_case_html_report(self, report_data: Dict[str, Any]) -> str:
        """Generate HTML for a test case report."""
        test_case_name = report_data.get("test_case_name", "Unknown Test Case")
        persona_name = report_data.get("persona_name", "Unknown Persona")
        behavior_name = report_data.get("behavior_name", "Unknown Behavior")

        overall_metrics = report_data.get("overall_metrics", {})
        accuracy = overall_metrics.get("accuracy", 0) * 100
        empathy = overall_metrics.get("empathy", 0) * 100
        response_time = overall_metrics.get("response_time", 0)

        questions_html = ""
        for q_eval in report_data.get("questions_evaluated", []):
            question = q_eval.get("question", "Unknown Question")
            metrics = q_eval.get("metrics", {})
            q_accuracy = metrics.get("accuracy", 0) * 100
            q_empathy = metrics.get("empathy", 0) * 100
            q_response_time = metrics.get("response_time", 0)

            conversation_html = ""
            for turn in q_eval.get("conversation", []):
                speaker = turn.get("speaker", "Unknown")
                text = turn.get("text", "")
                speaker_class = "evaluator" if speaker == "evaluator" else "agent"

                conversation_html += f"""
                <div class="conversation-turn {speaker_class}">
                    <div class="speaker">{speaker}</div>
                    <div class="text">{text}</div>
                </div>
                """

            questions_html += f"""
            <div class="question-evaluation">
                <h3>Question: {question}</h3>
                <div class="metrics">
                    <div class="metric">
                        <span class="label">Accuracy:</span>
                        <span class="value">{q_accuracy:.1f}%</span>
                    </div>
                    <div class="metric">
                        <span class="label">Empathy:</span>
                        <span class="value">{q_empathy:.1f}%</span>
                    </div>
                    <div class="metric">
                        <span class="label">Response Time:</span>
                        <span class="value">{q_response_time:.2f}s</span>
                    </div>
                </div>
                <h4>Conversation:</h4>
                <div class="conversation">
                    {conversation_html}
                </div>
            </div>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Test Case Report: {test_case_name}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                h1, h2, h3, h4 {{
                    color: #2c3e50;
                }}
                .overview {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    border-radius: 5px;
                    margin-bottom: 30px;
                }}
                .overall-metrics {{
                    display: flex;
                    justify-content: space-between;
                    margin-top: 20px;
                }}
                .metric-card {{
                    background-color: white;
                    border-radius: 5px;
                    padding: 15px;
                    width: 30%;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .metric-title {{
                    font-size: 14px;
                    color: #7f8c8d;
                    margin-bottom: 5px;
                }}
                .metric-value {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #2980b9;
                }}
                .question-evaluation {{
                    background-color: white;
                    border-radius: 5px;
                    padding: 20px;
                    margin-bottom: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .metrics {{
                    display: flex;
                    justify-content: space-between;
                    margin: 15px 0;
                }}
                .metric {{
                    padding: 10px;
                    background-color: #f8f9fa;
                    border-radius: 5px;
                }}
                .conversation {{
                    background-color: #f8f9fa;
                    padding: 15px;
                    border-radius: 5px;
                }}
                .conversation-turn {{
                    margin-bottom: 15px;
                    padding: 10px;
                    border-radius: 5px;
                }}
                .evaluator {{
                    background-color: #e3f2fd;
                }}
                .agent {{
                    background-color: #e8f5e9;
                }}
                .speaker {{
                    font-weight: bold;
                    margin-bottom: 5px;
                }}
                .text {{
                    white-space: pre-wrap;
                }}
            </style>
        </head>
        <body>
            <h1>Test Case Report: {test_case_name}</h1>
            
            <div class="overview">
                <h2>Overview</h2>
                <p><strong>Persona:</strong> {persona_name}</p>
                <p><strong>Behavior:</strong> {behavior_name}</p>
                
                <div class="overall-metrics">
                    <div class="metric-card">
                        <div class="metric-title">Accuracy</div>
                        <div class="metric-value">{accuracy:.1f}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Empathy</div>
                        <div class="metric-value">{empathy:.1f}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Avg. Response Time</div>
                        <div class="metric-value">{response_time:.2f}s</div>
                    </div>
                </div>
            </div>
            
            <h2>Question Evaluations</h2>
            {questions_html}
        </body>
        </html>
        """

        return html

    def _generate_aggregate_html_report(self, report_data: Dict[str, Any]) -> str:
        """Generate HTML for an aggregate report."""
        name = report_data.get("name", "Unknown Report")
        description = report_data.get("description", "")

        overall_metrics = report_data.get("overall_metrics", {})
        accuracy = overall_metrics.get("accuracy", 0) * 100
        empathy = overall_metrics.get("empathy", 0) * 100
        response_time = overall_metrics.get("response_time", 0)
        success_rate = overall_metrics.get("success_rate", 0) * 100
        total_questions = overall_metrics.get("total_questions", 0)

        test_cases_html = ""
        for tc_report in report_data.get("test_case_reports", []):
            tc_name = tc_report.get("test_case_name", "Unknown Test Case")
            tc_persona = tc_report.get("persona_name", "Unknown Persona")
            tc_behavior = tc_report.get("behavior_name", "Unknown Behavior")
            tc_id = tc_report.get("id", "")

            tc_metrics = tc_report.get("overall_metrics", {})
            tc_accuracy = tc_metrics.get("accuracy", 0) * 100
            tc_empathy = tc_metrics.get("empathy", 0) * 100
            tc_response_time = tc_metrics.get("response_time", 0)

            test_cases_html += f"""
            <tr>
                <td><a href="/reports/{tc_id}/html">{tc_name}</a></td>
                <td>{tc_persona}</td>
                <td>{tc_behavior}</td>
                <td>{tc_accuracy:.1f}%</td>
                <td>{tc_empathy:.1f}%</td>
                <td>{tc_response_time:.2f}s</td>
            </tr>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Aggregate Report: {name}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                h1, h2, h3 {{
                    color: #2c3e50;
                }}
                .overview {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    border-radius: 5px;
                    margin-bottom: 30px;
                }}
                .description {{
                    margin: 20px 0;
                    padding: 15px;
                    background-color: #eff6ff;
                    border-radius: 5px;
                }}
                .metrics-container {{
                    display: flex;
                    flex-wrap: wrap;
                    justify-content: space-between;
                    margin: 20px 0;
                }}
                .metric-card {{
                    background-color: white;
                    border-radius: 5px;
                    padding: 15px;
                    width: 30%;
                    margin-bottom: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .metric-title {{
                    font-size: 14px;
                    color: #7f8c8d;
                    margin-bottom: 5px;
                }}
                .metric-value {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #2980b9;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 20px 0;
                }}
                th, td {{
                    text-align: left;
                    padding: 12px;
                    border-bottom: 1px solid #ddd;
                }}
                th {{
                    background-color: #f2f2f2;
                }}
                tr:hover {{
                    background-color: #f5f5f5;
                }}
                a {{
                    color: #3498db;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <h1>Aggregate Report: {name}</h1>
            
            <div class="overview">
                <h2>Overview</h2>
                
                <div class="description">
                    <p>{description}</p>
                </div>
                
                <div class="metrics-container">
                    <div class="metric-card">
                        <div class="metric-title">Overall Accuracy</div>
                        <div class="metric-value">{accuracy:.1f}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Overall Empathy</div>
                        <div class="metric-value">{empathy:.1f}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Avg. Response Time</div>
                        <div class="metric-value">{response_time:.2f}s</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Success Rate</div>
                        <div class="metric-value">{success_rate:.1f}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Total Questions</div>
                        <div class="metric-value">{total_questions}</div>
                    </div>
                </div>
            </div>
            
            <h2>Test Cases</h2>
            <table>
                <thead>
                    <tr>
                        <th>Test Case</th>
                        <th>Persona</th>
                        <th>Behavior</th>
                        <th>Accuracy</th>
                        <th>Empathy</th>
                        <th>Response Time</th>
                    </tr>
                </thead>
                <tbody>
                    {test_cases_html}
                </tbody>
            </table>
        </body>
        </html>
        """

        return html

    def _calculate_aggregate_metrics(
        self, test_case_reports: List[TestCaseReport]
    ) -> Dict[str, Any]:
        """
        Calculate aggregate metrics from multiple test case reports.

        Args:
            test_case_reports: List of test case reports

        Returns:
            Dictionary of aggregate metrics
        """
        # Initialize counters
        total_accuracy = 0.0
        total_empathy = 0.0
        total_response_time = 0.0
        total_successful_questions = 0
        total_questions = 0

        # Aggregate metrics across all test cases
        for report in test_case_reports:
            metrics = report.overall_metrics

            if metrics.successful:
                # Only include successful evaluations in average
                total_accuracy += metrics.accuracy
                total_empathy += metrics.empathy
                total_response_time += metrics.response_time
                total_successful_questions += 1

            # Count questions
            total_questions += len(report.questions_evaluated)

        # Calculate averages
        avg_accuracy = (
            total_accuracy / total_successful_questions
            if total_successful_questions > 0
            else 0
        )
        avg_empathy = (
            total_empathy / total_successful_questions
            if total_successful_questions > 0
            else 0
        )
        avg_response_time = (
            total_response_time / total_successful_questions
            if total_successful_questions > 0
            else 0
        )
        success_rate = (
            total_successful_questions / len(test_case_reports)
            if test_case_reports
            else 0
        )

        # Group metrics by persona and behavior
        persona_metrics = {}
        behavior_metrics = {}

        for report in test_case_reports:
            persona = report.persona_name
            behavior = report.behavior_name
            metrics = report.overall_metrics

            # Initialize if not exists
            if persona not in persona_metrics:
                persona_metrics[persona] = {
                    "accuracy": [],
                    "empathy": [],
                    "response_time": [],
                }
            if behavior not in behavior_metrics:
                behavior_metrics[behavior] = {
                    "accuracy": [],
                    "empathy": [],
                    "response_time": [],
                }

            # Only include successful evaluations
            if metrics.successful:
                persona_metrics[persona]["accuracy"].append(metrics.accuracy)
                persona_metrics[persona]["empathy"].append(metrics.empathy)
                persona_metrics[persona]["response_time"].append(metrics.response_time)

                behavior_metrics[behavior]["accuracy"].append(metrics.accuracy)
                behavior_metrics[behavior]["empathy"].append(metrics.empathy)
                behavior_metrics[behavior]["response_time"].append(
                    metrics.response_time
                )

        # Calculate averages by persona and behavior
        for persona in persona_metrics:
            for metric in persona_metrics[persona]:
                values = persona_metrics[persona][metric]
                persona_metrics[persona][metric] = (
                    sum(values) / len(values) if values else 0
                )

        for behavior in behavior_metrics:
            for metric in behavior_metrics[behavior]:
                values = behavior_metrics[behavior][metric]
                behavior_metrics[behavior][metric] = (
                    sum(values) / len(values) if values else 0
                )

        return {
            "accuracy": avg_accuracy,
            "empathy": avg_empathy,
            "response_time": avg_response_time,
            "success_rate": success_rate,
            "total_questions": total_questions,
            "total_successful_questions": total_successful_questions,
            "total_test_cases": len(test_case_reports),
            "by_persona": persona_metrics,
            "by_behavior": behavior_metrics,
        }


# Create a singleton instance
reporting_service = ReportingService()
