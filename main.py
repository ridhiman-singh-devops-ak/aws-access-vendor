import json
import logging
import urllib.request

import boto3
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Static config
# ---------------------------------------------------------------------------
ALLOWED_EMAIL_DOMAIN = "armakuni.com"
MAX_DURATION_DAYS = 15

AWS_ACCOUNTS = [
    {"id": "178647777766", "name": "AI-Solutions"},
    {"id": "222634373323", "name": "Armakuni Learning Account"},
    {"id": "443370699517", "name": "Armakuni LLC"},
    {"id": "941377159697", "name": "Development @Armakuni"},
    {"id": "038462780679", "name": "Internal @Armakuni"},
    {"id": "205930617989", "name": "Production @Armakuni"},
]

AWS_SERVICES = [
    "EC2",
    "Lambda",
    "Elastic Beanstalk",
    "EC2 Image Builder",
    "AWS App Runner",
    "S3",
    "DynamoDB",
    "VPC",
    "CloudFront",
    "API Gateway",
    "Amazon Q Developer",
    "CloudWatch",
    "CloudFormation",
    "AWS Config",
    "CloudTrail",
    "Amazon SageMaker AI",
    "Amazon Polly",
    "Amazon Rekognition",
    "Amazon Textract",
    "Amazon Transcribe",
    "Amazon Q Business",
    "Amazon Bedrock",
    "Amazon Bedrock AgentCore",
    "Amazon Q",
    "Amazon Lex",
    "Analytics",
    "Athena",
    "Amazon OpenSearch Service",
    "Kinesis",
    "QuickSight",
    "AWS Lake Formation",
    "MSK",
    "AWS Clean Rooms",
    "Amazon SageMaker",
    "AWS Glue",
    "Amazon Data Firehose",
    "Cognito",
    "Secrets Manager",
    "Key Management Service",
    "Security Hub CSPM",
    "IAM",
    "Security Hub",
    "AWS Amplify",
    "Step Functions",
    "Amazon AppFlow",
    "Amazon MQ",
    "Simple Notification Service",
    "Simple Queue Service",
    "Managed Apache Airflow",
    "Amazon EventBridge",
    "Amazon Connect",
    "Amazon Chime",
    "Amazon Simple Email Service",
]

# ---------------------------------------------------------------------------
# Slack config — webhook URL loaded once at startup from Secrets Manager
# ---------------------------------------------------------------------------
_slack_webhook_url: str | None = None


def _get_secret(secret_name: str) -> str | None:
    try:
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        return sm.get_secret_value(SecretId=secret_name)["SecretString"]
    except Exception as exc:
        logger.warning("Could not load secret %s: %s", secret_name, exc)
        return None


def _load_slack_config() -> None:
    global _slack_webhook_url
    webhook_url = _get_secret("ak-aws-access-vending/slack-webhook-url")
    if webhook_url:
        _slack_webhook_url = webhook_url
    else:
        logger.warning("Slack webhook URL not found — Slack notifications disabled")


# ---------------------------------------------------------------------------
# FastAPI + rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def startup_event() -> None:
    _load_slack_config()


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def _post_slack_request(
    requester_name: str,
    requester_email: str,
    manager_email: str,
    access_type: str,
    account_name: str,
    services: list[str],
    duration_days: int,
    project_name: str,
    project_pm_lead: str,
    client_cost_borne: str,
    client_aws_access: str,
    use_case: str,
) -> None:
    if not _slack_webhook_url:
        logger.warning("Slack not configured — skipping notification")
        return

    services_text = ", ".join(services)

    payload = {
        "text": "New AWS Access Request",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":aws: New AWS Access Request", "emoji": True},
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Requester*\n{requester_name}"},
                    {"type": "mrkdwn", "text": f"*Email*\n{requester_email}"},
                    {"type": "mrkdwn", "text": f"*Manager*\n{manager_email}"},
                    {"type": "mrkdwn", "text": f"*Access Type*\n{access_type}"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*AWS Account*\n{account_name}"},
                    {"type": "mrkdwn", "text": f"*Duration*\n{duration_days} day(s)"},
                    {"type": "mrkdwn", "text": f"*Project*\n{project_name}"},
                    {"type": "mrkdwn", "text": f"*PM / Lead*\n{project_pm_lead}"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Client Bearing Cost?*\n{client_cost_borne}"},
                    {"type": "mrkdwn", "text": f"*Access to Client AWS Account?*\n{client_aws_access}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*AWS Services Requested*\n{services_text}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Use Case / Justification*\n{use_case}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                        "style": "primary",
                        "action_id": "approve_request",
                        "value": "static_placeholder",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny", "emoji": True},
                        "style": "danger",
                        "action_id": "deny_request",
                        "value": "static_placeholder",
                    },
                ],
            },
        ],
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _slack_webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        logger.error("Failed to post Slack message: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "accounts": AWS_ACCOUNTS,
            "aws_services": json.dumps(AWS_SERVICES),
            "max_duration": MAX_DURATION_DAYS,
        },
    )


@app.post("/request-access")
@limiter.limit("5/minute")
async def request_access(
    request: Request,
    requester_name: str = Form(...),
    requester_email: str = Form(...),
    manager_email: str = Form(...),
    access_type: str = Form(...),
    aws_account_id: str = Form(...),
    selected_services: str = Form(...),
    duration_days: int = Form(...),
    project_name: str = Form(...),
    project_pm_lead: str = Form(...),
    client_cost_borne: str = Form(...),
    client_aws_access: str = Form(...),
    use_case: str = Form(...),
):
    # Email domain guards
    for email in [requester_email, manager_email]:
        if email.split("@")[-1].lower() != ALLOWED_EMAIL_DOMAIN:
            raise HTTPException(
                status_code=400,
                detail=f"All emails must be @{ALLOWED_EMAIL_DOMAIN} addresses.",
            )

    # Services validation
    services = [s.strip() for s in selected_services.split(",") if s.strip()]
    if not services:
        raise HTTPException(status_code=400, detail="Select at least one AWS service.")

    # Access type validation
    if access_type not in ("POC", "RAPID"):
        raise HTTPException(status_code=400, detail="Invalid access type.")

    # Duration validation
    if not (1 <= duration_days <= MAX_DURATION_DAYS):
        raise HTTPException(
            status_code=400,
            detail=f"Duration must be between 1 and {MAX_DURATION_DAYS} days.",
        )

    # Resolve account name
    account_name = next(
        (a["name"] for a in AWS_ACCOUNTS if a["id"] == aws_account_id), aws_account_id
    )

    # Post Slack approval card
    _post_slack_request(
        requester_name=requester_name,
        requester_email=requester_email,
        manager_email=manager_email,
        access_type=access_type,
        account_name=account_name,
        services=services,
        duration_days=duration_days,
        project_name=project_name,
        project_pm_lead=project_pm_lead,
        client_cost_borne=client_cost_borne,
        client_aws_access=client_aws_access,
        use_case=use_case,
    )

    return RedirectResponse(
        url=f"/success?name={requester_name}&account={account_name}&days={duration_days}",
        status_code=303,
    )


@app.get("/success", response_class=HTMLResponse)
async def success(request: Request, name: str = "", account: str = "", days: int = 0):
    return templates.TemplateResponse(
        request,
        "success.html",
        context={"name": name, "account": account, "days": days},
    )
