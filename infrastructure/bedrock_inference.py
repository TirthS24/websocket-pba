"""
create_bedrock_inference_profiles.py
-------------------------------------
Creates AWS Bedrock Application Inference Profiles for every combination of
environment × use-case, all pointing to the US cross-region inference profile
for Claude Haiku 4.5 (us.anthropic.claude-haiku-4-5-20251001-v1:0).

BEFORE RUNNING
--------------
1.  Fill in MODEL_SOURCE_ARN below with the correct ARN from your AWS account.
    Typical value for the built-in US cross-region profile:
        arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-haiku-4-5-20251001-v1:0
    OR the system-defined cross-region profile ARN visible in your Bedrock console.

2.  Ensure your AWS credentials are configured (env vars, ~/.aws/credentials,
    or an IAM role) with the bedrock:CreateInferenceProfile permission.

3.  Install boto3 if needed:  pip install boto3

WHAT IT CREATES
---------------
15 inference profiles (3 environments × 5 use-cases) in us-east-2.

Naming convention:  {environment}_{use_case}
Examples:
  devlive_sms_router
  testlive_web_respond
  live_thread_summarize

HOW TO RUN
----------
    python create_bedrock_inference_profiles.py

    # Dry-run (prints what would be created, makes no API calls):
    python create_bedrock_inference_profiles.py --dry-run

    # Delete all 15 profiles created by this script:
    python create_bedrock_inference_profiles.py --delete
"""

import argparse
import json
import time
import boto3
from botocore.exceptions import ClientError

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURE THIS ↓
# ──────────────────────────────────────────────────────────────────────────────

MODEL_SOURCE_ARN = "arn:aws:bedrock:us-east-2:819245696044:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0"

AWS_REGION = "us-east-2"

# ──────────────────────────────────────────────────────────────────────────────
# MATRIX DEFINITION
# ──────────────────────────────────────────────────────────────────────────────

ENVIRONMENTS = ["Devlive", "Testlive", "Live"]

USE_CASES = [
    "smsRouter",
    "webRouter",
    "threadSummarize",
    "smsRespond",
    "webRespond",
]

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def build_profile_name(environment: str, use_case: str) -> str:
    """e.g.  devlive_sms_router"""
    return f"PBA{environment}-{use_case}"


def build_tags(environment: str, use_case: str) -> list[dict]:
    """Resource tags attached to every inference profile."""
    return [
        {"key": "PBA_Environment",  "value": environment},
        {"key": "PBA_UseCase",      "value": use_case},
        {"key": "Model",        "value": "claude-haiku-4-5"},
    ]


def generate_profile_matrix() -> list[dict]:
    """Return the full list of (name, env, use_case) dicts to be created."""
    profiles = []
    for env in ENVIRONMENTS:
        for uc in USE_CASES:
            profiles.append(
                {
                    "name":        build_profile_name(env, uc),
                    "environment": env,
                    "use_case":    uc,
                }
            )
    return profiles


# ──────────────────────────────────────────────────────────────────────────────
# CORE OPERATIONS
# ──────────────────────────────────────────────────────────────────────────────

def create_profile(client, profile: dict, dry_run: bool = False) -> dict:
    """
    Create a single Bedrock application inference profile.
    Returns a result dict with status and ARN (or error message).
    """
    name      = profile["name"]
    env       = profile["environment"]
    use_case  = profile["use_case"]

    print(f"  → Creating: {name}", end="", flush=True)

    if dry_run:
        print("  [DRY-RUN – skipped]")
        return {"name": name, "status": "dry-run", "arn": "n/a"}

    try:
        response = client.create_inference_profile(
            inferenceProfileName=name,
            description="Inference profile for cost management of PBA use for different use cases",
            modelSource={
                "copyFrom": MODEL_SOURCE_ARN
            },
            tags=build_tags(env, use_case),
        )

        arn    = response["inferenceProfileArn"]
        status = response.get("status", "ACTIVE")
        print(f"  [OK]  ARN: {arn}")
        return {"name": name, "status": status, "arn": arn}

    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]

        if code == "ResourceInUseException":
            print(f"  [SKIP] Already exists.")
            return {"name": name, "status": "already_exists", "arn": "existing"}
        else:
            print(f"  [FAIL] {code}: {msg}")
            return {"name": name, "status": "error", "error": f"{code}: {msg}"}


