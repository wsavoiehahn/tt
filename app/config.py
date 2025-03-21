# app/config.py
import os
import json
import boto3
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from pydantic import BaseModel, Field
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Load environment variables from .env file
load_dotenv()


class AppConfig(BaseModel):
    CLIENT_ID: str
    ENV_TIER: str  # set to "local" if running locally!
    AWS_DEFAULT_REGION: str
    LOCAL_MODE: bool = False
    LOCAL_STORAGE_PATH: str = "./storage"
    OPENAI_API_KEY: str
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    URL: str
    TWILIO_PHONE_NUMBER: str
    TARGET_PHONE_NUMBER: str
    KNOWLEDGE_BASE_PATH: str
    PERSONAS_PATH: str
    S3_BUCKET_NAME: str  # base bucket name from AWS params or env
    PORT: int
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str

    KNOWLEDGE_BASE: Dict[str, Any] = Field(default_factory=dict)
    PERSONAS: Dict[str, Any] = Field(default_factory=dict)

    # dynamically construct the full bucket name
    @property
    def FULL_S3_BUCKET_NAME(self):
        return f"{self.CLIENT_ID}-{self.ENV_TIER}-{self.S3_BUCKET_NAME}"

    def is_local_mode(self):
        return self.LOCAL_MODE

    def _load_local_file(self, key: str) -> Dict[str, Any]:
        file_path = f"{self.LOCAL_STORAGE_PATH}/{key}"
        with open(file_path, "r") as f:
            return json.load(f)

    def load_json_file(self, key: str) -> Dict[str, Any]:
        if self.is_local_mode():
            logger.info("Loading JSON from local storage")
            return self._load_local_file(key)

        s3_client = boto3.client("s3", region_name=self.AWS_DEFAULT_REGION)
        try:
            response = s3_client.get_object(Bucket=self.FULL_S3_BUCKET_NAME, Key=key)
            json_data = json.loads(response["Body"].read().decode("utf-8"))
        except Exception as e:
            logging.error(
                f"Error loading knowledge base from S3: {str(e)}. Improper location"
            )
        return json_data

    @classmethod
    def load(cls):
        local_mode = os.getenv("LOCAL_MODE", "false").lower() == "true"

        required_vars = [
            "OPENAI_API_KEY",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "URL",
            "TWILIO_PHONE_NUMBER",
            "TARGET_PHONE_NUMBER",
            "KNOWLEDGE_BASE_PATH",
            "PERSONAS_PATH",
            "S3_BUCKET_NAME",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "PORT",
        ]

        if local_mode:
            missing = [var for var in required_vars if var not in os.environ]
            if missing:
                raise ValueError(f"Missing vars in LOCAL_MODE: {missing}")
            config = {var: os.getenv(var) for var in required_vars}
        else:
            CLIENT_ID = os.environ["CLIENT_ID"]
            ENV_TIER = os.environ["ENV_TIER"]
            AWS_REGION = os.environ["AWS_DEFAULT_REGION"]

            ssm = boto3.client("ssm", region_name=AWS_REGION)
            param_paths = [f"{CLIENT_ID}/{ENV_TIER}/{var}" for var in required_vars]

            try:
                response = ssm.get_parameters(Names=param_paths, WithDecryption=True)
                aws_params = {
                    param["Name"]: param["Value"] for param in response["Parameters"]
                }

                missing = set(param_paths) - set(aws_params.keys())
                if missing:
                    raise ValueError(f"Missing AWS parameters: {missing}")

                config = {
                    var: aws_params[f"{CLIENT_ID}/{ENV_TIER}/{var}"]
                    for var in required_vars
                }
            except ClientError as e:
                raise RuntimeError(f"AWS Error: {e}")

        config.update(
            {
                "CLIENT_ID": os.environ["CLIENT_ID"],
                "ENV_TIER": os.environ["ENV_TIER"],
                "AWS_DEFAULT_REGION": os.environ["AWS_DEFAULT_REGION"],
                "LOCAL_MODE": local_mode,
                "LOCAL_STORAGE_PATH": os.getenv("LOCAL_STORAGE_PATH", "./storage"),
                "PORT": int(config["PORT"]),
            }
        )

        instance = cls(**config)
        # Load dictionaries after instance creation

        instance.KNOWLEDGE_BASE = instance.load_json_file(instance.KNOWLEDGE_BASE_PATH)
        instance.PERSONAS = instance.load_json_file(instance.PERSONAS_PATH)

        return instance

    def get_persona_traits(self, persona_name):
        """
        Returns the traits for the given persona name.
        If the persona is not found, returns None.
        """
        return next(
            (
                persona["traits"]
                for persona in self.PERSONAS["personas"]
                if persona["name"] == persona_name
            ),
            None,
        )

    def get_behavior_characteristics(self, behavior_name):
        """
        Returns the characteristics for the given behavior name.
        If the behavior is not found, returns None.
        """
        return next(
            (
                behavior["characteristics"]
                for behavior in self.PERSONAS["behaviors"]
                if behavior["name"] == behavior_name
            ),
            None,
        )


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

    def get_persona_traits(self, persona_name):
        """
        Returns the traits for the given persona name.
        If the persona is not found, returns None.
        """
        data = self.load_personas()
        return next(
            (
                persona["traits"]
                for persona in data["personas"]
                if persona["name"] == persona_name
            ),
            None,
        )

    def get_behavior_characteristics(self, behavior_name):
        """
        Returns the characteristics for the given behavior name.
        If the behavior is not found, returns None.
        """
        data = self.load_personas()
        return next(
            (
                behavior["characteristics"]
                for behavior in data["behaviors"]
                if behavior["name"] == behavior_name
            ),
            None,
        )


# Create a singleton instance
config = Config()

# Singleton instance initialized here
app_config = AppConfig.load()
