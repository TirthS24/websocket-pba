# AWS CDK Infrastructure for WebSocket PBA Server

This directory contains AWS CDK (Cloud Development Kit) code to deploy the Django WebSocket server to AWS ECS Fargate behind an Application Load Balancer with sticky sessions.

## Architecture

- **Application Load Balancer (ALB)**: Deployed in public subnets with sticky sessions enabled for WebSocket support
- **ECS Fargate Service**: Deployed in private subnets with auto-scaling (1-4 tasks)
- **Security Groups**: Configured to allow traffic from ALB to ECS tasks only
- **Health Checks**: ALB health checks configured to use `/health/` endpoint
- **Sticky Sessions**: Enabled for WebSocket session persistence (configurable duration)

## Prerequisites

1. **AWS CLI** configured with appropriate credentials
2. **AWS CDK CLI** installed: `npm install -g aws-cdk`
3. **Python 3.12+** installed
4. **Existing AWS Resources**:
   - VPC with public and private subnets
   - ECR repository with Docker image pushed

## Setup

### 1. Install CDK Dependencies

```bash
cd infrastructure
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment file and fill in your values:

```bash
cp env.example .env
```

Edit `.env` and configure the following required variables:

#### Required CDK Configuration

```bash
# Environment name (stage, live, etc.)
ENVIRONMENT=stage

# AWS Configuration
AWS_ACCOUNT_ID=123456789012
AWS_REGION=us-east-1

# VPC Configuration (existing VPC)
VPC_ID=vpc-xxxxxxxxxxxxxxxxx
PUBLIC_SUBNET_IDS=subnet-xxxxxxxxxxxxxxxxx,subnet-yyyyyyyyyyyyyyyyy
PRIVATE_SUBNET_IDS=subnet-aaaaaaaaaaaaaaaaa,subnet-bbbbbbbbbbbbbbbbb

# ECR Repository (existing repository name)
ECR_REPOSITORY_NAME=your-ecr-repo-name

# ALB Sticky Session Configuration
STICKY_SESSION_DURATION=86400  # Duration in seconds (default: 1 day)

# Container Configuration
CONTAINER_PORT=8000
DESIRED_TASK_COUNT=1
MIN_TASK_COUNT=1
MAX_TASK_COUNT=4
```

#### Django Application Environment Variables

All other environment variables in `.env` (DJANGO_SECRET_KEY, PSQL_*, BEDROCK_*, etc.) will be automatically passed to ECS tasks. Make sure to include:

- `DJANGO_SECRET_KEY`: Required for production
- `DJANGO_ALLOWED_HOSTS`: Should include the ALB DNS name (you'll get this after first deployment)
- `INSTANCE_ID`: Set to `ecs-fargate` or similar
- All other Django/LangGraph configuration variables

### 3. Bootstrap CDK (First Time Only)

If this is your first time using CDK in this AWS account/region, bootstrap it:

```bash
cdk bootstrap
```

This creates the necessary S3 bucket and IAM roles for CDK deployments.

## Deployment

### Prerequisites

1. **Docker** must be installed and running
2. **AWS CLI** configured with appropriate credentials
3. **ECR Repository** must already exist (the script will not create it)

### Deploy Stack

Use the provided deployment script which handles the complete deployment process:

```bash
# Make script executable (first time only)
chmod +x deploy.sh

# Run deployment script
./deploy.sh
```

**What the script does:**
1. Builds the Docker image from `Dockerfile`
2. Logs in to Amazon ECR
3. Tags the image for ECR
4. Pushes the image to the existing ECR repository
5. Exports the image URI and runs `cdk deploy --all`

**Manual Deployment (if needed):**

If you prefer to deploy manually:

```bash
# 1. Build and push image manually
docker build -t <repo-name>:latest -f Dockerfile ..
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker tag <repo-name>:latest <account>.dkr.ecr.<region>.amazonaws.com/<repo-name>:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/<repo-name>:latest

# 2. Export image URI and deploy
export ECR_IMAGE_URI=<account>.dkr.ecr.<region>.amazonaws.com/<repo-name>:latest
cdk deploy --all
```

### Verify Deployment

After deployment, CDK will output the ALB DNS name. You can:

1. **Check Stack Outputs**:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name WebSocketPbaStack-stage \
     --query 'Stacks[0].Outputs'
   ```

2. **Test Health Endpoint**:
   ```bash
   curl http://<ALB-DNS-NAME>/health/
   ```

