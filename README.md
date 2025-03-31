# AI Call Center Evaluator

A system for evaluating AI call center agent performance across various personas and behaviors.

## Overview

The AI Call Center Evaluator simulates customer calls to test and evaluate the performance of AI call center agents. It can be configured to use different customer personas (Tech-Savvy, Elderly, Non-Native Speaker, etc.) and behaviors (Frustrated, Confused, Urgent, etc.) to create realistic testing scenarios.

The system:
- Initiates outbound calls using Twilio
- Conducts simulated customer conversations
- Records and transcribes the interactions
- Evaluates the performance of the AI agent based on accuracy, empathy, and response time
- Generates detailed reports for analysis

## System Architecture

### Key Components

- **FastAPI Backend** - Powers the API and dashboard
- **Twilio Integration** - Handles outbound calls and call status webhooks
- **OpenAI Realtime API** - Provides the customer simulation via WebSockets
- **AWS Integration** - S3 for storage and DynamoDB for state management
- **Web Dashboard** - Visualizes test results and analytics

### Services

- **EvaluatorService** - Manages test case execution and evaluation
- **TwilioService** - Handles Twilio API interactions
- **S3Service** - Manages storage of test data, audio, and reports
- **DynamoDBService** - Maintains test state across executions
- **ReportingService** - Generates and serves evaluation reports

## Installation

### Prerequisites

- Docker and Docker Compose
- AWS Account with S3 and DynamoDB access
- Twilio Account
- OpenAI API access
- ngrok (for webhook handling)

### Setup Local Environment

1. **Clone the repository:**

2. **Create a .env file:**
   Inside the root directory of the project folder copy the template below and fill in the required values:
   ```
   # OpenAI API credentials
   OPENAI_API_KEY=your_openai_api_key
   
   # Twilio credentials
   TWILIO_ACCOUNT_SID=your_twilio_account_sid
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   TWILIO_PHONE_NUMBER=your_twilio_phone_number
   
   # Target phone number for testing
   TARGET_PHONE_NUMBER=phone_number_to_call
   
   # Public URL for webhooks (provided by ngrok)
   URL=your_ngrok_url
   
   # Knowledge base and persona configuration paths
   KNOWLEDGE_BASE_PATH=./kb.json
   PERSONAS_PATH=./behaviorPersona.json
   
   # AWS configuration
   AWS_ACCESS_KEY_ID=your_aws_access_key
   AWS_SECRET_ACCESS_KEY=your_aws_secret_key
   AWS_DEFAULT_REGION=us-east-2
   S3_BUCKET_NAME=ai-call-center-evaluator-storage
   
   # Application settings
   PORT=4040
   CLIENT_ID=sendero
   ENV_TIER=dev
   LOCAL_MODE=true
   LOCAL_STORAGE_PATH=.
   ```

3. **Set up ngrok for webhook handling:**
   ```bash
   ngrok http 80
   ```
   
   Then, get the public URL and update your .env file:
   ```bash
   NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[0].public_url')
   STRIPPED_URL=${NGROK_URL#https://}
   echo "URL: $STRIPPED_URL"
   ```

4. **Start the application using Docker Compose:**
   ```bash
   docker-compose up
   ```

5. **For debugging (recommended for local development):**
   - Uncomment the debug command in docker-compose.yaml:
     ```yaml
     command: python -Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m uvicorn app.main:app --host 0.0.0.0 --port 80 --reload
     ```
   - Uncomment the debug command in Dockerfile:
     ```dockerfile
     CMD ["python", "-m", "debugpy", "--listen", "0.0.0.0:5678", "--wait-for-client", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
     ```
   - Use VSCode's "Attach to Docker" run configuration to connect to the debugger

### Production Deployment (EC2)

1. **Launch and configure an EC2 instance:**
   - Install Docker and Docker Compose
   - Configure AWS CLI with appropriate permissions

2. **Set up AWS Parameter Store:**
   Instead of using a .env file, store configuration in AWS Parameter Store with the following parameters:
   ```
   /{CLIENT_ID}/{ENV_TIER}/OPENAI_API_KEY
   /{CLIENT_ID}/{ENV_TIER}/TWILIO_ACCOUNT_SID
   /{CLIENT_ID}/{ENV_TIER}/TWILIO_AUTH_TOKEN
   /{CLIENT_ID}/{ENV_TIER}/URL
   /{CLIENT_ID}/{ENV_TIER}/TWILIO_PHONE_NUMBER
   /{CLIENT_ID}/{ENV_TIER}/TARGET_PHONE_NUMBER
   /{CLIENT_ID}/{ENV_TIER}/KNOWLEDGE_BASE_PATH
   /{CLIENT_ID}/{ENV_TIER}/PERSONAS_PATH
   /{CLIENT_ID}/{ENV_TIER}/S3_BUCKET_NAME
   /{CLIENT_ID}/{ENV_TIER}/AWS_ACCESS_KEY_ID
   /{CLIENT_ID}/{ENV_TIER}/AWS_SECRET_ACCESS_KEY
   /{CLIENT_ID}/{ENV_TIER}/PORT
   ```

