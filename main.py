import json
from datetime import datetime, timedelta, timezone

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

DURATION_CAPS: dict[str, int] = {
    "ReadOnly": 30,
    "PowerUser": 7,
    "NetworkAdmin": 14,
    "DataEngineer": 14,
}

PERMISSION_SETS = list(DURATION_CAPS.keys())

# Hardcoded account list — replace with Organizations API call once backend is wired up
AWS_ACCOUNTS = [
    {"id": "111111111111", "name": "ak-dev"},
    {"id": "222222222222", "name": "ak-staging"},
    {"id": "333333333333", "name": "ak-production"},
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
            "permission_sets": PERMISSION_SETS,
            "duration_caps": json.dumps(DURATION_CAPS),
        },
    )


@app.post("/request-access")
@limiter.limit("5/minute")
async def request_access(
    request: Request,
    requester_name: str = Form(...),
    requester_email: str = Form(...),
    aws_account_id: str = Form(...),
    permission_set: str = Form(...),
    duration_days: int = Form(...),
    justification: str = Form(""),
):
    # Email domain guard
    domain = requester_email.split("@")[-1].lower()
    if domain != ALLOWED_EMAIL_DOMAIN:
        raise HTTPException(
            status_code=400,
            detail=f"Email must be an @{ALLOWED_EMAIL_DOMAIN} address.",
        )

    # Permission set validation
    if permission_set not in DURATION_CAPS:
        raise HTTPException(status_code=400, detail="Invalid permission set.")

    # Duration cap
    max_days = DURATION_CAPS[permission_set]
    if not (1 <= duration_days <= max_days):
        raise HTTPException(
            status_code=400,
            detail=f"{permission_set} access is capped at {max_days} day(s).",
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