3. **Update ALLOWED_HOSTS**: After getting the ALB DNS name, update your `.env` file:
   ```bash
   DJANGO_ALLOWED_HOSTS=<ALB-DNS-NAME>.elb.amazonaws.com
   ```
   Then redeploy to update the ECS tasks with the new environment variable.

### Update Stack

To update the stack after making changes:

```bash
cdk deploy WebSocketPbaStack-stage
```

CDK will only update resources that have changed, ensuring idempotency.

## Stack Features

### Sticky Sessions

Sticky sessions are enabled on the ALB target group to ensure WebSocket connections persist to the same ECS task. The duration is configurable via `STICKY_SESSION_DURATION` in `.env` (default: 86400 seconds = 1 day).

### Auto-Scaling

The ECS service is configured with auto-scaling based on:
- **CPU Utilization**: Scales when CPU > 70%
- **Memory Utilization**: Scales when Memory > 80%

Scaling limits:
- **Minimum**: 1 task (configurable via `MIN_TASK_COUNT`)
- **Maximum**: 4 tasks (configurable via `MAX_TASK_COUNT`)

### Security Groups

- **ALB Security Group**: Allows HTTP (80) and HTTPS (443) from internet
- **ECS Security Group**: Allows traffic from ALB only (port 8000)

### Health Checks

ALB health checks are configured to:
- **Path**: `/health/`
- **Interval**: 30 seconds
- **Timeout**: 5 seconds
- **Healthy Threshold**: 2 consecutive successes
- **Unhealthy Threshold**: 3 consecutive failures

### WebSocket Support

- ALB idle timeout set to 3600 seconds (1 hour) for long-lived WebSocket connections
- Sticky sessions ensure WebSocket connections route to the same task

## Environment Management

To deploy to different environments (stage, live, etc.):

1. **Create environment-specific `.env` files**:
   ```bash
   cp .env .env.stage
   cp .env .env.live
   ```

2. **Update environment-specific values** in each `.env` file

3. **Deploy each environment**:
   ```bash
   # Load stage environment
   export $(cat .env.stage | xargs)
   cdk deploy WebSocketPbaStack-stage

   # Load live environment
   export $(cat .env.live | xargs)
   cdk deploy WebSocketPbaStack-live
   ```

Alternatively, use CDK context to pass environment:

```bash
cdk deploy --context environment=stage
```

## Troubleshooting

### Stack Deployment Fails

1. **Check AWS Credentials**: Ensure AWS CLI is configured correctly
   ```bash
   aws sts get-caller-identity
   ```

2. **Verify VPC/Subnet IDs**: Ensure the VPC and subnet IDs in `.env` are correct
   ```bash
   aws ec2 describe-vpcs --vpc-ids <VPC_ID>
   aws ec2 describe-subnets --subnet-ids <SUBNET_ID>
   ```

3. **Check ECR Repository**: Ensure the ECR repository exists and has images
   ```bash
   aws ecr describe-repositories --repository-names <ECR_REPOSITORY_NAME>
   ```

### ECS Tasks Not Starting

1. **Check Task Logs**:
   ```bash
   aws logs tail /ecs/websocket-pba-stage --follow
   ```

2. **Verify Environment Variables**: Check that required Django environment variables are set in `.env`

3. **Check Security Groups**: Ensure ECS security group allows traffic from ALB

### Health Checks Failing

1. **Verify Health Endpoint**: Test the health endpoint directly on a task
2. **Check Security Groups**: Ensure ALB can reach ECS tasks
3. **Review Task Logs**: Check for application errors

## Cleanup

To destroy the stack:

```bash
cdk destroy WebSocketPbaStack-stage
```

**Warning**: This will delete all resources created by the stack, including the ALB, ECS service, and associated resources.

## Cost Optimization

- **Fargate Tasks**: Charges based on vCPU and memory usage. Current configuration uses 256 CPU units (0.25 vCPU) and 512 MB memory per task.
- **ALB**: Charges per hour and per LCU (Load Balancer Capacity Unit)
- **CloudWatch Logs**: Log retention set to 7 days to minimize costs

## Additional Resources

- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [ECS Fargate Documentation](https://docs.aws.amazon.com/ecs/latest/developerguide/AWS_Fargate.html)
- [Application Load Balancer Documentation](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/)
- [ALB Sticky Sessions](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html)
