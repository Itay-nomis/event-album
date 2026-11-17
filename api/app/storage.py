"""Object storage abstraction shared by the api and the worker.

The rest of the code only talks to the small `Storage` interface below, so the
same app runs against:
  * STORAGE_BACKEND=s3    -> SeaweedFS locally (or any S3-compatible service), via boto3
  * STORAGE_BACKEND=azure -> Azure Blob Storage, authenticated with DefaultAzureCredential
                             (on AKS this picks up the pod's workload identity: no keys at all)

NOTE: this file is intentionally duplicated in api/app/storage.py and worker/storage.py
(each Docker image only gets its own folder). A test checks that both copies stay identical.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Protocol


class NotFound(Exception):
    """Raised when a key does not exist in the bucket/container."""


class Storage(Protocol):
    def ensure_container(self) -> None:
        """Create the bucket/container if it does not exist yet."""

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store bytes under `key`."""

    def get(self, key: str) -> bytes:
        """Read the whole object. Raises NotFound."""

    def stream(self, key: str) -> tuple[Iterator[bytes], str]:
        """Return (chunk iterator, content type) for streaming to a client. Raises NotFound."""


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ):
        import boto3  # imported lazily so the azure backend does not need it at import time

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def ensure_container(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def _get_object(self, key: str) -> dict:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                raise NotFound(key) from e
            raise

    def get(self, key: str) -> bytes:
        return self._get_object(key)["Body"].read()

    def stream(self, key: str) -> tuple[Iterator[bytes], str]:
        obj = self._get_object(key)
        return obj["Body"].iter_chunks(), obj.get("ContentType") or "application/octet-stream"


class AzureBlobStorage:
    def __init__(self, account_url: str, container: str, credential=None):
        from azure.storage.blob import BlobServiceClient

        if credential is None:
            # Tries env vars, workload identity (AKS), managed identity, `az login`... in order.
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        self.service = BlobServiceClient(account_url=account_url, credential=credential)
        self.container = self.service.get_container_client(container)

    def ensure_container(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self.container.create_container()
        except ResourceExistsError:
            pass

    def put(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(
            name=key, data=data, overwrite=True, content_settings=ContentSettings(content_type=content_type)
        )

    def _download(self, key: str):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.download_blob(key)
        except ResourceNotFoundError as e:
            raise NotFound(key) from e

    def get(self, key: str) -> bytes:
        return self._download(key).readall()

    def stream(self, key: str) -> tuple[Iterator[bytes], str]:
        downloader = self._download(key)
        content_type = downloader.properties.content_settings.content_type or "application/octet-stream"
        return downloader.chunks(), content_type


def from_env() -> Storage:
    """Build the storage backend selected by the STORAGE_BACKEND env var."""
    backend = os.environ.get("STORAGE_BACKEND", "s3").lower()
    if backend == "s3":
        return S3Storage(
            bucket=os.environ.get("S3_BUCKET", "photos"),
            endpoint_url=os.environ.get("S3_ENDPOINT") or None,
            access_key=os.environ.get("S3_ACCESS_KEY") or None,
            secret_key=os.environ.get("S3_SECRET_KEY") or None,
            region=os.environ.get("S3_REGION", "us-east-1"),
        )
    if backend == "azure":
        return AzureBlobStorage(
            account_url=os.environ["AZURE_STORAGE_ACCOUNT_URL"],  # https://<account>.blob.core.windows.net
            container=os.environ.get("AZURE_STORAGE_CONTAINER", "photos"),
        )
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r} (expected 's3' or 'azure')")
