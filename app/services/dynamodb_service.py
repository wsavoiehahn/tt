# app/services/dynamodb_service.py
import json
import boto3
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class DynamoDBService:
    """Service for managing test data in DynamoDB."""

    def __init__(self, table_name="ai-call-center-evaluator-dev-tests"):
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb")
        self.table = self.dynamodb.Table(table_name)

    def ensure_table_exists(self):
        """Ensure the DynamoDB table exists, create it if it doesn't."""
        try:
            self.dynamodb.meta.client.describe_table(TableName=self.table_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                # Create the table
                logger.info(f" Creating DynamoDB table {self.table_name}")
                self.dynamodb.create_table(
                    TableName=self.table_name,
                    KeySchema=[{"AttributeName": "test_id", "KeyType": "HASH"}],
                    AttributeDefinitions=[
                        {"AttributeName": "test_id", "AttributeType": "S"}
                    ],
                    ProvisionedThroughput={
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                )
                # Wait until the table exists
                self.dynamodb.meta.client.get_waiter("table_exists").wait(
                    TableName=self.table_name
                )
            else:
                logger.error(f"Error checking DynamoDB table: {str(e)}")
                raise

    def save_test(self, test_id: str, test_data: Dict[str, Any]) -> bool:
        """
        Save test data to DynamoDB.

        Args:
            test_id: The test ID
            test_data: The test data dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug(f"Saving test {test_id} to DynamoDB")

            # Convert any non-serializable objects to strings
            test_data_json = json.dumps(test_data, default=str)

            # Store in DynamoDB
            self.table.put_item(
                Item={
                    "test_id": test_id,
                    "test_data": test_data_json,
                    "created_at": datetime.now().isoformat(),
                    "status": test_data.get("status", "unknown"),
                }
            )
            logger.debug(f"Test {test_id} saved to DynamoDB")
            return True
        except Exception as e:
            logger.error(f"Error saving test to DynamoDB: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def get_test(self, test_id: str) -> Optional[Dict[str, Any]]:
        """
        Get test data from DynamoDB.

        Args:
            test_id: The test ID

        Returns:
            The test data dictionary, or None if not found
        """
        try:
            logger.debug(f"Getting test {test_id} from DynamoDB")
            response = self.table.get_item(Key={"test_id": test_id})

            if "Item" not in response:
                logger.info(f"Test {test_id} not found in DynamoDB")
                return None

            # Parse the stored JSON
            test_data = json.loads(response["Item"]["test_data"])
            logger.debug(f"Retrieved test {test_id} from DynamoDB")
            return test_data
        except Exception as e:
            logger.error(f"Error getting test from DynamoDB: {str(e)}")
            return None

    def update_test_status(self, test_id: str, status: str) -> bool:
        """
        Update the status of a test in DynamoDB.

        Args:
            test_id: The test ID
            status: The new status

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug(f"Updating test {test_id} status to {status} in DynamoDB")
            response = self.table.update_item(
                Key={"test_id": test_id},
                UpdateExpression="set #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": status},
                ReturnValues="UPDATED_NEW",
            )
            logger.debug(f"Updated test {test_id} status in DynamoDB")
            return True
        except Exception as e:
            logger.error(f"Error updating test status in DynamoDB: {str(e)}")
            return False

    def delete_test(self, test_id: str) -> bool:
        """
        Delete a test from DynamoDB.

        Args:
            test_id: The test ID

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.error(f"DEBUG: Deleting test {test_id} from DynamoDB")
            self.table.delete_item(Key={"test_id": test_id})
            logger.error(f"DEBUG: Deleted test {test_id} from DynamoDB")
            return True
        except Exception as e:
            logger.error(f"DEBUG: Error deleting test from DynamoDB: {str(e)}")
            return False


# Create a singleton instance
dynamodb_service = DynamoDBService()