3. **Set required environment variables using either method:**

   **Option A: Using a .env file (recommended)**
   Create a .env file in the project directory:
   ```
   CLIENT_ID=your_client_id
   ENV_TIER=prod
   AWS_DEFAULT_REGION=us-east-2
   LOCAL_MODE=false
   ```

   **Option B: Exporting variables**
   ```bash
   export CLIENT_ID=your_client_id
   export ENV_TIER=prod
   export AWS_DEFAULT_REGION=us-east-2
   export LOCAL_MODE=false
   ```

4. **Start ngrok to expose the application:**
   ```bash
   ngrok http 80
   ```

5. **Update the URL parameter in AWS Parameter Store:**
   ```bash
   NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[0].public_url')
   STRIPPED_URL=${NGROK_URL#https://}
   aws ssm put-parameter --name "/${CLIENT_ID}/${ENV_TIER}/URL" --value "$STRIPPED_URL" --type String --overwrite
   ```

6. **Start the application:**
   ```bash
   docker-compose up -d
   ```

## Configuration Reference

### Required Environment Variables

| Variable | Description | Local Setup | Production Setup |
|----------|-------------|-------------|-----------------|
| OPENAI_API_KEY | OpenAI API key | .env file | AWS Parameter Store |
| TWILIO_ACCOUNT_SID | Twilio account identifier | .env file | AWS Parameter Store |
| TWILIO_AUTH_TOKEN | Twilio authentication token | .env file | AWS Parameter Store |
| URL | Public URL for webhooks (from ngrok) | .env file | AWS Parameter Store |
| TWILIO_PHONE_NUMBER | Outbound phone number for calls | .env file | AWS Parameter Store |
| TARGET_PHONE_NUMBER | Target phone number for testing | .env file | AWS Parameter Store |
| KNOWLEDGE_BASE_PATH | Path to knowledge base JSON | .env file | AWS Parameter Store |
| PERSONAS_PATH | Path to personas/behaviors JSON | .env file | AWS Parameter Store |
| S3_BUCKET_NAME | Base name for S3 bucket | .env file | AWS Parameter Store |
| AWS_ACCESS_KEY_ID | AWS access key | .env file | AWS Parameter Store |
| AWS_SECRET_ACCESS_KEY | AWS secret key | .env file | AWS Parameter Store |
| PORT | Application port | .env file | AWS Parameter Store |
| CLIENT_ID | Client identifier | .env file | Environment variable |
| ENV_TIER | Environment tier (dev/prod) | .env file | Environment variable |
| AWS_DEFAULT_REGION | AWS region | .env file | Environment variable |
| LOCAL_MODE | Whether to use local storage | .env file | Environment variable |
| LOCAL_STORAGE_PATH | Path for local storage | .env file | Environment variable |

> **Note:** For sensitive values like API keys, please contact Vish or Will for the actual values.

### Knowledge Base and Personas

The system uses two JSON files for configuration:

- **kb.json** - Contains the knowledge base with FAQs and IVR scripts
- **behaviorPersona.json** - Contains persona and behavior definitions

## Usage

1. Access the dashboard at `http://localhost/dashboard`
2. Create a new test by clicking the "New Test" button
3. Select a persona, behavior, and enter a test question
4. Click "Create Test" to initiate a call
5. View the test results in the dashboard
6. Detailed reports are available by clicking on the report ID

## Debugging

For local development, use VSCode's debugging capabilities:

1. Start the application with the debug command enabled
2. Use the "Attach to Docker" run configuration in VSCode
3. Set breakpoints in your code
4. Trigger the relevant functionality from the dashboard

## Troubleshooting

- **Webhook Issues**: Ensure ngrok is running and the URL is correctly configured
- **Call Failures**: Check Twilio credentials and phone number configuration
- **Storage Errors**: Verify AWS credentials and S3 bucket permissions
- **API Errors**: Confirm OpenAI API key is valid and has sufficient credits

## Contributing

Please follow these steps to contribute:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Implement your changes
4. Run tests and ensure they pass
5. Commit your changes (`git commit -m 'Add my feature'`)
6. Push to the branch (`git push origin feature/my-feature`)
7. Create a Pull Request
