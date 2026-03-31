from aws_cdk import Stack
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as actions
from aws_cdk import aws_codestarconnections as codestarconnections
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class PipelineStack(Stack):
    """ECR repository + CodePipeline: GitHub → CodeBuild (docker build + ECR push).

    ECR lives here because the pipeline owns the image build process.
    AppRunner references the ECR repo by name — no cross-stack dependency.

    One-time manual step after deploying this stack:
      1. Go to AWS Console → Developer Tools → Connections
      2. Find the "aws-access-vending-github" connection and click "Update pending connection"
      3. Authorise the GitHub App for your org
    Without this the pipeline Source stage will stay in Pending state.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_owner: str,
        github_repo: str,
        github_branch: str = "main",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------------------------------------------
        # ECR repository (owned by pipeline — it's the one pushing images)
        # -------------------------------------------------------------------
        self.ecr_repo = ecr.Repository(
            self,
            "aws-access-vending-ecr",
            repository_name="aws-access-vending-app",
        )

        # -------------------------------------------------------------------
        # GitHub connection (CodeStar)
        # -------------------------------------------------------------------
        github_connection = codestarconnections.CfnConnection(
            self,
            "aws-access-vending-github-connection",
            connection_name="aws-access-vending-github",
            provider_type="GitHub",
        )
        connection_arn = github_connection.attr_connection_arn

        # -------------------------------------------------------------------
        # CodeBuild project — builds the Docker image and pushes to ECR
        # -------------------------------------------------------------------
        build_project = codebuild.PipelineProject(
            self,
            "aws-access-vending-codebuild",
            project_name="aws-access-vending-docker-build",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,  # Required for docker build
            ),
            environment_variables={
                "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                    value=self.ecr_repo.repository_uri
                ),
            },
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "aws ecr get-login-password --region $AWS_DEFAULT_REGION"
                                " | docker login --username AWS --password-stdin $ECR_REPO_URI",
                            ]
                        },
                        "build": {
                            "commands": [
                                "docker build -t $ECR_REPO_URI:latest .",
                                "docker tag $ECR_REPO_URI:latest $ECR_REPO_URI:dev",
                            ]
                        },
                        "post_build": {
                            "commands": [
                                "docker push $ECR_REPO_URI:latest",
                                "docker push $ECR_REPO_URI:dev",
                            ]
                        },
                    },
                }
            ),
        )

        self.ecr_repo.grant_pull_push(build_project)

        # -------------------------------------------------------------------
        # Pipeline
        # -------------------------------------------------------------------
        source_artifact = codepipeline.Artifact("source")

        codepipeline.Pipeline(
            self,
            "aws-access-vending-codepipeline",
            pipeline_name="aws-access-vending-pipeline",
            pipeline_type=codepipeline.PipelineType.V2,
            cross_account_keys=False,  # Use AWS managed key (SSE-S3), not CMK
            stages=[
                codepipeline.StageProps(
                    stage_name="source",
                    actions=[
                        actions.CodeStarConnectionsSourceAction(
                            action_name="github-source",
                            owner=github_owner,
                            repo=github_repo,
                            branch=github_branch,
                            connection_arn=connection_arn,
                            output=source_artifact,
                            trigger_on_push=True,
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="build",
                    actions=[
                        actions.CodeBuildAction(
                            action_name="docker-build-push",
                            project=build_project,
                            input=source_artifact,
                        )
                    ],
                ),
            ],
        )
