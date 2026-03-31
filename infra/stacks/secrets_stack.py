from aws_cdk import Stack
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class SecretsStack(Stack):
    """Secrets Manager secrets (sensitive) and SSM parameters (non-sensitive).

    After deploying, populate each secret:
      aws secretsmanager put-secret-value \
        --secret-id ak-aws-access-vending/slack-bot-token \
        --secret-string "xoxb-..."

    SSM parameters are pre-populated with defaults at deploy time.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        _secret_prefix = "ak-aws-access-vending"
        _param_prefix = "/ak-aws-access-vending"

        def _secret(logical_id: str, name: str, description: str) -> secretsmanager.Secret:
            return secretsmanager.Secret(
                self,
                logical_id,
                secret_name=f"{_secret_prefix}/{name}",
                description=description,
            )

        def _param(logical_id: str, name: str, value: str, description: str) -> ssm.StringParameter:
            return ssm.StringParameter(
                self,
                logical_id,
                parameter_name=f"{_param_prefix}/{name}",
                string_value=value,
                description=description,
            )

        # -------------------------------------------------------------------
        # Secrets Manager — sensitive credentials (populate manually post-deploy)
        # -------------------------------------------------------------------
        self.slack_webhook_url = _secret(
            "slack-webhook-url",
            "slack-webhook-url",
            "Slack incoming webhook URL for posting approval cards to the channel",
        )
        self.slack_bot_token = _secret(
            "slack-bot-token", "slack-bot-token", "Slack bot OAuth token (xoxb-...) — needed for interactive button handling"
        )
        self.slack_signing_secret = _secret(
            "slack-signing-secret",
            "slack-signing-secret",
            "Slack signing secret for HMAC request verification",
        )
        self.slack_channel_id = _secret(
            "slack-channel-id",
            "slack-channel-id",
            "Slack channel ID where approval cards are posted",
        )
        self.aws_api_url = _secret(
            "aws-api-url",
            "aws-api-url",
            "API Gateway URL that triggers the Step Functions provisioning workflow",
        )
        self.sso_instance_arn = _secret(
            "sso-instance-arn",
            "sso-instance-arn",
            "IAM Identity Center instance ARN",
        )
        self.sns_topic_arn = _secret(
            "sns-topic-arn",
            "sns-topic-arn",
            "SNS topic ARN for grant confirmation and expiry notifications",
        )

        # -------------------------------------------------------------------
        # SSM Parameter Store — non-sensitive config (pre-populated at deploy)
        # -------------------------------------------------------------------
        self.pending_requests_table_param = _param(
            "pending-requests-table-param",
            "pending-requests-table",
            "ak-aws-access-vending-pending-requests",
            "DynamoDB table name for pending approval requests",
        )
        self.allowed_email_domain_param = _param(
            "allowed-email-domain-param",
            "allowed-email-domain",
            "armakuni.com",
            "Permitted email domain for access requests",
        )
        self.ses_from_email_param = _param(
            "ses-from-email-param",
            "ses-from-email",
            "noreply@armakuni.com",
            "SES sender address for approval/denial notifications",
        )
