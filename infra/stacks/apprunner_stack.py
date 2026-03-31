from aws_cdk import Stack
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

from stacks.dynamodb_stack import DynamoDBStack
from stacks.pipeline_stack import PipelineStack
from stacks.secrets_stack import SecretsStack


class AppRunnerStack(Stack):
    """App Runner service for the FastAPI frontend/backend.

    ECR repo lives in PipelineStack (pipeline owns image builds).
    AppRunner references it by the repository object passed in.
    Deploy order: pipeline first → activate GitHub connection → pipeline
    pushes first image → then deploy this stack.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        secrets_stack: SecretsStack,
        dynamodb_stack: DynamoDBStack,
        pipeline_stack: PipelineStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------------------------------------------
        # IAM role: App Runner → ECR (image pull)
        # -------------------------------------------------------------------
        ecr_access_role = iam.Role(
            self,
            "aws-access-vending-apprunner-ecr-role",
            role_name="aws-access-vending-apprunner-ecr-role",
            assumed_by=iam.ServicePrincipal("build.apprunner.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSAppRunnerServicePolicyForECRAccess"
                )
            ],
        )

        # -------------------------------------------------------------------
        # IAM role: App Runner instance (runtime permissions)
        # -------------------------------------------------------------------
        instance_role = iam.Role(
            self,
            "aws-access-vending-apprunner-instance-role",
            role_name="aws-access-vending-apprunner-instance-role",
            assumed_by=iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
        )

        # DynamoDB — PendingRequests table only (AccessRecords added later)
        instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                ],
                resources=[dynamodb_stack.pending_requests_table.table_arn],
            )
        )

        # Secrets Manager — read Slack credentials at runtime
        instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    secrets_stack.slack_webhook_url.secret_arn,
                    secrets_stack.slack_bot_token.secret_arn,
                    secrets_stack.slack_signing_secret.secret_arn,
                    secrets_stack.slack_channel_id.secret_arn,
                    secrets_stack.aws_api_url.secret_arn,
                ],
            )
        )

        # SSM Parameter Store — read non-sensitive config at runtime
        instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    secrets_stack.pending_requests_table_param.parameter_arn,
                    secrets_stack.allowed_email_domain_param.parameter_arn,
                    secrets_stack.ses_from_email_param.parameter_arn,
                ],
            )
        )

        # AWS Organizations — list accounts for the form dropdown
        instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=["organizations:ListAccounts"],
                resources=["*"],
            )
        )

        # SES — send approval/denial emails
        instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail"],
                resources=["*"],
            )
        )

        # -------------------------------------------------------------------
        # App Runner service
        # -------------------------------------------------------------------
        initial_image = f"{pipeline_stack.ecr_repo.repository_uri}:latest"

        self.service = apprunner.CfnService(
            self,
            "aws-access-vending-apprunner-service",
            service_name="aws-access-vending-app",
            source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                authentication_configuration=apprunner.CfnService.AuthenticationConfigurationProperty(
                    access_role_arn=ecr_access_role.role_arn,
                ),
                auto_deployments_enabled=True,
                image_repository=apprunner.CfnService.ImageRepositoryProperty(
                    image_identifier=initial_image,
                    image_repository_type="ECR",
                    image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                        port="8000",
                        # No env vars injected — app fetches all config from
                        # Secrets Manager and SSM Parameter Store at startup.
                    ),
                ),
            ),
            instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                instance_role_arn=instance_role.role_arn,
            ),
            health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                protocol="HTTP",
                path="/health",
                interval=10,
                timeout=5,
                healthy_threshold=1,
                unhealthy_threshold=3,
            ),
        )

        self.service.node.add_dependency(pipeline_stack.ecr_repo)
