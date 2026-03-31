#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.apprunner_stack import AppRunnerStack
from stacks.dynamodb_stack import DynamoDBStack
from stacks.pipeline_stack import PipelineStack
from stacks.secrets_stack import SecretsStack

app = cdk.App()

# All resources in us-east-1 (only approved operational region)
env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region="us-east-1",
)

# GitHub repo details — set in cdk.json context or override with:
#   cdk deploy --context github_owner=armakuni --context github_repo=aws-access-vending
github_owner = app.node.try_get_context("github_owner") or "armakuni"
github_repo = app.node.try_get_context("github_repo") or "ak-aws-access-vendor"
github_branch = app.node.try_get_context("github_branch") or "main"

# 1. Secrets (Slack tokens — populate values manually after first deploy)
secrets = SecretsStack(app, "aws-access-vending-secrets", env=env)

# 2. DynamoDB tables
dynamodb = DynamoDBStack(app, "aws-access-vending-dynamodb", env=env)

# 3. CodePipeline: creates ECR repo + builds/pushes Docker image on every push
pipeline = PipelineStack(
    app,
    "aws-access-vending-pipeline",
    github_owner=github_owner,
    github_repo=github_repo,
    github_branch=github_branch,
    env=env,
)

# 4. App Runner: deploy AFTER pipeline has pushed the first image to ECR
apprunner = AppRunnerStack(
    app,
    "aws-access-vending-apprunner",
    secrets_stack=secrets,
    dynamodb_stack=dynamodb,
    pipeline_stack=pipeline,
    env=env,
)
apprunner.add_dependency(secrets)
apprunner.add_dependency(dynamodb)
apprunner.add_dependency(pipeline)

# Apply tags to every resource in every stack
cdk.Tags.of(app).add("Project", "internal-ak")
cdk.Tags.of(app).add("CreatedBy", "ridhiman@armakuni.com")
cdk.Tags.of(app).add("Environment", "dev")

app.synth()
