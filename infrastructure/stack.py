"""
AWS CDK Stack for ECS Fargate deployment with ALB, ElastiCache Redis, and LLM ALB.

This stack creates:
- Two Application Load Balancers (ws_server, LLM) in public subnets
- ElastiCache Redis in private subnets (accessible only by ws_server tasks)
- Two ECS Fargate services on the same cluster: ws_server (port 8000) and LLM (port 7980), each with its own task definition
- ALB endpoints route to the correct service; Redis connects only to ws_server
- Auto-scaling and security groups per service
"""

import os

from aws_cdk import (
    Duration,
    Stack,
    Tags,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticache as elasticache,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct
from dotenv import load_dotenv

# LLM service port (same ECS task, second port)
LLM_CONTAINER_PORT = 7980
REDIS_PORT = 6379

# Keys provided by Secrets Manager (not from .env / task environment)
WS_SECRET_KEYS = {"DJANGO_SECRET_KEY", "AUTH_API_KEY", "LLM_SERVICE_AUTH"}
LLM_SECRET_KEYS = {
    "PSQL_BOT_USERNAME",
    "PSQL_BOT_PASSWORD",
    "PSQL_HOST",
    "PSQL_PORT",
    "PSQL_STATE_DATABASE",
    "PSQL_SSLMODE",
    "AWS_BEDROCK_REGION",
    "BEDROCK_MODEL_ID_BILLING_AGENT",
    "BEDROCK_MODEL_ID_CLAIM_AGENT",
    "BEDROCK_MODEL_ID_ESCALATION_DETECTION",
    "BEDROCK_MODEL_ID_INTENT_DETECTION",
    "BEDROCK_MODEL_ID_SMS_ROUTER",
    "BEDROCK_MODEL_ID_THREAD_SUMMARIZE",
    "BEDROCK_MODEL_ID_SMS_RESPOND",
    "BEDROCK_MODEL_ID_WEB_RESPOND",
    "AUTH_API_KEY",
    "MAXIMUM_GUARDRAIL_REWRITES",
}


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
        ecr_llm_repo_name = os.getenv("ECR_LLM_REPOSITORY_NAME") or ecr_repo_name
        # Optional: Skip ECS service creation for initial ALB-only deployment
        deploy_ecs_service = os.getenv("DEPLOY_ECS_SERVICE", "true").lower() == "true"
        container_port = int(os.getenv("CONTAINER_PORT", "8000"))
        desired_count = int(os.getenv("DESIRED_TASK_COUNT", "1"))
        min_capacity = int(os.getenv("MIN_TASK_COUNT", "1"))
        max_capacity = int(os.getenv("MAX_TASK_COUNT", "4"))
        desired_llm_count = int(os.getenv("DESIRED_LLM_TASK_COUNT", str(desired_count)))
        min_llm_capacity = int(os.getenv("MIN_LLM_TASK_COUNT", str(min_capacity)))
        max_llm_capacity = int(os.getenv("MAX_LLM_TASK_COUNT", str(max_capacity)))
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

        # --- Security group for ALB (inbound from internet, outbound to ECS) ---
        alb_sg = ec2.SecurityGroup(
            self,
            "ALBSecurityGroup",
            vpc=vpc,
            description="Security group for Application Load Balancer",
            allow_all_outbound=True,
        )
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

        # --- Security groups: separate for ws_server and LLM so Redis can allow only ws_server ---
        if ecs_sg_id:
            print(f"Using existing ECS Security Group for ws_server: {ecs_sg_id}")
            ws_sg = ec2.SecurityGroup.from_security_group_id(
                self,
                "WsECSSecurityGroup",
                security_group_id=ecs_sg_id,
                mutable=True,
            )
            ws_sg.add_ingress_rule(
                alb_sg,
                ec2.Port.tcp(container_port),
                "Allow traffic from ALB to ws_server",
            )
            # LLM uses its own SG so Redis can be restricted to ws_sg only
            llm_sg = ec2.SecurityGroup(
                self,
                "LLMECSSecurityGroup",
                vpc=vpc,
                description="Security group for ECS LLM tasks",
                allow_all_outbound=True,
            )
            llm_sg.add_ingress_rule(
                alb_sg,
                ec2.Port.tcp(LLM_CONTAINER_PORT),
                "Allow traffic from ALB to LLM (port 7980)",
            )
        else:
            print("ECS_SECURITY_GROUP_ID not set - creating separate SGs for ws_server and LLM")
            ws_sg = ec2.SecurityGroup(
                self,
                "WsECSSecurityGroup",
                vpc=vpc,
                description="Security group for ECS ws_server tasks",
                allow_all_outbound=True,
            )
            ws_sg.add_ingress_rule(
                alb_sg,
                ec2.Port.tcp(container_port),
                "Allow traffic from ALB to ws_server (port 8000)",
            )
            llm_sg = ec2.SecurityGroup(
                self,
                "LLMECSSecurityGroup",
                vpc=vpc,
                description="Security group for ECS LLM tasks",
                allow_all_outbound=True,
            )
            llm_sg.add_ingress_rule(
                alb_sg,
                ec2.Port.tcp(LLM_CONTAINER_PORT),
                "Allow traffic from ALB to LLM (port 7980)",
            )

        # RDS: allow both ws_server and LLM services (both may need DB)
        if rds_sg_id:
            print(f"Configuring RDS connectivity...")
            rds_sg = ec2.SecurityGroup.from_security_group_id(
                self,
                "RDSSecurityGroup",
                security_group_id=rds_sg_id,
                mutable=True,
            )
            rds_sg.add_ingress_rule(
                ws_sg,
                ec2.Port.tcp(rds_port),
                "Allow traffic from ws_server ECS tasks",
            )
            rds_sg.add_ingress_rule(
                llm_sg,
                ec2.Port.tcp(rds_port),
                "Allow traffic from LLM ECS tasks",
            )
            print(f"✅ Configured RDS security group for ws_server and LLM")
        else:
            print("⚠️  RDS_SECURITY_GROUP_ID not set - skipping RDS configuration")

        # ElastiCache Redis: inbound from ws_server tasks only (LLM does not connect to Redis)
        redis_node_type = os.getenv("REDIS_CACHE_NODE_TYPE", "cache.t3.micro")
        redis_subnet_group = elasticache.CfnSubnetGroup(
            self,
            "RedisSubnetGroup",
            description=f"Subnet group for Redis ({environment})",
            subnet_ids=private_subnet_ids,
            cache_subnet_group_name=f"websocket-pba-redis-{environment}",
        )
        redis_sg = ec2.SecurityGroup(
            self,
            "RedisSecurityGroup",
            vpc=vpc,
            description="Security group for ElastiCache Redis",
            allow_all_outbound=True,
        )
        redis_sg.add_ingress_rule(
            ws_sg,
            ec2.Port.tcp(REDIS_PORT),
            "Allow Redis from ws_server ECS tasks only",
        )
        redis_cluster = elasticache.CfnReplicationGroup(
            self,
            "RedisCluster",
            replication_group_description=f"Redis for websocket-pba ({environment})",
            cache_node_type=redis_node_type,
            engine="redis",
            num_node_groups=1,
            replicas_per_node_group=0,
            cache_subnet_group_name=redis_subnet_group.cache_subnet_group_name,
            security_group_ids=[redis_sg.security_group_id],
            automatic_failover_enabled=False,
        )
        redis_cluster.add_dependency(redis_subnet_group)

        # Create Application Load Balancer (ws_server)
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

        # Target group for ws_server: ALB forwards to task IP:container_port (8000)
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup",
            port=container_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            vpc=vpc,
            target_type=elbv2.TargetType.IP,
        )
        target_group.configure_health_check(
            path="/health/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )
        listener = alb.add_listener(
            "Listener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[target_group],
        )

        # Second ALB for LLM: listener 80 -> target group port 7980 (LLM container)
        llm_alb = elbv2.ApplicationLoadBalancer(
            self,
            "LLMALB",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnets=public_subnets),
            idle_timeout=Duration.seconds(60),
        )
        llm_target_group = elbv2.ApplicationTargetGroup(
            self,
            "LLMTargetGroup",
            port=LLM_CONTAINER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            vpc=vpc,
            target_type=elbv2.TargetType.IP,
        )
        llm_target_group.configure_health_check(
            path="/health/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )
        llm_alb.add_listener(
            "LLMListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[llm_target_group],
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

            # Reference existing ECR repositories (ws_server and LLM)
            ecr_repository = ecr.Repository.from_repository_name(
                self,
                "ECRRepository",
                repository_name=ecr_repo_name,
            )
            ecr_llm_repository = ecr.Repository.from_repository_name(
                self,
                "ECRLLMRepository",
                repository_name=ecr_llm_repo_name,
            )

            # Get image URIs from environment (set by deploy.sh)
            ecr_image_uri = os.getenv("ECR_IMAGE_URI")
            ecr_llm_image_uri = os.getenv("ECR_LLM_IMAGE_URI")
            if not ecr_image_uri:
                raise ValueError(
                    "ECR_IMAGE_URI must be set when deploying ECS service. Run the deploy.sh script to build and push the images first."
                )
            if not ecr_llm_image_uri:
                raise ValueError(
                    "ECR_LLM_IMAGE_URI must be set when deploying ECS service. Run the deploy.sh script to build and push the images first."
                )

            # Shared task execution role (both services pull from ECR)
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
            ecr_repository.grant_pull(task_execution_role)
            ecr_llm_repository.grant_pull(task_execution_role)

            log_group = logs.LogGroup(
                self,
                "LogGroup",
                log_group_name=f"/ecs/websocket-pba-{environment}",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=kwargs.get("removal_policy"),
            )

            # Reference existing secrets (create in AWS Console before deploy)
            ws_secret = secretsmanager.Secret.from_secret_name_v2(
                self,
                "WsServerSecret",
                secret_name=f"pba-{environment}/ws-server-secrets",
            )
            llm_secret = secretsmanager.Secret.from_secret_name_v2(
                self,
                "LLMSecret",
                secret_name=f"pba-{environment}/llm-server-secrets",
            )
            ws_secret.grant_read(task_execution_role)
            llm_secret.grant_read(task_execution_role)

            redis_primary = redis_cluster.attr_primary_end_point_address
            redis_port_attr = redis_cluster.attr_primary_end_point_port
            ws_alb_dns = alb.load_balancer_dns_name
            llm_alb_dns = llm_alb.load_balancer_dns_name
            env_overrides = {
                "REDIS_URL": f"redis://{redis_primary}:{redis_port_attr}/0",
                "WS_SERVER_URL": f"ws://{ws_alb_dns}",
                "WS_SERVER_ORIGIN": f"http://{ws_alb_dns}",
                "LLM_SERVICE_URL": f"http://{llm_alb_dns}",
            }

            # Load env from .env; exclude keys provided by Secrets Manager
            full_env = self._load_task_environment_variables(
                env_overrides,
                exclude_keys=WS_SECRET_KEYS | LLM_SECRET_KEYS,
            )
            ws_env = dict(full_env)
            # LLM env: no REDIS_URL; APPDATA_FOLDER_PATH for standalone LLM image
            llm_env = {k: v for k, v in full_env.items() if k != "REDIS_URL"}
            llm_env["APPDATA_FOLDER_PATH"] = "/app/appdata"

            ws_secrets = {
                "DJANGO_SECRET_KEY": ecs.Secret.from_secrets_manager(ws_secret, field="DJANGO_SECRET_KEY"),
                "AUTH_API_KEY": ecs.Secret.from_secrets_manager(ws_secret, field="AUTH_API_KEY"),
                "LLM_SERVICE_AUTH": ecs.Secret.from_secrets_manager(ws_secret, field="LLM_SERVICE_AUTH"),
            }
            llm_secrets = {
                "PSQL_BOT_USERNAME": ecs.Secret.from_secrets_manager(llm_secret, field="PSQL_BOT_USERNAME"),
                "PSQL_BOT_PASSWORD": ecs.Secret.from_secrets_manager(llm_secret, field="PSQL_BOT_PASSWORD"),
                "PSQL_HOST": ecs.Secret.from_secrets_manager(llm_secret, field="PSQL_HOST"),
                "PSQL_PORT": ecs.Secret.from_secrets_manager(llm_secret, field="PSQL_PORT"),
                "PSQL_STATE_DATABASE": ecs.Secret.from_secrets_manager(llm_secret, field="PSQL_STATE_DATABASE"),
                "PSQL_SSLMODE": ecs.Secret.from_secrets_manager(llm_secret, field="PSQL_SSLMODE"),
                "AWS_BEDROCK_REGION": ecs.Secret.from_secrets_manager(llm_secret, field="AWS_BEDROCK_REGION"),
                "BEDROCK_MODEL_ID_BILLING_AGENT": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_BILLING_AGENT"),
                "BEDROCK_MODEL_ID_CLAIM_AGENT": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_CLAIM_AGENT"),
                "BEDROCK_MODEL_ID_ESCALATION_DETECTION": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_ESCALATION_DETECTION"),
                "BEDROCK_MODEL_ID_INTENT_DETECTION": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_INTENT_DETECTION"),
                "BEDROCK_MODEL_ID_SMS_ROUTER": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_SMS_ROUTER"),
                "BEDROCK_MODEL_ID_THREAD_SUMMARIZE": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_THREAD_SUMMARIZE"),
                "BEDROCK_MODEL_ID_SMS_RESPOND": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_SMS_RESPOND"),
                "BEDROCK_MODEL_ID_WEB_RESPOND": ecs.Secret.from_secrets_manager(llm_secret, field="BEDROCK_MODEL_ID_WEB_RESPOND"),
                "AUTH_API_KEY": ecs.Secret.from_secrets_manager(llm_secret, field="AUTH_API_KEY"),
                "MAXIMUM_GUARDRAIL_REWRITES": ecs.Secret.from_secrets_manager(llm_secret, field="MAXIMUM_GUARDRAIL_REWRITES"),
            }

            runtime_platform = ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            )

            # --- Task definition: ws_server only (Redis, Django, port 8000) ---
            ws_task_definition = ecs.FargateTaskDefinition(
                self,
                "WsTaskDefinition",
                memory_limit_mib=2048,
                cpu=1024,
                execution_role=task_execution_role,
                runtime_platform=runtime_platform,
            )
            ws_container = ws_task_definition.add_container(
                "WebSocketContainer",
                image=ecs.ContainerImage.from_registry(ecr_image_uri),
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix="websocket-pba-ws",
                    log_group=log_group,
                ),
                environment=ws_env,
                secrets=ws_secrets,
            )
            ws_container.add_port_mappings(
                ecs.PortMapping(container_port=container_port, protocol=ecs.Protocol.TCP)
            )

            # --- Task definition: LLM only (no Redis; port 7980) ---
            llm_task_definition = ecs.FargateTaskDefinition(
                self,
                "LLMTaskDefinition",
                memory_limit_mib=2048,
                cpu=1024,
                execution_role=task_execution_role,
                runtime_platform=runtime_platform,
            )
            llm_container = llm_task_definition.add_container(
                "LLMContainer",
                image=ecs.ContainerImage.from_registry(ecr_llm_image_uri),
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix="websocket-pba-llm",
                    log_group=log_group,
                ),
                environment=llm_env,
                secrets=llm_secrets,
            )
            llm_container.add_port_mappings(
                ecs.PortMapping(
                    container_port=LLM_CONTAINER_PORT,
                    protocol=ecs.Protocol.TCP,
                )
            )

            # --- Service: ws_server — ALB (ws) -> target_group (8000) -> this service ---
            ws_service = ecs.FargateService(
                self,
                "WsService",
                cluster=cluster,
                task_definition=ws_task_definition,
                desired_count=desired_count,
                security_groups=[ws_sg],
                vpc_subnets=ec2.SubnetSelection(subnets=private_subnets),
                assign_public_ip=False,
                enable_execute_command=False,
            )
            target_group.add_target(
                ws_service.load_balancer_target(
                    container_name="WebSocketContainer",
                    container_port=container_port,
                )
            )
            ws_scaling = ws_service.auto_scale_task_count(
                min_capacity=min_capacity,
                max_capacity=max_capacity,
            )
            ws_scaling.scale_on_cpu_utilization(
                "CpuScaling",
                target_utilization_percent=70,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )
            ws_scaling.scale_on_memory_utilization(
                "MemoryScaling",
                target_utilization_percent=80,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )

            # --- Service: LLM — ALB (LLM) -> llm_target_group (7980) -> this service ---
            llm_service = ecs.FargateService(
                self,
                "LLMService",
                cluster=cluster,
                task_definition=llm_task_definition,
                desired_count=desired_llm_count,
                security_groups=[llm_sg],
                vpc_subnets=ec2.SubnetSelection(subnets=private_subnets),
                assign_public_ip=False,
                enable_execute_command=False,
            )
            llm_target_group.add_target(
                llm_service.load_balancer_target(
                    container_name="LLMContainer",
                    container_port=LLM_CONTAINER_PORT,
                )
            )
            llm_scaling = llm_service.auto_scale_task_count(
                min_capacity=min_llm_capacity,
                max_capacity=max_llm_capacity,
            )
            llm_scaling.scale_on_cpu_utilization(
                "CpuScaling",
                target_utilization_percent=70,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )
            llm_scaling.scale_on_memory_utilization(
                "MemoryScaling",
                target_utilization_percent=80,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )

        # Output ws_server ALB DNS name
        self.add_output(
            "LoadBalancerDNS",
            value=alb.load_balancer_dns_name,
            description="DNS name of the ws_server Application Load Balancer",
        )

        # Output ALB ARN
        self.add_output(
            "LoadBalancerARN",
            value=alb.load_balancer_arn,
            description="ARN of the ws_server Application Load Balancer",
        )

        # Output LLM ALB DNS name
        self.add_output(
            "LLMAlbDnsName",
            value=llm_alb.load_balancer_dns_name,
            description="DNS name of the LLM Application Load Balancer",
        )

        # Output Redis primary endpoint (host:port) for REDIS_URL
        redis_connection_url = f"redis://{redis_cluster.attr_primary_end_point_address}:{redis_cluster.attr_primary_end_point_port}/0"
        self.add_output(
            "RedisPrimaryEndpoint",
            value=redis_cluster.attr_primary_end_point_address,
            description="Redis primary endpoint hostname",
        )
        self.add_output(
            "RedisConnectionUrl",
            value=redis_connection_url,
            description="Redis connection URL (redis://host:port/0)",
        )

    def _load_task_environment_variables(
        self,
        overrides: dict | None = None,
        exclude_keys: set[str] | None = None,
    ) -> dict:
        """
        Load environment variables from .env file for ECS task definition.

        Excludes CDK-specific variables and optionally keys provided by Secrets Manager.
        When overrides is provided (e.g. REDIS_URL, WS_SERVER_URL, LLM_SERVICE_URL from
        stack constructs), those values take precedence over .env.
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
            "ECR_LLM_REPOSITORY_NAME",
            "ECR_IMAGE_URI",
            "ECR_LLM_IMAGE_URI",
            "ECR_IMAGE_TAG",
            "STICKY_SESSION_DURATION",
            "CONTAINER_PORT",
            "DESIRED_TASK_COUNT",
            "MIN_TASK_COUNT",
            "MAX_TASK_COUNT",
            "DESIRED_LLM_TASK_COUNT",
            "MIN_LLM_TASK_COUNT",
            "MAX_LLM_TASK_COUNT",
            "AWS_REGION",
            "AWS_ACCOUNT_ID",
            "DEPLOY_ECS_SERVICE",
            "LLM_SERVICE_URL_ECS",
            "REDIS_CACHE_NODE_TYPE",
        }

        excluded = exclude_keys or set()

        # Load all environment variables except CDK-specific and secret keys
        task_env = {}
        for key, value in os.environ.items():
            if key not in cdk_vars and key not in excluded and value:
                task_env[key] = value

        # Stack-injected overrides (Redis, ALB endpoints) take precedence
        if overrides:
            for key, value in overrides.items():
                if value is not None and key not in excluded:
                    task_env[key] = str(value)

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
