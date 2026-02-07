"""
AWS CDK Stack for ECS Fargate deployment with ALB and sticky sessions.

This stack creates:
- Application Load Balancer in public subnets
- ECS Fargate service in private subnets
- Auto-scaling configuration
- Security groups
- Sticky sessions for WebSocket support
"""

import os

from aws_cdk import (
    Duration,
    Stack,
    Tags,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_targets as targets,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct
from dotenv import load_dotenv


class WebSocketPbaStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Load environment variables from .env file
        env_file = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file)
        else:
            # Try loading from parent directory
            parent_env = os.path.join(os.path.dirname(__file__), "..", ".env")
            if os.path.exists(parent_env):
                load_dotenv(parent_env)

        # Get configuration from environment variables
        environment = os.getenv("ENVIRONMENT", "devlive")
        vpc_id = os.getenv("VPC_ID")
        public_subnet_ids = os.getenv("PUBLIC_SUBNET_IDS", "").split(",")
        private_subnet_ids = os.getenv("PRIVATE_SUBNET_IDS", "").split(",")
        public_route_table_ids = os.getenv("PUBLIC_ROUTE_TABLE_IDS", "").split(",")
        private_route_table_ids = os.getenv("PRIVATE_ROUTE_TABLE_IDS", "").split(",")
        availability_zones = os.getenv("AVAILABILITY_ZONES", "").split(",")
        ecr_repo_name = os.getenv("ECR_REPOSITORY_NAME")
        # Optional: Skip ECS service creation for initial ALB-only deployment
        deploy_ecs_service = os.getenv("DEPLOY_ECS_SERVICE", "true").lower() == "true"
        sticky_duration = int(os.getenv("STICKY_SESSION_DURATION", "86400"))
        container_port = int(os.getenv("CONTAINER_PORT", "8000"))
        desired_count = int(os.getenv("DESIRED_TASK_COUNT", "1"))
        min_capacity = int(os.getenv("MIN_TASK_COUNT", "1"))
        max_capacity = int(os.getenv("MAX_TASK_COUNT", "4"))
        # RDS configuration (optional)
        rds_sg_id = os.getenv("RDS_SECURITY_GROUP_ID")
        rds_port = int(os.getenv("RDS_PORT", "5432"))  # Default to PostgreSQL port
        # ECS security group configuration
        ecs_sg_id = os.getenv("ECS_SECURITY_GROUP_ID")  # Use existing SG if provided

        # Validate required parameters
        if not vpc_id:
            raise ValueError("VPC_ID must be set in .env file")
        if not public_subnet_ids or not public_subnet_ids[0]:
            raise ValueError("PUBLIC_SUBNET_IDS must be set in .env file")
        if not private_subnet_ids or not private_subnet_ids[0]:
            raise ValueError("PRIVATE_SUBNET_IDS must be set in .env file")
        if not public_route_table_ids or not public_route_table_ids[0]:
            raise ValueError("PUBLIC_ROUTE_TABLE_IDS must be set in .env file")
        if not private_route_table_ids or not private_route_table_ids[0]:
            raise ValueError("PRIVATE_ROUTE_TABLE_IDS must be set in .env file")
        if not ecr_repo_name:
            raise ValueError("ECR_REPOSITORY_NAME must be set in .env file")
        if not availability_zones or not availability_zones[0]:
            raise ValueError("AVAILABILITY_ZONES must be set in .env file")

        # Clean up subnet and route table IDs (remove empty strings)
        public_subnet_ids = [s.strip() for s in public_subnet_ids if s.strip()]
        private_subnet_ids = [s.strip() for s in private_subnet_ids if s.strip()]
        public_route_table_ids = [s.strip() for s in public_route_table_ids if s.strip()]
        private_route_table_ids = [s.strip() for s in private_route_table_ids if s.strip()]
        availability_zones = [s.strip() for s in availability_zones if s.strip()]

        # Validate environment is set
        if not environment:
            raise ValueError("ENVIRONMENT must be set in .env file or environment variables")

        # Tag all resources with environment
        Tags.of(self).add("Environment", environment)
        Tags.of(self).add("ManagedBy", "CDK")

        # Import existing VPC
        vpc = ec2.Vpc.from_lookup(
            self,
            "VPC",
            vpc_id=vpc_id,
        )

        # Import public subnets WITH route table IDs
        public_subnets = [
            ec2.Subnet.from_subnet_attributes(
                self,
                f"PublicSubnet{i}",
                subnet_id=subnet_id,
                availability_zone=availability_zones[i],
                route_table_id=public_route_table_ids[0],
            )
            for i, subnet_id in enumerate(public_subnet_ids)
        ]

        # Import private subnets WITH route table IDs
        private_subnets = [
            ec2.Subnet.from_subnet_attributes(
                self,
                f"PrivateSubnet{i}",
                subnet_id=subnet_id,
                availability_zone=availability_zones[i],
                route_table_id=private_route_table_ids[0],
            )
            for i, subnet_id in enumerate(private_subnet_ids)
        ]

        # Create security group for ALB
        alb_sg = ec2.SecurityGroup(
            self,
            "ALBSecurityGroup",
            vpc=vpc,
            description="Security group for Application Load Balancer",
            allow_all_outbound=True,
        )

        # Allow HTTP and HTTPS from internet
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP from internet",
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS from internet",
        )

        # Import or create security group for ECS tasks
        if ecs_sg_id:
            # Use existing ECS security group
            print(f"Using existing ECS Security Group: {ecs_sg_id}")
            ecs_sg = ec2.SecurityGroup.from_security_group_id(
                self,
                "ECSSecurityGroup",
                security_group_id=ecs_sg_id,
                mutable=True,  # Allow CDK to add rules to this security group
            )
            
            # Add ingress rule from ALB to ECS (if not already present)
            # Note: CDK will handle duplicates gracefully
            ecs_sg.add_ingress_rule(
                alb_sg,
                ec2.Port.tcp(container_port),
                "Allow traffic from ALB",
            )
        else:
            # Create new ECS security group if not provided
            print("ECS_SECURITY_GROUP_ID not set - creating new security group")
            ecs_sg = ec2.SecurityGroup(
                self,
                "ECSSecurityGroup",
                vpc=vpc,
                description="Security group for ECS Fargate tasks",
                allow_all_outbound=True,
            )

            # Allow traffic from ALB to ECS tasks
            ecs_sg.add_ingress_rule(
                alb_sg,
                ec2.Port.tcp(container_port),
                "Allow traffic from ALB",
            )

        # Configure RDS security group if provided
        if rds_sg_id:
            print(f"Configuring RDS connectivity...")
            print(f"  RDS Security Group: {rds_sg_id}")
            print(f"  RDS Port: {rds_port}")
            
            # Import existing RDS security group
            rds_sg = ec2.SecurityGroup.from_security_group_id(
                self,
                "RDSSecurityGroup",
                security_group_id=rds_sg_id,
                mutable=True,  # Allow CDK to modify this security group
            )
            
            # Allow ECS tasks to connect to RDS
            rds_sg.add_ingress_rule(
                ecs_sg,
                ec2.Port.tcp(rds_port),
                "Allow traffic from ECS tasks",
            )
            
            print(f"✅ Configured RDS security group to allow traffic from ECS")
        else:
            print("⚠️  RDS_SECURITY_GROUP_ID not set - skipping RDS configuration")
            print("   If you have an RDS instance, add RDS_SECURITY_GROUP_ID to .env")

        # Create Application Load Balancer
        # Set idle timeout to 3600 seconds (1 hour) for WebSocket support
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "ALB",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnets=public_subnets),
            idle_timeout=Duration.seconds(3600),  # 1 hour for WebSocket connections
        )

        # Create target group with sticky sessions
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup",
            port=container_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            vpc=vpc,
            target_type=elbv2.TargetType.IP,
        )

        # Configure health check for ALB target group
        # The health endpoint returns HTTP 200 with JSON response
        target_group.configure_health_check(
            path="/health/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # Enable sticky sessions for WebSocket support
        # Using load balancer-generated cookies (AWS will auto-generate the cookie name)
        target_group.enable_cookie_stickiness(
            duration=Duration.seconds(sticky_duration),
        )

        # Create listener
        listener = alb.add_listener(
            "Listener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[target_group],
        )

        # Only create ECS resources if deploy_ecs_service is True
        # This allows for a two-phase deployment: ALB first, then ECS with correct ALLOWED_HOSTS
        if deploy_ecs_service:
            # Create ECS cluster
            cluster = ecs.Cluster(
                self,
                "Cluster",
                vpc=vpc,
                cluster_name=f"websocket-pba-{environment}",
            )

            # Reference existing ECR repository
            ecr_repository = ecr.Repository.from_repository_name(
                self,
                "ECRRepository",
                repository_name=ecr_repo_name,
            )

            # Get image URI from environment variable (set by deploy.sh script)
            # Format: <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
            ecr_image_uri = os.getenv("ECR_IMAGE_URI")
            if not ecr_image_uri:
                raise ValueError(
                    "ECR_IMAGE_URI must be set when deploying ECS service. Run the deploy.sh script to build and push the image first."
                )

            # Parse image URI to extract repository and tag
            # ECR_IMAGE_URI format: <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
            if ":" in ecr_image_uri:
                image_repo_uri, image_tag = ecr_image_uri.rsplit(":", 1)
            else:
                image_repo_uri = ecr_image_uri
                image_tag = "latest"

            # Create task execution role with ECR permissions
            # This role is used by ECS to pull images from ECR and write logs to CloudWatch
            task_execution_role = iam.Role(
                self,
                "TaskExecutionRole",
                assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AmazonECSTaskExecutionRolePolicy"
                    )
                ],
            )

            # Grant ECR pull permissions to the task execution role
            ecr_repository.grant_pull(task_execution_role)

            # Create task definition
            # Using ARM64 architecture to match Mac Silicon (Apple Silicon) for native builds
            task_definition = ecs.FargateTaskDefinition(
                self,
                "TaskDefinition",
                memory_limit_mib=2048,
                cpu=1024,
                execution_role=task_execution_role,
                runtime_platform=ecs.RuntimePlatform(
                    cpu_architecture=ecs.CpuArchitecture.ARM64,  # ARM64 for Mac Silicon compatibility
                    operating_system_family=ecs.OperatingSystemFamily.LINUX,
                ),
            )

            # Create log group
            log_group = logs.LogGroup(
                self,
                "LogGroup",
                log_group_name=f"/ecs/websocket-pba-{environment}",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=kwargs.get("removal_policy"),
            )

            # Add container to task definition
            container = task_definition.add_container(
                "WebSocketContainer",
                image=ecs.ContainerImage.from_ecr_repository(
                    ecr_repository,
                    tag=image_tag,
                ),
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix="websocket-pba",
                    log_group=log_group,
                ),
                environment=self._load_task_environment_variables(),
            )

            # Add port mapping
            container.add_port_mappings(
                ecs.PortMapping(
                    container_port=container_port,
                    protocol=ecs.Protocol.TCP,
                )
            )

            # Create ECS service
            service = ecs.FargateService(
                self,
                "Service",
                cluster=cluster,
                task_definition=task_definition,
                desired_count=desired_count,
                security_groups=[ecs_sg],
                vpc_subnets=ec2.SubnetSelection(subnets=private_subnets),
                assign_public_ip=False,  # Tasks in private subnets
                enable_execute_command=False,
            )

            # Attach service to target group
            service.attach_to_application_target_group(target_group)

            # Configure auto-scaling
            scaling = service.auto_scale_task_count(
                min_capacity=min_capacity,
                max_capacity=max_capacity,
            )

            # Scale based on CPU utilization
            scaling.scale_on_cpu_utilization(
                "CpuScaling",
                target_utilization_percent=70,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )

            # Scale based on memory utilization
            scaling.scale_on_memory_utilization(
                "MemoryScaling",
                target_utilization_percent=80,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )

        # Output ALB DNS name
        self.add_output(
            "LoadBalancerDNS",
            value=alb.load_balancer_dns_name,
            description="DNS name of the Application Load Balancer",
        )

        # Output ALB ARN
        self.add_output(
            "LoadBalancerARN",
            value=alb.load_balancer_arn,
            description="ARN of the Application Load Balancer",
        )

    def _load_task_environment_variables(self) -> dict:
        """
        Load environment variables from .env file for ECS task definition.

        Excludes CDK-specific variables and includes Django app variables.
        """
        env_file = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file)
        else:
            parent_env = os.path.join(os.path.dirname(__file__), "..", ".env")
            if os.path.exists(parent_env):
                load_dotenv(parent_env)

        # CDK-specific variables to exclude from ECS task environment
        cdk_vars = {
            "ENVIRONMENT",
            "VPC_ID",
            "PUBLIC_SUBNET_IDS",
            "PRIVATE_SUBNET_IDS",
            "PUBLIC_ROUTE_TABLE_IDS",
            "PRIVATE_ROUTE_TABLE_IDS",
            "ECR_REPOSITORY_NAME",
            "ECR_IMAGE_TAG",
            "STICKY_SESSION_DURATION",
            "CONTAINER_PORT",
            "DESIRED_TASK_COUNT",
            "MIN_TASK_COUNT",
            "MAX_TASK_COUNT",
            "AWS_REGION",
            "AWS_ACCOUNT_ID",
            "DEPLOY_ECS_SERVICE",  # Add this
        }

        # Load all environment variables except CDK-specific ones
        task_env = {}
        for key, value in os.environ.items():
            if key not in cdk_vars and value:
                task_env[key] = value

        return task_env

    def add_output(self, id: str, value: str, description: str) -> None:
        """Helper method to add stack outputs."""
        from aws_cdk import CfnOutput

        CfnOutput(
            self,
            id,
            value=value,
            description=description,
        )
