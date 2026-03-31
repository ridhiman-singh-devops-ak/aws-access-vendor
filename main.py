import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import boto3
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ---------------------------------------------------------------------------
# Config — fetched from Secrets Manager and SSM Parameter Store at startup
# ---------------------------------------------------------------------------
_AWS_REGION = "us-east-1"
_ssm = boto3.client("ssm", region_name=_AWS_REGION)
_sm = boto3.client("secretsmanager", region_name=_AWS_REGION)

_SECRET_PREFIX = "ak-aws-access-vending"
_PARAM_PREFIX = "/ak-aws-access-vending"


def _get_secret(name: str) -> str:
    resp = _sm.get_secret_value(SecretId=f"{_SECRET_PREFIX}/{name}")
    return resp["SecretString"]


def _get_param(name: str) -> str:
    resp = _ssm.get_parameter(Name=f"{_PARAM_PREFIX}/{name}", WithDecryption=False)
    return resp["Parameter"]["Value"]


# Secrets Manager — sensitive credentials
SLACK_BOT_TOKEN = _get_secret("slack-bot-token")
SLACK_SIGNING_SECRET = _get_secret("slack-signing-secret")
SLACK_CHANNEL_ID = _get_secret("slack-channel-id")
AWS_API_URL = _get_secret("aws-api-url")

# SSM Parameter Store — non-sensitive config
AWS_REGION = _AWS_REGION
PENDING_REQUESTS_TABLE = _get_param("pending-requests-table")
ALLOWED_EMAIL_DOMAIN = _get_param("allowed-email-domain")
SES_FROM_EMAIL = _get_param("ses-from-email")

# Per-permission-set maximum duration in days (enforced server-side)
DURATION_CAPS: dict[str, int] = {
    "ReadOnly": 30,
    "PowerUser": 7,
    "NetworkAdmin": 14,
    "DataEngineer": 14,
}

PERMISSION_SETS = list(DURATION_CAPS.keys())

# ---------------------------------------------------------------------------
# AWS / Slack clients
# ---------------------------------------------------------------------------
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
ses_client = boto3.client("ses", region_name=AWS_REGION)
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# ---------------------------------------------------------------------------
# FastAPI + rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# Account list (cached, sourced from AWS Organizations)
# ---------------------------------------------------------------------------
_account_cache: dict = {"accounts": [], "fetched_at": 0.0}


def get_aws_accounts() -> list[dict]:
    """Return active AWS accounts from AWS Organizations, cached for 5 minutes."""
    now = time.time()
    if now - _account_cache["fetched_at"] < 300:
        return _account_cache["accounts"]

    try:
        org = boto3.client("organizations", region_name=AWS_REGION)
        paginator = org.get_paginator("list_accounts")
        accounts = []
        for page in paginator.paginate():
            for acct in page["Accounts"]:
                if acct["Status"] == "ACTIVE":
                    accounts.append({"id": acct["Id"], "name": acct["Name"]})
        _account_cache["accounts"] = accounts
        _account_cache["fetched_at"] = now
        return accounts
    except Exception as exc:
        print(f"[WARN] Could not list AWS accounts: {exc}")
        return _account_cache["accounts"]  # return stale rather than crash


