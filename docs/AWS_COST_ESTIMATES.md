# AWS Cost Estimates — WebSocket PBA Stack (us-east-2)

This document provides **monthly cost estimates** for the AWS resources defined in the CDK stack (`infrastructure/stack.py`) under **default configuration** and **single environment**, in region **us-east-2 (Ohio)**.

Estimates assume one environment (e.g. `devlive`), 24/7 operation, and typical low-to-moderate usage. Actual costs depend on traffic, scaling, and data transfer.

---

## 1. ECS Fargate (ws_server and LLM services)

**Purpose:** Runs the two application services on a single ECS cluster: **ws_server** (Django/WebSocket on port 8000) and **LLM** (LLM API on port 7980). Both use Fargate with ARM64 (Graviton), 1 vCPU and 2 GB memory per task, with default desired count 1 each (min 1, max 4).

**Cost reference:** [AWS Fargate Pricing](https://aws.amazon.com/fargate/pricing/)

**Default configuration (from stack):**
- **ws_server:** 1 vCPU, 2048 MiB memory, desired count 1, ARM64
- **LLM:** 1 vCPU, 2048 MiB memory, desired count 1, ARM64
- Billing: per second (1-minute minimum); 20 GB ephemeral storage included

**Cost estimates (us-east-2):**  
Fargate Linux/ARM in us-east-2 is typically similar to US East (N. Virginia). Using approximate per-vCPU and per-GB rates:

- **Per task (1 vCPU, 2 GB) running 24/7:** ~\$28–32/month  
- **Two tasks (ws_server + LLM):** **~\$56–64/month**

*Use the [AWS Pricing Calculator (Fargate)](https://calculator.aws/#/createCalculator/Fargate) and select us-east-2 for exact rates.*

---

## 2. Application Load Balancers (2 ALBs)

**Purpose:**  
- **First ALB:** Fronts the **ws_server** (HTTP/WebSocket), with 1-hour idle timeout for long-lived connections.  
- **Second ALB:** Fronts the **LLM** service (HTTP on port 7980).  

Both are internet-facing in public subnets and route to the corresponding ECS target groups.

**Cost reference:** [Elastic Load Balancing Pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)

**Pricing model:**  
- Hourly charge per ALB  
- Load Balancer Capacity Units (LCU) per hour (based on new connections, active connections, processed bytes, rule evaluations)

**Cost estimates (us-east-2):**  
Assuming ~1 LCU per ALB and standard hourly rate (region rates are close to us-east-1):

- **Per ALB (low traffic, ~1 LCU):** ~\$22–25/month  
- **Two ALBs:** **~\$44–50/month**

*Exact LCU usage depends on request rate and connection duration; use [AWS Pricing Calculator (ELB)](https://calculator.aws/#/createCalculator/ElasticLoadBalancing) for your region.*

---

## 3. ElastiCache for Redis

**Purpose:** In-memory cache used only by **ws_server** (e.g. sessions, pub/sub, real-time state). The LLM service does not connect to Redis. Deployed in private subnets with a security group that allows only ws_server ECS tasks.

**Cost reference:** [Amazon ElastiCache Pricing](https://aws.amazon.com/elasticache/pricing/)

**Default configuration (from stack):**
- **Node type:** `cache.t3.micro`
- **Topology:** 1 node group, 0 replicas (single-node, no automatic failover)
- **Engine:** Redis

**Cost estimates (us-east-2):**  
On-demand cache.t3.micro in us-east-2:

- **~\$0.027/hour** → **~\$19.70/month** (730 hours)

---

## 4. RDS for PostgreSQL (LLM checkpointer)

**Purpose:** PostgreSQL database used as the **LLM graph checkpointer** (conversation state). Optional in the stack (`CREATE_RDS=true` by default). When created, it lives in private subnets and is reachable by both ws_server and LLM ECS tasks. The stack creates a secret in Secrets Manager for the DB credentials.

**Cost reference:** [Amazon RDS for PostgreSQL Pricing](https://aws.amazon.com/rds/postgresql/pricing/)

**Default configuration (from stack):**
- **Engine:** PostgreSQL 17  
- **Instance:** Effective type **db.t4g.micro** (Graviton; stack maps non–db.t3.micro to `BURSTABLE4_GRAVITON` + `MICRO`)  
- **Storage:** 20 GB allocated  
- **Multi-AZ:** No (single-AZ)

**Cost estimates (us-east-2):**  
Approximate on-demand:

- **Instance (db.t4g.micro):** ~\$0.016/hour → **~\$11.70/month**  
- **Storage (20 GB):** ~\$0.115/GB-month → **~\$2.30/month**  
- **RDS total:** **~\$14/month**

*Included only when `CREATE_RDS=true` (default).*

---

## 5. Amazon CloudWatch Logs

**Purpose:** Central log group for both ECS services (`/ecs/websocket-pba-{environment}`). Log streams: `websocket-pba-ws` (ws_server) and `websocket-pba-llm` (LLM). Used for application and container logs.

**Cost reference:** [Amazon CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)

**Default configuration (from stack):**
- **Retention:** 1 week (`ONE_WEEK`)

**Cost estimates (us-east-2):**  
- **Ingestion:** ~\$0.50/GB  
- **Storage:** ~\$0.03/GB/month  

For two low-traffic ECS tasks with 1-week retention, assume **~\$1–3/month** (e.g. 1–2 GB ingestion).

---

## 6. AWS Secrets Manager

**Purpose:** Stores secrets referenced by the stack:  
- **pba-{environment}/ws-server-secrets** — Django secret key, auth API key, LLM service auth (used by ws_server).  
- **pba-{environment}/llm-server-secrets** — PSQL_* and Bedrock-related keys (used by LLM).  
When RDS is created by the stack, RDS also creates a secret for the DB credentials (consumed by the LLM task).

**Cost reference:** [AWS Secrets Manager Pricing](https://aws.amazon.com/secrets-manager/pricing/)

**Pricing model:**  
- Per secret per month  
- Per 10,000 API calls (retrieval)

**Cost estimates:**  
- **Per secret:** \$0.40/month  
- **Secrets used by this stack:** 2 referenced (ws-server, llm-server) + 1 RDS-generated if `CREATE_RDS=true` → **3 secrets**  
- **Monthly:** **~\$1.20** (API calls typically negligible at low usage)

---

## 7. ECS Cluster, VPC, and Security Groups

**Purpose:**  
- **ECS cluster** (`websocket-pba-{environment}`): Logical grouping for the two Fargate services.  
- **VPC and subnets:** Imported (existing); no VPC creation in the stack.  
- **Security groups:** ALB, ws_server ECS, LLM ECS, Redis, and RDS (when created) — control ingress/egress for ALBs, ECS, Redis, and RDS.

**Cost reference:**  
- [Amazon ECS Pricing](https://aws.amazon.com/ecs/pricing/) (cluster itself has no additional charge for Fargate)  
- [Amazon VPC Pricing](https://aws.amazon.com/vpc/pricing/)

**Cost estimates:**  
- **ECS cluster:** No extra charge (you pay only for Fargate tasks and other resources).  
- **VPC, subnets, security groups:** No charge.  
- **NAT Gateway:** Not created in this stack; if your existing VPC uses NAT Gateways for private subnets, those are billed separately.  

**In-scope cost for this stack:** **\$0** (for cluster, VPC, and SGs as defined here).

---

## 8. ECR (Elastic Container Registry)

**Purpose:** Stack **references existing** ECR repositories for ws_server and LLM images; it does not create them. Images are built and pushed by your deploy process (`deploy.sh` / CI).

**Cost reference:** [Amazon ECR Pricing](https://aws.amazon.com/ecr/pricing/)

**Cost estimates:**  
- **Storage:** ~\$0.10/GB-month.  
- For a few images (e.g. 2–5 GB total): **~\$0.20–0.50/month**.  
Included in the summary as an optional line; adjust based on actual storage.

---

## 9. Data Transfer and Other

**Purpose:** Data transfer in/out of the ALBs, and any cross-AZ or inter-service traffic. Not defined as a separate “service” in the stack but affects the bill.

**Cost reference:** [AWS Data Transfer Pricing](https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer)

**Cost estimates:**  
- First 100 GB out to internet (us-east-2): ~\$0.09/GB; first 1 GB out often free.  
- For low-to-moderate traffic, **~\$0–5/month** is a reasonable placeholder; replace with your own usage.

---

# Consolidated Monthly Cost (Single Environment, us-east-2)

| Service / Resource              | Purpose in project                          | Est. cost/month (USD) |
|---------------------------------|---------------------------------------------|------------------------|
| ECS Fargate (ws_server + LLM)  | Two container services (1 task each, 1 vCPU, 2 GB, ARM) | \$56–64 |
| Application Load Balancer (×2) | ws_server ALB + LLM ALB                     | \$44–50 |
| ElastiCache Redis (cache.t3.micro) | Redis for ws_server only                 | \$19.70 |
| RDS PostgreSQL (db.t4g.micro, 20 GB) | LLM checkpointer DB (when CREATE_RDS=true) | \$14 |
| CloudWatch Logs (1-week retention) | ECS log group for both services          | \$1–3 |
| Secrets Manager (3 secrets)     | ws-server, llm-server, RDS secret           | \$1.20 |
| ECS Cluster / VPC / Security Groups | No charge                                 | \$0 |
| ECR (storage, optional)        | Existing image repositories                 | \$0.20–0.50 |
| Data transfer (optional)        | Outbound and inter-AZ (placeholder)         | \$0–5 |
| **Total (approximate)**        |                                             | **\$136–152** |

All estimates use **default stack configuration**, **us-east-2**, and **one environment**. For precise figures, use the [AWS Pricing Calculator](https://calculator.aws/) with your region and expected usage.
