"""Utilities for S3."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import boto3

if TYPE_CHECKING:
    from ripple1d.hecstac.ras.item import RASModelItem


def save_bytes_s3(
    data: io.BytesIO,
    s3_path: str,
    content_type: str = "",
):
    """Upload BytesIO to S3."""
    parsed = urlparse(s3_path)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=data.getvalue(), ContentType=content_type)


def save_file_s3(
    local_path: str,
    s3_path: str,
):
    """Upload BytesIO to S3."""
    parsed = urlparse(s3_path)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    s3 = boto3.client("s3")
    s3.upload_file(Filename=local_path, Bucket=bucket, Key=key)


def parse_s3_url(s3_url: str):
    """
    Extract the bucket name and path from an S3 URL.

    Args:
        s3_url (str): The S3 URL (e.g., 's3://my-bucket/path/to/object.txt').

    Returns
    -------
        tuple: (bucket_name, path)
    """
    parsed = urlparse(s3_url)
    bucket = parsed.netloc
    path = parsed.path.lstrip("/")
    return bucket, path


def make_uri_public(uri: str) -> str:
    """Convert from an AWS S3 URI to an https url."""
    bucket, path = parse_s3_url(uri)
    return f"https://{bucket}.s3.amazonaws.com/{path}"
