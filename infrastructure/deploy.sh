#!/bin/bash
set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting deployment process...${NC}"

# Load environment variables from .env file
# Use absolute path to handle directory changes later in the script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: .env file not found at $ENV_FILE${NC}"
    exit 1
fi

# Source the .env file
set -a
source "$ENV_FILE"
set +a

# Validate required environment variables
if [ -z "$AWS_REGION" ]; then
    echo -e "${RED}Error: AWS_REGION not set in .env file${NC}"
    exit 1
fi

if [ -z "$ECR_REPOSITORY_NAME" ]; then
    echo -e "${RED}Error: ECR_REPOSITORY_NAME not set in .env file${NC}"
    exit 1
fi

if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo -e "${RED}Error: AWS_ACCOUNT_ID not set in .env file${NC}"
    exit 1
fi

# Construct ECR repository URI
ECR_REPOSITORY_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY_NAME}"
# Use timestamp as tag so ECS always pulls the new image (avoids "latest" cache).
# If ECR_IMAGE_TAG is set and is not "latest", use it; otherwise use timestamp.
IMAGE_TAG="${ECR_IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
if [ "$IMAGE_TAG" = "latest" ]; then
    IMAGE_TAG="$(date +%Y%m%d%H%M%S)"
fi
IMAGE_URI="${ECR_REPOSITORY_URI}:${IMAGE_TAG}"

echo -e "${YELLOW}Configuration:${NC}"
echo "  AWS Region: $AWS_REGION"
echo "  ECR Repository: $ECR_REPOSITORY_NAME"
echo "  ECR Repository URI: $ECR_REPOSITORY_URI"
echo "  Image Tag: $IMAGE_TAG"
echo "  Image URI: $IMAGE_URI"
echo ""

# Step 1: Build Docker image
echo -e "${GREEN}Step 1: Building Docker image...${NC}"
cd "$(dirname "$0")/.."  # Go to project root

# Detect architecture and build natively for Mac Silicon (ARM64) or x86_64
# ECS Fargate supports both ARM64 and X86_64 architectures
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    echo -e "${YELLOW}  Detected ARM64 architecture (Apple Silicon Mac)${NC}"
    echo -e "${YELLOW}  Building natively for linux/arm64 (matches ECS Fargate ARM64)...${NC}"
    BUILD_PLATFORM="linux/arm64"
    # Pull base image for ARM64
    echo -e "${YELLOW}  Pulling base image for linux/arm64 platform...${NC}"
    docker pull --platform linux/arm64 python:3.12-slim || true
else
    echo -e "${YELLOW}  Detected x86_64 architecture, building for linux/amd64...${NC}"
    BUILD_PLATFORM="linux/amd64"
    # Pull base image for AMD64
    echo -e "${YELLOW}  Pulling base image for linux/amd64 platform...${NC}"
    docker pull --platform linux/amd64 python:3.12-slim || true
fi

# Remove any cached layers that might be for wrong platform
echo -e "${YELLOW}  Clearing Docker build cache to avoid platform mismatch...${NC}"
docker builder prune -f || true

# Build natively for detected architecture
# DOCKER_BUILDKIT=0 uses classic builder to avoid buildx manifest list issues
# --no-cache ensures we don't use cached layers from wrong platform
echo -e "${YELLOW}  Building image for $BUILD_PLATFORM platform (no cache to avoid platform mismatch)...${NC}"
DOCKER_BUILDKIT=0 docker build \
    --platform "$BUILD_PLATFORM" \
    --pull \
    --no-cache \
    -t "${ECR_REPOSITORY_NAME}:${IMAGE_TAG}" \
    -f Dockerfile .

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Docker build failed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker image built successfully${NC}"
echo ""

# Step 2: Login to ECR
echo -e "${GREEN}Step 2: Logging in to Amazon ECR...${NC}"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REPOSITORY_URI"
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: ECR login failed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Logged in to ECR successfully${NC}"
echo ""

# Step 3: Tag image for ECR
echo -e "${GREEN}Step 3: Tagging Docker image for ECR...${NC}"
docker tag "${ECR_REPOSITORY_NAME}:${IMAGE_TAG}" "$IMAGE_URI"
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Docker tag failed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Image tagged successfully${NC}"
echo ""