def delete_profile(client, profile_arn: str, name: str) -> None:
    """Delete a single inference profile by ARN."""
    try:
        client.delete_inference_profile(inferenceProfileIdentifier=profile_arn)
        print(f"  → Deleted: {name}  ({profile_arn})")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        print(f"  → FAILED to delete {name}: {code}: {msg}")


def list_managed_profiles(client) -> list[dict]:
    """
    List all application inference profiles tagged with ManagedBy=this script.
    Returns list of {name, arn} dicts.
    """
    managed = []
    paginator = client.get_paginator("list_inference_profiles")

    for page in paginator.paginate(typeEquals="APPLICATION"):
        for p in page.get("inferenceProfileSummaries", []):
            managed.append({"name": p["inferenceProfileName"], "arn": p["inferenceProfileArn"]})

    # Filter to only profiles whose name matches our naming convention
    expected_names = {build_profile_name(e, u) for e in ENVIRONMENTS for u in USE_CASES}
    return [p for p in managed if p["name"] in expected_names]


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Create / delete Bedrock inference profiles for all env × use-case combos."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making any API calls.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete all 15 inference profiles created by this script.",
    )
    parser.add_argument(
        "--region",
        default=AWS_REGION,
        help=f"AWS region to target (default: {AWS_REGION}).",
    )
    args = parser.parse_args()

    client = boto3.client("bedrock", region_name=args.region)

    # ── DELETE MODE ──────────────────────────────────────────────────────────
    if args.delete:
        print(f"\n{'─'*60}")
        print(f"DELETE MODE  |  region: {args.region}")
        print(f"{'─'*60}\n")

        profiles = list_managed_profiles(client)

        if not profiles:
            print("No managed inference profiles found. Nothing to delete.")
            return

        print(f"Found {len(profiles)} profile(s) to delete:\n")
        for p in profiles:
            delete_profile(client, p["arn"], p["name"])
            time.sleep(0.3)   # gentle rate-limiting

        print(f"\nDone. {len(profiles)} profile(s) deleted.")
        return

    # ── CREATE MODE ──────────────────────────────────────────────────────────
    mode_label = "DRY-RUN" if args.dry_run else "CREATE"
    print(f"\n{'─'*60}")
    print(f"{mode_label} MODE  |  region: {args.region}")
    print(f"Model source ARN: {MODEL_SOURCE_ARN}")
    print(f"{'─'*60}\n")

    profiles  = generate_profile_matrix()
    results   = []
    succeeded = 0
    skipped   = 0
    failed    = 0

    for i, profile in enumerate(profiles, 1):
        print(f"[{i:02d}/{len(profiles)}]", end="  ")
        result = create_profile(client, profile, dry_run=args.dry_run)
        results.append(result)

        if result["status"] in ("ACTIVE", "CREATING", "dry-run"):
            succeeded += 1
        elif result["status"] == "already_exists":
            skipped += 1
        else:
            failed += 1

        # Gentle rate-limiting to avoid ThrottlingException
        if not args.dry_run:
            time.sleep(0.5)

    # ── SUMMARY ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"SUMMARY")
    print(f"{'─'*60}")
    print(f"  Total profiles targeted : {len(profiles)}")
    print(f"  Created / OK            : {succeeded}")
    print(f"  Already existed (skipped): {skipped}")
    print(f"  Errors                  : {failed}")

    if failed:
        print(f"\nFailed profiles:")
        for r in results:
            if r["status"] == "error":
                print(f"  - {r['name']}: {r.get('error')}")

    print(f"\nAll inference profile names created:")
    for r in results:
        arn_display = r["arn"] if r["arn"] != "n/a" else "(dry-run)"
        print(f"  {r['name']:<35}  {arn_display}")

    # Optionally dump results as JSON
    output_file = "inference_profiles_result.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to: {output_file}")


if __name__ == "__main__":
    main()