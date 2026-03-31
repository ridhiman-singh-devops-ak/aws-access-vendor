import json

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

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
# FastAPI + rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

templates = Jinja2Templates(directory="templates")

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

    # TODO: write to DynamoDB + post Slack approval card once backend is wired up

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
