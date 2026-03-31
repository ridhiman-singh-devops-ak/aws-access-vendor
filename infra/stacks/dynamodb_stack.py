from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class DynamoDBStack(Stack):
    """DynamoDB tables for the access vending machine.

    ak-aws-access-vending-access-records  — long-lived grant records; TTL drives cleanup via Streams.
    ak-aws-access-vending-pending-requests — short-lived Slack approval state.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------------------------------------------
        # Table 1: Access Records (grant ledger + cleanup trigger)
        # -------------------------------------------------------------------
        self.access_records_table = dynamodb.Table(
            self,
            "ak-aws-access-vending-access-records",
            table_name="ak-aws-access-vending-access-records",
            partition_key=dynamodb.Attribute(
                name="request_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            stream=dynamodb.StreamViewType.OLD_IMAGE,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        # -------------------------------------------------------------------
        # Table 2: Pending Requests (approval workflow state)
        # -------------------------------------------------------------------
        self.pending_requests_table = dynamodb.Table(
            self,
            "ak-aws-access-vending-pending-requests",
            table_name="ak-aws-access-vending-pending-requests",
            partition_key=dynamodb.Attribute(
                name="request_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
