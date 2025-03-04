# app/config.py
import os
import json
import boto3
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Configuration manager that loads settings from environment variables or AWS Parameter Store."""

    def __init__(self, region_name: str = "us-east-2"):
        self.region_name = region_name
        self._config_cache = {}
        self._ssm_client = None

    @property
    def ssm_client(self):
        """Lazy-loaded SSM client."""
        if self._ssm_client is None:
            self._ssm_client = boto3.client("ssm", region_name=self.region_name)
        return self._ssm_client

    def get_parameter(self, name: str, use_cache: bool = True) -> str:
        """Get parameter from AWS Parameter Store."""
        if use_cache and name in self._config_cache:
            return self._config_cache[name]

        # Map of new parameter names to existing ones
        parameter_map = {
            "/ai-evaluator/openai_api_key": "/openai/api_key",
            "/ai-evaluator/twilio_account_sid": "/twilio/account_sid",
            "/ai-evaluator/twilio_auth_token": "/twilio/auth_token",
            "/ai-evaluator/default_outbound_number": "/twilio/phone_number",
            "/ai-evaluator/ai_service_phone_number": "/twilio/target_phone_number",
        }

        # Use mapped parameter if it exists
        param_name = parameter_map.get(name, name)

        try:
            response = self.ssm_client.get_parameter(
                Name=param_name, WithDecryption=True
            )
            value = response["Parameter"]["Value"]
            self._config_cache[name] = value
            return value
        except ClientError as e:
            print(f"Error fetching parameter {param_name}: {str(e)}")
            # Fall back to env vars if available
            env_var_name = name.split("/")[-1]
            return os.environ.get(env_var_name, "")

    def load_secrets(self) -> Dict[str, str]:
        """Load all required secrets."""
        secrets = {
            "openai_api_key": self.get_parameter("/openai/api_key"),
            "twilio_account_sid": self.get_parameter("/twilio/account_sid"),
            "twilio_auth_token": self.get_parameter("/twilio/auth_token"),
            "default_outbound_number": self.get_parameter("/twilio/phone_number"),
            "ai_service_phone_number": self.get_parameter(
                "/twilio/target_phone_number"
            ),
            "s3_bucket_name": self.get_parameter("/ai-evaluator/s3_bucket_name", False)
            or os.environ.get("S3_BUCKET_NAME", "ai-call-center-evaluator-dev-storage"),
        }
        return secrets

    def load_knowledge_base(self, kb_path: Optional[str] = None) -> Dict[str, Any]:
        """Load knowledge base from file or S3."""

        if os.environ.get("LOCAL_MODE") == "true":
            kb_path = os.environ.get("KNOWLEDGE_BASE_PATH", "kb.json")
            return self._load_local_file(kb_path)

        if kb_path is None:
            kb_path = self.get_parameter(
                "/ai-evaluator/knowledge_base_path", False
            ) or os.environ.get("KNOWLEDGE_BASE_PATH", "kb.json")

        if kb_path.startswith("s3://"):
            # Load from S3
            bucket, key = kb_path.replace("s3://", "").split("/", 1)
            s3_client = boto3.client("s3", region_name=self.region_name)
            try:
                response = s3_client.get_object(Bucket=bucket, Key=key)
                kb_data = json.loads(response["Body"].read().decode("utf-8"))
            except Exception as e:
                print(f"Error loading knowledge base from S3: {str(e)}")
                # Fallback to local file
                kb_data = self._load_local_file("kb.json")
        else:
            # Load from local file
            kb_data = self._load_local_file(kb_path)

        return kb_data

    def load_personas(self, personas_path: Optional[str] = None) -> Dict[str, Any]:
        """Load personas from file or S3."""
        if personas_path is None:
            personas_path = self.get_parameter(
                "/ai-evaluator/personas_path", False
            ) or os.environ.get("PERSONAS_PATH", "behaviorPersona.json")

        if personas_path.startswith("s3://"):
            # Load from S3
            bucket, key = personas_path.replace("s3://", "").split("/", 1)
            s3_client = boto3.client("s3", region_name=self.region_name)
            try:
                response = s3_client.get_object(Bucket=bucket, Key=key)
                personas_data = json.loads(response["Body"].read().decode("utf-8"))
            except Exception as e:
                print(f"Error loading personas from S3: {str(e)}")
                # Fallback to local file
                personas_data = self._load_local_file("behaviorPersona.json")
        else:
            # Load from local file
            personas_data = self._load_local_file(personas_path)

        return personas_data

    def _load_local_file(self, filepath: str) -> Dict[str, Any]:
        """Load and parse a local JSON file."""
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading file {filepath}: {str(e)}")
            return {}


# Create a singleton instance
config = Config()
