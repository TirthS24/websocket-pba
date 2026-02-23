# WebSocket PBA – Infrastructure

CDK stack for ECS Fargate (ws_server + LLM), ALBs, and ElastiCache Redis. All ECS task environment variables are loaded from `infrastructure/.env` at deploy time and passed to the task definitions.

## Deploy

1. Copy `env.example` to `.env` and fill in your values (VPC, ECR, ENVIRONMENT, and all app env vars for ECS tasks).
2. Run `./infrastructure/deploy.sh` to build images, push to ECR, and deploy the stack.
