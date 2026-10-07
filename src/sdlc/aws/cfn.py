"""Thin wrapper around `aws cloudformation deploy`/`describe-stacks`/`delete-stack`.

We shell out to the AWS CLI for deploy/delete (not boto3) so --dry-run can print the
exact command, per CLAUDE.md/BRIEF.md ("supports --dry-run (print commands only)").
boto3 (cloudformation client) is used only for the read-only describe-stacks lookup.
"""
import shlex
import subprocess

import boto3
from botocore.exceptions import ClientError


def deploy_stack(*, template_path: str, stack_name: str, region: str,
                  parameters: dict[str, str], tags: dict[str, str],
                  dry_run: bool = True) -> None:
    cmd = [
        "aws", "cloudformation", "deploy",
        "--template-file", template_path,
        "--stack-name", stack_name,
        "--region", region,
        "--capabilities", "CAPABILITY_NAMED_IAM",
    ]
    if parameters:
        cmd += ["--parameter-overrides", *(f"{k}={v}" for k, v in parameters.items())]
    if tags:
        cmd += ["--tags", *(f"{k}={v}" for k, v in tags.items())]

    if dry_run:
        print(f"[dry-run] would run: {shlex.join(cmd)}")
        return
    subprocess.run(cmd, check=True)


def stack_outputs(*, stack_name: str, region: str) -> dict[str, str]:
    """Read-only; always runs (even under --dry-run callers use this to check what
    already exists before deciding whether a deploy/create step is needed)."""
    client = boto3.client("cloudformation", region_name=region)
    stacks = client.describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def stack_exists(*, stack_name: str, region: str) -> bool:
    try:
        stack_outputs(stack_name=stack_name, region=region)
        return True
    except ClientError as exc:
        if "does not exist" in str(exc):
            return False
        raise


def delete_stack(*, stack_name: str, region: str, dry_run: bool = True) -> None:
    cmd = ["aws", "cloudformation", "delete-stack", "--stack-name", stack_name, "--region", region]
    if dry_run:
        print(f"[dry-run] would run: {shlex.join(cmd)}")
        return
    subprocess.run(cmd, check=True)
