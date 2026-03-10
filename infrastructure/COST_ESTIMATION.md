# AWS Cost Estimation — Single Environment (us-east-2)

This document estimates monthly costs for **one environment** of the WebSocket PBA stack (`infrastructure/stack.py`) in **AWS Region us-east-2 (Ohio)**. Estimates are given for **1,000**, **10,000**, and **100,000** users with usage assumptions described per service.

**Pricing references:** All figures are derived from the linked AWS pricing pages. Where a page does not list us-east-2 explicitly, pricing is assumed aligned with US East (N. Virginia) or stated as such.

---

## 1. Application Load Balancer (ALB)

**Cost reference:** [Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)

The stack deploys **two** ALBs: one for the WebSocket server (ws_server) and one for the LLM service. ALB pricing in us-east-2 consists of:

- **Hourly charge:** ~$0.0225 per ALB per hour (each partial hour billed as full).
- **LCU (Load Balancer Capacity Units):** Billed per minute based on the dimension with highest usage (new connections/sec, active connections/min, processed GB/hour, rule evaluations/sec). LCU rate in us-east-2 is approximately **$0.0082 per LCU-hour** (region-specific rate; see [pricing page](https://aws.amazon.com/elasticloadbalancing/pricing/) for current LCU pricing by region).

**Use case:** WebSocket (long-lived connections) and HTTP to LLM. Assumptions:

- **1,000 users:** Low traffic; ~1–2 LCUs per ALB on average → 2 ALBs × ($0.0225 × 720 + 2 × $0.0082 × 720) ≈ **$44/month**.
- **10,000 users:** Moderate; ~5–8 LCUs per ALB → 2 × ($16.20 + 8 × $5.90) ≈ **$110/month**.
- **100,000 users:** High; ~15–25 LCUs per ALB → 2 × ($16.20 + 20 × $5.90) ≈ **$252/month**.


| Costing per unit(s)                 | 1,000 users | 10,000 users | 100,000 users |
| ----------------------------------- | ----------- | ------------ | ------------- |
| 2 ALBs; $0.0225/hr + $0.0082/LCU-hr | ~$44/mo     | ~$110/mo     | ~$252/mo      |


---

## 2. Amazon ECS Fargate

**Cost reference:** [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/)

The stack runs two Fargate services (ws_server and LLM), each with:

- **Task size:** 1 vCPU, 2 GB memory, Linux/ARM (Graviton).
- **Scaling:** Min 1, max 4 tasks per service (configurable via `MIN_TASK_COUNT` / `MAX_TASK_COUNT` and LLM equivalents).

Fargate bills on **vCPU-seconds** and **memory (GB)-seconds** from image pull until task stop. For **Linux/ARM** in US regions, the Fargate page lists (e.g. US East N. Virginia): **$0.0000089944 per vCPU-second**, **$0.0000009889 per GB-second**. Ephemeral storage: first 20 GB included; we assume no extra. us-east-2 pricing is typically in line with us-east-1 for Fargate; refer to the [pricing page](https://aws.amazon.com/fargate/pricing/) for the current regional table.

**Per task per month (730 hours):**  
1 vCPU × $0.0000089944 × 3600 × 730 + 2 GB × $0.0000009889 × 3600 × 730 ≈ **$28.85 per task/month**.

**Assumptions:**

- **1,000 users:** 1 ws_server + 1 LLM task (2 tasks) → **~$58/month**.
- **10,000 users:** ~~2 ws + 2 LLM (4 tasks) → **~~$115/month**.
- **100,000 users:** ~~4 ws + 4 LLM (8 tasks) → **~~$231/month**.


| Costing per unit(s)                   | 1,000 users       | 10,000 users       | 100,000 users      |
| ------------------------------------- | ----------------- | ------------------ | ------------------ |
| 1 vCPU, 2 GB ARM; per task ~$28.85/mo | ~$58/mo (2 tasks) | ~$115/mo (4 tasks) | ~$231/mo (8 tasks) |


---

## 3. Amazon ElastiCache for Redis

**Cost reference:** [Amazon ElastiCache pricing](https://aws.amazon.com/elasticache/pricing/)

The stack uses **one** ElastiCache for Redis cluster:

- **Node type:** `cache.t3.micro` (configurable via `REDIS_CACHE_NODE_TYPE`).
- **Topology:** 1 node group, 0 replicas (single node).

On-demand pricing in **us-east-2** for Redis (cache.t3.micro) is approximately **$0.027 per node-hour** (see [ElastiCache pricing](https://aws.amazon.com/elasticache/pricing/) and regional tabs / calculator).

**Monthly:** $0.027 × 730 ≈ **$19.71/month**. This is independent of user count for a single node.


| Costing per unit(s)       | 1,000 users | 10,000 users | 100,000 users |
| ------------------------- | ----------- | ------------ | ------------- |
| cache.t3.micro, $0.027/hr | ~$20/mo     | ~$20/mo      | ~$20/mo       |


---

## 4. Amazon RDS for PostgreSQL

**Cost reference:** [Amazon RDS for PostgreSQL pricing](https://aws.amazon.com/rds/postgresql/pricing/)

The stack optionally creates one RDS instance when `CREATE_RDS=true`:

- **Instance:** `db.t4g.micro` (BURSTABLE4_GRAVITON), Single-AZ.
- **Storage:** 20 GB General Purpose SSD (gp2/gp3), default.

**Instance:** On-demand in us-east-2 for db.t4g.micro Single-AZ is approximately **$0.016 per hour** (see [RDS PostgreSQL pricing](https://aws.amazon.com/rds/postgresql/pricing/) for current regional rates).  
**Monthly instance:** $0.016 × 730 ≈ **$11.68/month**.

**Storage:** General Purpose SSD (Single-AZ) in us-east-2 is about **$0.115 per GB-month**.  
**20 GB:** 20 × $0.115 ≈ **$2.30/month**.

**Total RDS:** ~**$14/month**, largely unchanged across user tiers for this instance size.


| Costing per unit(s)                                | 1,000 users | 10,000 users | 100,000 users |
| -------------------------------------------------- | ----------- | ------------ | ------------- |
| db.t4g.micro + 20 GB gp2; $0.016/hr + $0.115/GB-mo | ~$14/mo     | ~$14/mo      | ~$14/mo       |


---

## 5. Amazon ECR

**Cost reference:** [Amazon ECR pricing](https://aws.amazon.com/ecr/pricing/)

The stack references two ECR repositories (ws_server and LLM). Pricing:

- **Storage:** **$0.10 per GB-month** for private repository storage.
- **Data transfer:** Pulls from ECR to Fargate in the **same region** are **$0.00**.

Assuming **~2 GB** total stored (e.g. a few image tags across both repos): 2 × $0.10 = **$0.20/month**. Data transfer in-region is free, so no extra cost by user tier.


| Costing per unit(s)                             | 1,000 users | 10,000 users | 100,000 users |
| ----------------------------------------------- | ----------- | ------------ | ------------- |
| $0.10/GB-month storage; in-region transfer free | ~$0.20/mo   | ~$0.20/mo    | ~$0.20/mo     |


---

## 6. Amazon CloudWatch Logs

**Cost reference:** [Amazon CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)

The stack creates one log group (`/ecs/websocket-pba-{environment}`) with **1 week retention**. Pricing in us-east-2:

- **Ingestion:** **$0.50 per GB** (first 5 GB/month often free tier).
- **Storage:** **$0.03 per GB-month** (see [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/) for Logs).

Assumptions for ECS log volume:

- **1,000 users:** ~~3 GB ingested → under 5 GB free → **~~$0/month** (storage negligible).
- **10,000 users:** ~~15 GB ingested → (15 − 5) × $0.50 = **~~$5/month** (+ minimal storage).
- **100,000 users:** ~~80 GB ingested → (80 − 5) × $0.50 = **~~$37.50/month** (+ storage ~$2).


| Costing per unit(s)                              | 1,000 users | 10,000 users | 100,000 users |
| ------------------------------------------------ | ----------- | ------------ | ------------- |
| $0.50/GB ingestion (5 GB free); $0.03/GB storage | ~$0/mo      | ~$5/mo       | ~$40/mo       |


---

## 7. AWS Secrets Manager

**Cost reference:** [AWS Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/)

The stack uses:

- **Secrets:** `pba-{env}/ws-server-secrets`, `pba-{env}/llm-server-secrets`, and the RDS-generated secret (when RDS is created).

Pricing:

- **$0.40 per secret per month.**
- **$0.05 per 10,000 API calls** (e.g. GetSecretValue).

**Secrets:** 3 × $0.40 = **$1.20/month** (fixed).

**API calls:** Tasks read secrets at startup and possibly on refresh. Roughly:

- **1,000 users:** ~~6k calls → **~~$0.03**.
- **10,000 users:** ~~25k calls → **~~$0.13**.
- **100,000 users:** ~~120k calls → **~~$0.60**.


| Costing per unit(s)                  | 1,000 users | 10,000 users | 100,000 users |
| ------------------------------------ | ----------- | ------------ | ------------- |
| $0.40/secret/mo; $0.05/10k API calls | ~$1.23/mo   | ~$1.33/mo    | ~$1.80/mo     |


---

## 8. VPC and data transfer

**Cost reference:** [Amazon VPC pricing](https://aws.amazon.com/vpc/pricing/)

The stack **imports** an existing VPC and subnets; there is **no charge for the VPC** itself. Data transfer:

- **Same AZ:** No charge for traffic between EC2/ECS and ElastiCache/RDS in the same AZ.
- **Cross-AZ in region:** Typically **$0.01 per GB** each way (charged on the EC2/ECS side).
- **Internet egress:** Standard data transfer out rates apply if clients connect from the internet (ALB is internet-facing).

For a single environment, we assume modest cross-AZ and egress and add a small buffer in the summary. No separate row in the final table; impact is reflected in the “Notes” below.

---

## Final summary table

All values are **approximate monthly costs in USD** for **us-east-2 (Ohio)** for one environment. Rows are the services from this stack; columns are costing units and the three user tiers.


| Service                       | Costing per unit(s)                              | 1,000 users | 10,000 users | 100,000 users |
| ----------------------------- | ------------------------------------------------ | ----------- | ------------ | ------------- |
| **Application Load Balancer** | 2 ALBs; $0.0225/hr + $0.0082/LCU-hr              | ~$44        | ~$110        | ~$252         |
| **ECS Fargate**               | 1 vCPU, 2 GB ARM; ~$28.85/task/month             | ~$58        | ~$115        | ~$231         |
| **ElastiCache Redis**         | cache.t3.micro; $0.027/hr                        | ~$20        | ~$20         | ~$20          |
| **RDS PostgreSQL**            | db.t4g.micro + 20 GB; $0.016/hr + $0.115/GB-mo   | ~$14        | ~$14         | ~$14          |
| **ECR**                       | $0.10/GB-month storage                           | ~$0.20      | ~$0.20       | ~$0.20        |
| **CloudWatch Logs**           | $0.50/GB ingestion (5 GB free); $0.03/GB storage | ~$0         | ~$5          | ~$40          |
| **Secrets Manager**           | $0.40/secret/mo; $0.05/10k API calls             | ~$1.23      | ~$1.33       | ~$1.80        |
| **Total (approx.)**           |                                                  | **~$137**   | **~$265**    | **~$559**     |


---

## Notes

- **Region:** All estimates use **us-east-2 (Ohio)**. For exact rates, use the [AWS Pricing Calculator](https://calculator.aws/) and select us-east-2.
- **Bedrock:** The LLM service uses AWS Bedrock (inference profiles). Cost depends on model, tokens, and usage; not included in the table. See [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).
- **Scaling:** Fargate and ALB LCU scale with load; user tiers are illustrative. Adjust task count and LCU assumptions to match real traffic.
- **Reserved capacity:** Savings Plans (Fargate) and Reserved Instances (RDS, ElastiCache) can reduce these costs for steady, long-term usage.
- **Free tier:** New accounts may have free tier benefits (e.g. ALB hours, Fargate, RDS, ECR, CloudWatch, Secrets Manager); apply where applicable.