# ---------------------------------------------------------------------------
# Slack signature verification
# ---------------------------------------------------------------------------
def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify the X-Slack-Signature HMAC-SHA256 header."""
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False  # Replay attack guard
        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        computed = "v0=" + hmac.new(
            SLACK_SIGNING_SECRET.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# DynamoDB helpers (PendingRequests table)
# ---------------------------------------------------------------------------
def write_pending_request(request_id: str, data: dict) -> None:
    table = dynamodb.Table(PENDING_REQUESTS_TABLE)
    ttl = int(time.time()) + 86400  # 24-hour auto-expiry
    table.put_item(Item={"request_id": request_id, "ttl": ttl, **data})


def get_pending_request(request_id: str) -> dict | None:
    table = dynamodb.Table(PENDING_REQUESTS_TABLE)
    resp = table.get_item(Key={"request_id": request_id})
    return resp.get("Item")


def update_pending_request_slack_ts(request_id: str, ts: str, channel_id: str) -> None:
    table = dynamodb.Table(PENDING_REQUESTS_TABLE)
    table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET slack_message_ts = :ts, slack_channel_id = :ch",
        ExpressionAttributeValues={":ts": ts, ":ch": channel_id},
    )


def delete_pending_request(request_id: str) -> None:
    table = dynamodb.Table(PENDING_REQUESTS_TABLE)
    table.delete_item(Key={"request_id": request_id})


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------
def post_slack_approval_card(request_id: str, data: dict) -> str:
    """Post a Block Kit approval card to the ops channel. Returns message ts."""
    expiry = datetime.now(timezone.utc) + timedelta(days=int(data["duration_days"]))
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":key: AWS Access Request"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Requester:*\n{data['requester_name']}"},
                {"type": "mrkdwn", "text": f"*Email:*\n{data['requester_email']}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Account:*\n{data['aws_account_name']} (`{data['aws_account_id']}`)",
                },
                {"type": "mrkdwn", "text": f"*Permission Set:*\n{data['permission_set']}"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{data['duration_days']} day(s)"},
                {
                    "type": "mrkdwn",
                    "text": f"*Expires:*\n{expiry.strftime('%Y-%m-%d %H:%M UTC')}",
                },
            ],
        },
    ]

    if data.get("justification"):
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Justification:*\n{data['justification']}",
                },
            }
        )

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "approve_request",
                    "value": request_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Confirm approval"},
                        "text": {
                            "type": "plain_text",
                            "text": f"Grant {data['permission_set']} on account {data['aws_account_id']} for {data['duration_days']} day(s)?",
                        },
                        "confirm": {"type": "plain_text", "text": "Yes, approve"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "action_id": "deny_request",
                    "value": request_id,
                },
            ],
        }
    )

    resp = slack_client.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        blocks=blocks,
        text=f"AWS Access Request from {data['requester_name']} ({data['requester_email']})",
    )
    return resp["ts"]


def update_slack_message_decided(
    channel_id: str, message_ts: str, decision: str, approver: str, data: dict
) -> None:
    """Replace the Approve/Deny buttons with the final decision."""
    emoji = ":white_check_mark:" if decision == "approved" else ":x:"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":key: AWS Access Request"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Requester:*\n{data['requester_name']}"},
                {"type": "mrkdwn", "text": f"*Email:*\n{data['requester_email']}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Account:*\n{data['aws_account_name']} (`{data['aws_account_id']}`)",
                },
                {"type": "mrkdwn", "text": f"*Permission Set:*\n{data['permission_set']}"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{data['duration_days']} day(s)"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *{decision.capitalize()}* by @{approver}",
            },
        },
    ]
    slack_client.chat_update(
        channel=channel_id,
        ts=message_ts,
        blocks=blocks,
        text=f"Request {decision} by {approver}",
    )


# ---------------------------------------------------------------------------
# SES email helper
# ---------------------------------------------------------------------------
def send_email(to: str, subject: str, body: str) -> None:
    try:
        ses_client.send_email(
            Source=SES_FROM_EMAIL,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
    except Exception as exc:
        print(f"[WARN] SES send failed to {to}: {exc}")  # Non-fatal


# ---------------------------------------------------------------------------
# Provisioning trigger (stub — wired up after Step Functions deploy)
# ---------------------------------------------------------------------------
def trigger_aws_provisioning(data: dict) -> None:
    """
    POST the approved request payload to API Gateway → Step Functions.
    TODO: uncomment once infra/stacks/api_stack.py is deployed and AWS_API_URL is set.
    """
    print(f"[STUB] trigger_aws_provisioning called for request: {data.get('request_id')}")
    # import urllib.request as _urllib
    # payload = json.dumps(data).encode()
    # req = _urllib.Request(AWS_API_URL, payload, {"Content-Type": "application/json"}, method="POST")
    # _urllib.urlopen(req, timeout=10)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    accounts = get_aws_accounts()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "accounts": accounts,
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

    # Resolve account name from cached list
    accounts = get_aws_accounts()
    account_name = next(
        (a["name"] for a in accounts if a["id"] == aws_account_id), aws_account_id
    )

    # Generate a deterministic-ish short request ID
    raw = f"{requester_email}:{aws_account_id}:{time.time()}"
    request_id = hashlib.sha256(raw.encode()).hexdigest()[:12]

    data = {
        "request_id": request_id,
        "requester_name": requester_name,
        "requester_email": requester_email,
        "aws_account_id": aws_account_id,
        "aws_account_name": account_name,
        "permission_set": permission_set,
        "duration_days": duration_days,
        "justification": justification,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    write_pending_request(request_id, data)

    try:
        ts = post_slack_approval_card(request_id, data)
        update_pending_request_slack_ts(request_id, ts, SLACK_CHANNEL_ID)
    except SlackApiError as exc:
        print(f"[ERROR] Slack post failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to post approval request to Slack.")

    return RedirectResponse(
        url=f"/success?name={requester_name}&account={account_name}&days={duration_days}",
        status_code=303,
    )


@app.get("/success", response_class=HTMLResponse)
async def success(request: Request, name: str = "", account: str = "", days: int = 0):
    return templates.TemplateResponse(
        "success.html",
        {"request": request, "name": name, "account": account, "days": days},
    )


@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    body = await request.body()

    # Verify Slack request authenticity
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if SLACK_SIGNING_SECRET and not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature.")

    form_data = parse_qs(body.decode("utf-8"))
    payload = json.loads(form_data.get("payload", ["{}"])[0])

    if payload.get("type") != "block_actions":
        return JSONResponse({"ok": True})

    action = payload["actions"][0]
    action_id = action["action_id"]
    request_id = action["value"]
    approver = payload["user"]["name"]
    channel_id = payload["container"]["channel_id"]
    message_ts = payload["container"]["message_ts"]

    pending = get_pending_request(request_id)
    if not pending:
        # Already actioned or TTL-expired — silently ack so Slack doesn't retry
        return JSONResponse({"ok": True})

    if action_id == "approve_request":
        trigger_aws_provisioning(pending)
        update_slack_message_decided(channel_id, message_ts, "approved", approver, pending)
        delete_pending_request(request_id)
        send_email(
            pending["requester_email"],
            "AWS Access Request Approved",
            (
                f"Hi {pending['requester_name']},\n\n"
                f"Your request for {pending['permission_set']} access to "
                f"{pending['aws_account_name']} ({pending['aws_account_id']}) "
                f"for {pending['duration_days']} day(s) has been approved by {approver}.\n\n"
                f"Access will be provisioned shortly. It will expire automatically — no action needed.\n\n"
                f"— AWS Access Vending Machine\n{SES_FROM_EMAIL}"
            ),
        )

    elif action_id == "deny_request":
        update_slack_message_decided(channel_id, message_ts, "denied", approver, pending)
        delete_pending_request(request_id)
        send_email(
            pending["requester_email"],
            "AWS Access Request Denied",
            (
                f"Hi {pending['requester_name']},\n\n"
                f"Your request for {pending['permission_set']} access to "
                f"{pending['aws_account_name']} ({pending['aws_account_id']}) "
                f"has been denied by {approver}.\n\n"
                f"If you have questions, please reach out to the ops team.\n\n"
                f"— AWS Access Vending Machine\n{SES_FROM_EMAIL}"
            ),
        )

    return JSONResponse({"ok": True})