# Step 4: Push image to ECR
echo -e "${GREEN}Step 4: Pushing Docker image to ECR...${NC}"
docker push "$IMAGE_URI"
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Docker push failed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Image pushed to ECR successfully${NC}"
echo ""

# Step 5: Phase 1 - Deploy ALB first (without ECS service) to get DNS name
echo -e "${GREEN}Step 5: Phase 1 - Deploying ALB infrastructure (without ECS service)...${NC}"
echo -e "${YELLOW}  This allows us to get the ALB DNS name before creating ECS tasks${NC}"
echo ""

# Get stack name from environment
STACK_NAME="WebSocketPbaStack-${ENVIRONMENT:-devlive}"

# Check if stack already exists
# Use || true to prevent script exit if stack doesn't exist (expected case)
STACK_EXISTS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query 'Stacks[0].StackName' \
    --output text 2>/dev/null || echo "")

cd "$(dirname "$0")"  # Go back to infrastructure directory

if [ -z "$STACK_EXISTS" ] || [ "$STACK_EXISTS" == "None" ]; then
    # Stack doesn't exist - create it with ALB only first
    echo -e "${YELLOW}  Stack doesn't exist. Creating new stack with ALB infrastructure first...${NC}"
    echo -e "${YELLOW}  This is a two-phase deployment to ensure ALLOWED_HOSTS is set correctly${NC}"
    echo ""
    export DEPLOY_ECS_SERVICE="false"
    echo -e "${GREEN}  Phase 1: Creating CloudFormation stack with ALB (ECS service will be added in Phase 2)...${NC}"
    cdk deploy --all
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: CDK deployment failed during Phase 1 (ALB creation)${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Stack created successfully with ALB infrastructure${NC}"
    
    # Get ALB DNS name from stack outputs
    echo -e "${YELLOW}  Waiting for ALB to be fully provisioned...${NC}"
    sleep 10  # Give CloudFormation time to create the ALB
    
    # Retry getting ALB DNS in case it's not immediately available
    ALB_DNS=""
    for i in {1..5}; do
        ALB_DNS=$(aws cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION" \
            --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerDNS`].OutputValue' \
            --output text 2>/dev/null || echo "")
        
        if [ -n "$ALB_DNS" ] && [ "$ALB_DNS" != "None" ]; then
            break
        fi
        
        if [ $i -lt 5 ]; then
            echo -e "${YELLOW}  ALB DNS not yet available, retrying in 5 seconds... (attempt $i/5)${NC}"
            sleep 5
        fi
    done
    
    if [ -z "$ALB_DNS" ] || [ "$ALB_DNS" == "None" ]; then
        echo -e "${RED}Error: Could not retrieve ALB DNS name from stack outputs${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}  ✓ ALB DNS name: $ALB_DNS${NC}"
    
    # # Step 6: Update DJANGO_ALLOWED_HOSTS with ALB DNS (strip port if present)
    # echo -e "${GREEN}Step 6: Updating DJANGO_ALLOWED_HOSTS with ALB DNS name and VPC wildcard...${NC}"
    
    # # Strip port number from ALB DNS (e.g., example.com:8000 -> example.com)
    # ALB_DNS_NO_PORT="${ALB_DNS%%:*}"
    
    # # Backup .env file
    # cp "$ENV_FILE" "${ENV_FILE}.backup"
    
    # # Update or add DJANGO_ALLOWED_HOSTS
    # CURRENT_ALLOWED_HOSTS=$(grep "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    
    # # Build new ALLOWED_HOSTS value
    # # IMPORTANT: Strip ports from all existing entries to ensure no ports in ALLOWED_HOSTS
    # # Add .10.0 wildcard to allow ALB health checks from VPC private IPs (10.0.x.x)
    # NEW_ALLOWED_HOSTS=""
    # if [ -z "$CURRENT_ALLOWED_HOSTS" ] || [ "$CURRENT_ALLOWED_HOSTS" == "" ]; then
    #     # Start fresh with ALB DNS (without port), localhost, and VPC wildcard
    #     NEW_ALLOWED_HOSTS="$ALB_DNS_NO_PORT,localhost,127.0.0.1,.10.0"
    # else
    #     # Process existing hosts: strip ports from each entry and build clean list
    #     IFS=',' read -ra HOST_ARRAY <<< "$CURRENT_ALLOWED_HOSTS"
    #     CLEAN_HOSTS=()
    #     for host in "${HOST_ARRAY[@]}"; do
    #         # Strip whitespace and port
    #         clean_host=$(echo "$host" | xargs | cut -d':' -f1)
    #         if [ -n "$clean_host" ]; then
    #             CLEAN_HOSTS+=("$clean_host")
    #         fi
    #     done
        
    #     # Add ALB DNS if not already present
    #     ALB_FOUND=false
    #     for host in "${CLEAN_HOSTS[@]}"; do
    #         if [ "$host" == "$ALB_DNS_NO_PORT" ]; then
    #             ALB_FOUND=true
    #             break
    #         fi
    #     done
        
    #     if [ "$ALB_FOUND" = false ]; then
    #         CLEAN_HOSTS+=("$ALB_DNS_NO_PORT")
    #     fi
        
    #     # Add localhost if not present
    #     if [[ ! " ${CLEAN_HOSTS[@]} " =~ " localhost " ]]; then
    #         CLEAN_HOSTS+=("localhost")
    #     fi
        
    #     # Add 127.0.0.1 if not present
    #     if [[ ! " ${CLEAN_HOSTS[@]} " =~ " 127.0.0.1 " ]]; then
    #         CLEAN_HOSTS+=("127.0.0.1")
    #     fi
        
    #     # Add .10.0 wildcard for VPC private IPs if not present
    #     if [[ ! " ${CLEAN_HOSTS[@]} " =~ " .10.0 " ]]; then
    #         CLEAN_HOSTS+=(".10.0")
    #     fi
        
    #     # Join all hosts with comma (no ports)
    #     NEW_ALLOWED_HOSTS=$(IFS=','; echo "${CLEAN_HOSTS[*]}")
    # fi
    
    # # Update DJANGO_ALLOWED_HOSTS in .env file
    # if grep -q "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE"; then
    #     # Remove old line and add new one (safer than sed for cross-platform)
    #     grep -v "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE" > "${ENV_FILE}.tmp"
    #     echo "DJANGO_ALLOWED_HOSTS=$NEW_ALLOWED_HOSTS" >> "${ENV_FILE}.tmp"
    #     mv "${ENV_FILE}.tmp" "$ENV_FILE"
    # else
    #     # Add new line
    #     echo "DJANGO_ALLOWED_HOSTS=$NEW_ALLOWED_HOSTS" >> "$ENV_FILE"
    # fi
    
    # # Update ALB_ENDPOINT for CORS configuration
    # ALB_ENDPOINT="http://$ALB_DNS_NO_PORT"
    # if grep -q "^ALB_ENDPOINT=" "$ENV_FILE"; then
    #     # Remove old line and add new one
    #     grep -v "^ALB_ENDPOINT=" "$ENV_FILE" > "${ENV_FILE}.tmp"
    #     echo "ALB_ENDPOINT=$ALB_ENDPOINT" >> "${ENV_FILE}.tmp"
    #     mv "${ENV_FILE}.tmp" "$ENV_FILE"
    # else
    #     # Add new line
    #     echo "ALB_ENDPOINT=$ALB_ENDPOINT" >> "$ENV_FILE"
    # fi
    
    # echo -e "${YELLOW}  Added to ALLOWED_HOSTS: $ALB_DNS_NO_PORT (ALB DNS without port)${NC}"
    # echo -e "${YELLOW}  Added to ALLOWED_HOSTS: .10.0 (VPC wildcard for 10.0.x.x IPs)${NC}"
    # echo -e "${YELLOW}  Added ALB_ENDPOINT: $ALB_ENDPOINT (for CORS configuration)${NC}"
    # echo -e "${YELLOW}  Note: .10.0 wildcard allows ALB health checks from all VPC private IPs${NC}"
    
    # echo -e "${GREEN}  ✓ Updated DJANGO_ALLOWED_HOSTS and ALB_ENDPOINT${NC}"
    
    # # Reload environment variables from updated .env file
    # set -a
    # source "$ENV_FILE"
    # set +a
    
    # Step 7: Phase 2 - Deploy ECS service with correct ALLOWED_HOSTS
    echo -e "${GREEN}Step 7: Phase 2 - Deploying ECS service with correct ALLOWED_HOSTS...${NC}"
    export ECR_IMAGE_URI="$IMAGE_URI"
    export DEPLOY_ECS_SERVICE="true"
    
    cdk deploy --all
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: CDK deployment failed${NC}"
        echo -e "${YELLOW}  Restoring .env file from backup...${NC}"
        mv "${ENV_FILE}.backup" "$ENV_FILE"
        exit 1
    fi
    
    # Remove backup file on success
    rm -f "${ENV_FILE}.backup"
    echo -e "${GREEN}  ✓ ECS service deployed with correct DJANGO_ALLOWED_HOSTS${NC}"
else
    # Stack exists - check if ALB DNS is already in ALLOWED_HOSTS
    echo -e "${YELLOW}  Stack already exists. Checking if ALB DNS and VPC wildcard are in ALLOWED_HOSTS...${NC}"
    
    # Get ALB DNS name from existing stack
    ALB_DNS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerDNS`].OutputValue' \
        --output text 2>/dev/null || echo "")
    
    if [ -z "$ALB_DNS" ] || [ "$ALB_DNS" == "None" ]; then
        echo -e "${YELLOW}  Warning: Could not retrieve ALB DNS name from stack outputs${NC}"
        echo -e "${YELLOW}  Proceeding with regular deployment...${NC}"
        export ECR_IMAGE_URI="$IMAGE_URI"
        export DEPLOY_ECS_SERVICE="true"
        cdk deploy --all
    else
        echo -e "${YELLOW}  ALB DNS name: $ALB_DNS${NC}"
        
        # Strip port number from ALB DNS
        ALB_DNS_NO_PORT="${ALB_DNS%%:*}"
        
        # Check if DJANGO_ALLOWED_HOSTS needs updating
        CURRENT_ALLOWED_HOSTS=$(grep "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
        
        # Check if ALB DNS (without port) and .10.0 wildcard are already in ALLOWED_HOSTS
        ALB_PRESENT=false
        VPC_WILDCARD_PRESENT=false
        
        if echo "$CURRENT_ALLOWED_HOSTS" | grep -q "$ALB_DNS_NO_PORT"; then
            ALB_PRESENT=true
        fi
        
        if echo "$CURRENT_ALLOWED_HOSTS" | grep -q ".10.0"; then
            VPC_WILDCARD_PRESENT=true
        fi
        
        if [ "$ALB_PRESENT" = true ] && [ "$VPC_WILDCARD_PRESENT" = true ]; then
            echo -e "${GREEN}  ✓ DJANGO_ALLOWED_HOSTS already contains ALB DNS and VPC wildcard${NC}"
            export ECR_IMAGE_URI="$IMAGE_URI"
            export DEPLOY_ECS_SERVICE="true"
            cdk deploy --all
        else
            echo -e "${YELLOW}  Updating DJANGO_ALLOWED_HOSTS in .env file...${NC}"
            
            # Backup .env file
            cp "$ENV_FILE" "${ENV_FILE}.backup"
            
            # Build new ALLOWED_HOSTS value
            # IMPORTANT: Strip ports from all existing entries to ensure no ports in ALLOWED_HOSTS
            # Add .10.0 wildcard to allow ALB health checks from VPC private IPs (10.0.x.x)
            NEW_ALLOWED_HOSTS=""
            if [ -z "$CURRENT_ALLOWED_HOSTS" ] || [ "$CURRENT_ALLOWED_HOSTS" == "" ]; then
                # Start fresh with ALB DNS (without port), localhost, and VPC wildcard
                NEW_ALLOWED_HOSTS="$ALB_DNS_NO_PORT,localhost,127.0.0.1,.10.0"
            else
                # Process existing hosts: strip ports from each entry and build clean list
                IFS=',' read -ra HOST_ARRAY <<< "$CURRENT_ALLOWED_HOSTS"
                CLEAN_HOSTS=()
                for host in "${HOST_ARRAY[@]}"; do
                    # Strip whitespace and port
                    clean_host=$(echo "$host" | xargs | cut -d':' -f1)
                    if [ -n "$clean_host" ]; then
                        CLEAN_HOSTS+=("$clean_host")
                    fi
                done
                
                # Add ALB DNS if not already present
                ALB_FOUND=false
                for host in "${CLEAN_HOSTS[@]}"; do
                    if [ "$host" == "$ALB_DNS_NO_PORT" ]; then
                        ALB_FOUND=true
                        break
                    fi
                done
                
                if [ "$ALB_FOUND" = false ]; then
                    CLEAN_HOSTS+=("$ALB_DNS_NO_PORT")
                fi
                
                # Add localhost if not present
                if [[ ! " ${CLEAN_HOSTS[@]} " =~ " localhost " ]]; then
                    CLEAN_HOSTS+=("localhost")
                fi
                
                # Add 127.0.0.1 if not present
                if [[ ! " ${CLEAN_HOSTS[@]} " =~ " 127.0.0.1 " ]]; then
                    CLEAN_HOSTS+=("127.0.0.1")
                fi
                
                # Add .10.0 wildcard for VPC private IPs if not present
                if [[ ! " ${CLEAN_HOSTS[@]} " =~ " .10.0 " ]]; then
                    CLEAN_HOSTS+=(".10.0")
                fi
                
                # Join all hosts with comma (no ports)
                NEW_ALLOWED_HOSTS=$(IFS=','; echo "${CLEAN_HOSTS[*]}")
            fi
            
            # Update DJANGO_ALLOWED_HOSTS in .env file
            if grep -q "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE"; then
                # Remove old line and add new one (safer than sed for cross-platform)
                grep -v "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE" > "${ENV_FILE}.tmp"
                echo "DJANGO_ALLOWED_HOSTS=$NEW_ALLOWED_HOSTS" >> "${ENV_FILE}.tmp"
                mv "${ENV_FILE}.tmp" "$ENV_FILE"
            else
                # Add new line
                echo "DJANGO_ALLOWED_HOSTS=$NEW_ALLOWED_HOSTS" >> "$ENV_FILE"
            fi
            
            # Update ALB_ENDPOINT for CORS configuration
            ALB_ENDPOINT="http://$ALB_DNS_NO_PORT"
            if grep -q "^ALB_ENDPOINT=" "$ENV_FILE"; then
                # Remove old line and add new one
                grep -v "^ALB_ENDPOINT=" "$ENV_FILE" > "${ENV_FILE}.tmp"
                echo "ALB_ENDPOINT=$ALB_ENDPOINT" >> "${ENV_FILE}.tmp"
                mv "${ENV_FILE}.tmp" "$ENV_FILE"
            else
                # Add new line
                echo "ALB_ENDPOINT=$ALB_ENDPOINT" >> "$ENV_FILE"
            fi
            
            echo -e "${GREEN}  ✓ Updated DJANGO_ALLOWED_HOSTS and ALB_ENDPOINT${NC}"
            echo -e "${YELLOW}  Added: $ALB_DNS_NO_PORT (ALB DNS without port)${NC}"
            echo -e "${YELLOW}  Added: .10.0 (VPC wildcard for 10.0.x.x IPs)${NC}"
            echo -e "${YELLOW}  Added: ALB_ENDPOINT=$ALB_ENDPOINT (for CORS)${NC}"
            
            # Reload environment variables
            set -a
            source "$ENV_FILE"
            set +a
            
            export ECR_IMAGE_URI="$IMAGE_URI"
            export DEPLOY_ECS_SERVICE="true"
            
            cdk deploy --all
            if [ $? -ne 0 ]; then
                echo -e "${RED}Error: CDK deployment failed${NC}"
                echo -e "${YELLOW}  Restoring .env file from backup...${NC}"
                mv "${ENV_FILE}.backup" "$ENV_FILE"
                exit 1
            fi
            
            rm -f "${ENV_FILE}.backup"
        fi
    fi
fi

echo ""
echo -e "${GREEN}✓ Deployment completed successfully!${NC}"
echo -e "${GREEN}Image URI used: $ECR_IMAGE_URI${NC}"
if [ -n "$ALB_DNS" ] && [ "$ALB_DNS" != "None" ]; then
    echo -e "${GREEN}ALB DNS: http://$ALB_DNS${NC}"
fi
echo -e "${YELLOW}DJANGO_ALLOWED_HOSTS configured with: $NEW_ALLOWED_HOSTS${NC}"