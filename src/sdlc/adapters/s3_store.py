"""ObjectStore backed by S3. Used when profile=aws: state (checkpoint, step run state,
sync manifest, plans, traceability) lives at s3://<bucket>/<state_prefix><key>, so the
scheduled Lambda and the AgentCore-hosted agent share state with each other and with
any local-profile run against the same bucket."""
import json

import boto3
from botocore.exceptions import ClientError


class S3ObjectStore:
    def __init__(self, bucket: str, region: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix
        self._client = boto3.client("s3", region_name=region)

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get_json(self, key: str) -> dict | None:
        data = self.get_bytes(key)
        return json.loads(data) if data is not None else None

    def put_json(self, key: str, value: dict, dry_run: bool = True) -> None:
        self.put_bytes(key, json.dumps(value, indent=2).encode(), dry_run=dry_run)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=self._key(key))
            return resp["Body"].read()
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def put_bytes(self, key: str, data: bytes, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would write s3://{self.bucket}/{self._key(key)} ({len(data)} bytes)")
            return
        self._client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def delete(self, key: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would delete s3://{self.bucket}/{self._key(key)}")
            return
        self._client.delete_object(Bucket=self.bucket, Key=self._key(key))
