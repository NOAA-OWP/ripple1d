"""Utils for working with s3."""

import json
import logging
import os
import pathlib
import posixpath
import re
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
import boto3.session
import botocore
import pystac
from mypy_boto3_s3.service_resource import ObjectSummary

logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)


def str_from_s3(ras_text_file_path: str, client, bucket) -> str:
    """Read a text file from s3 and return its contents as a string."""
    logging.debug(f"reading: {ras_text_file_path}")
    response = client.get_object(Bucket=bucket, Key=ras_text_file_path)
    return response["Body"].read().decode("utf-8")


def list_keys(s3_client: boto3.Session.client, bucket: str, prefix: str, suffix: str = "") -> list:
    """List all keys in an S3 bucket with a given prefix and suffix."""
    keys = []
    kwargs = {"Bucket": bucket, "Prefix": prefix}
    while True:
        resp = s3_client.list_objects_v2(**kwargs)
        keys += [obj["Key"] for obj in resp["Contents"] if obj["Key"].endswith(suffix)]
        try:
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        except KeyError:
            break
    return keys


def list_keys_regex(s3_client: boto3.Session.client, bucket: str, prefix_includes: str, suffix="") -> list:
    """List all keys in an S3 bucket with a given prefix and suffix."""
    keys = []
    kwargs = {"Bucket": bucket, "Prefix": prefix_includes}
    prefix_pattern = re.compile(prefix_includes.replace("*", ".*"))
    while True:
        resp = s3_client.list_objects_v2(**kwargs)
        keys += [
            obj["Key"] for obj in resp["Contents"] if prefix_pattern.match(obj["Key"]) and obj["Key"].endswith(suffix)
        ]
        try:
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        except KeyError:
            break
    return keys


def init_s3_resources() -> tuple:
    """Establish a boto3 (AWS) session and return the session, S3 client, and S3 resource handles."""
    # Instantitate S3 resources
    session = boto3.Session(
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )

    s3_client = session.client("s3")
    s3_resource = session.resource("s3")
    return session, s3_client, s3_resource


def check_s3_key_exists(bucket: str, key: str) -> bool:
    """Check if an object with the given key exists in the specified S3 bucket."""
    s3 = boto3.client("s3")
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def read_json_from_s3(bucket: str, key: str) -> dict:
    """Read a JSON file from an S3 bucket and return its contents as a dictionary."""
    s3_client = boto3.client("s3")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    file_content = response["Body"].read().decode("utf-8")
    json_content = json.loads(file_content)
    return json_content


def get_basic_object_metadata(obj: ObjectSummary) -> dict:
    """
    Retrieve basic metadata of an AWS S3 object.

    Parameters
    ----------
        obj (ObjectSummary): The AWS S3 object.

    Returns
    -------
        dict: A dictionary with the size, ETag, last modified date, storage platform, region, and storage tier of the object.
    """
    try:
        _ = obj.load()
        return {
            "file:size": obj.content_length,
            "e_tag": obj.e_tag.strip('"'),
            "last_modified": obj.last_modified.isoformat(),
            "storage:platform": "AWS",
            "storage:region": obj.meta.client.meta.region_name,
            "storage:tier": obj.storage_class,
        }
    except botocore.exceptions.ClientError:
        raise KeyError(f"Unable to access {obj.key} check that key exists and you have access")


def copy_item_to_s3(item: pystac.Item, s3_key: str, s3client: botocore.client.BaseClient):
    """
    Copy an item to an AWS S3 bucket.

    Parameters
    ----------
        item: The item to copy. It must have a `to_dict` method that returns a dictionary representation of it.
        s3_key (str): The file path in the S3 bucket to copy the item to.

    The function performs the following steps:
        1. Initializes a boto3 S3 client and splits the s3_key into the bucket name and the key.
        2. Converts the item to a dictionary, serializes it to a JSON string, and encodes it to bytes.
        3. Puts the encoded JSON string to the specified file path in the S3 bucket.
    """
    # s3 = boto3.client("s3")
    bucket, key = split_s3_key(s3_key)
    s3client.put_object(Body=json.dumps(item.to_dict()).encode(), Bucket=bucket, Key=key)


def split_s3_key(s3_key: str) -> tuple[str, str]:
    """
    Split an S3 key into the bucket name and the key.

    Parameters
    ----------
        s3_key (str): The S3 key to split. It should be in the format 's3://bucket/key'.

    Returns
    -------
        tuple: A tuple containing the bucket name and the key. If the S3 key does not contain a key, the second element
          of the tuple will be None.

    The function performs the following steps:
        1. Removes the 's3://' prefix from the S3 key.
        2. Splits the remaining string on the first '/' character.
        3. Returns the first part as the bucket name and the second part as the key. If there is no '/', the key will
          be None.
    """
    parts = s3_key.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else None
    return bucket, key


def s3_key_public_url_converter(url: str, dev_mode: bool = False) -> str:
    """
    Convert an S3 URL to an HTTPS URL and vice versa.

    Args:
    ----------
        url (str): The URL to convert. It should be in the format 's3://bucket/' or 'https://bucket.s3.amazonaws.com/'.
        dev_mode (bool): A flag indicating whether the function should use the Minio endpoint for S3 URL conversion.
    Return:
    -------
        str: The converted URL. If the input URL is an S3 URL, the function returns an HTTPS URL. If the input URL is
        an HTTPS URL, the function returns an S3 URL.

    The function performs the following steps:
        1. Checks if the input URL is an S3 URL or an HTTPS URL.
        2. If the input URL is an S3 URL, it converts it to an HTTPS URL.
        3. If the input URL is an HTTPS URL, it converts it to an S3 URL.
    """
    if url.startswith("s3"):
        bucket = url.replace("s3://", "").split("/")[0]
        key = url.replace(f"s3://{bucket}", "")[1:]
        if dev_mode:
            logging.info(f"dev_mode | using minio endpoint for s3 url conversion: {url}")
            return f"{os.environ.get('MINIO_S3_ENDPOINT')}/{bucket}/{key}"
        else:
            return f"https://{bucket}.s3.amazonaws.com/{key}"

    elif url.startswith("http"):
        if dev_mode:
            logging.info(f"dev_mode | using minio endpoint for s3 url conversion: {url}")
            bucket = url.replace(os.environ.get("MINIO_S3_ENDPOINT"), "").split("/")[0]
            key = url.replace(os.environ.get("MINIO_S3_ENDPOINT"), "")
        else:
            bucket = url.replace("https://", "").split(".s3.amazonaws.com")[0]
            key = url.replace(f"https://{bucket}.s3.amazonaws.com/", "")

        return f"s3://{bucket}/{key}"

    else:
        raise ValueError(f"Invalid URL format: {url}")


def verify_safe_prefix(s3_key: str):
    """
    TODO: discuss this with the team. Would like some safety mechanism to ensure that the S3 key is limited to certain prefixes.

    Should there be some restriction where these files can be written?
    """
    parts = s3_key.split("/")
    logging.debug(f"parts of the s3_key: {parts}")
    if parts[3] != "stac":
        raise ValueError(f"prefix must begin with stac/, user provided {s3_key} needs to be corrected")


def s3_upload_dir_recursively(local_src_dir: str, tgt_dir: str, s3_client: botocore.client.BaseClient) -> None:
    """Copy all files from a local directory. tgt_dir can be local or a s3:// prefix."""
    assert tgt_dir.startswith("s3://")
    pathmod = posixpath
    if not os.path.isdir(local_src_dir):
        raise NotADirectoryError(local_src_dir)
    for root, _, files in os.walk(local_src_dir):
        rel_root = os.path.relpath(root, start=local_src_dir)
        if os.path is not posixpath:
            # copying to s3 (posix system), but running in Windows
            rel_root = pathlib.PurePath(rel_root).as_posix()
        if rel_root == ".":
            rel_root = ""
        for fn in files:
            src_file = os.path.join(root, fn)
            tgt_file = pathmod.join(tgt_dir, rel_root, fn)
            logging.debug(f"Uploading: {src_file} -> {tgt_file}")
            bucket_name, key = extract_bucketname_and_keyname(s3path=tgt_file)
            s3_client.upload_file(
                Filename=src_file,
                Bucket=bucket_name,
                Key=key,
            )


def s3_delete_dir_recursively(s3_dir: str, s3_resource: boto3.resources.factory.ServiceResource) -> None:
    """Delete a s3:// directory and its contents recursively. OK if dir does not exist."""
    logging.debug(f"Deleting directory if exists: {s3_dir}")
    if not s3_dir.startswith("s3://"):
        raise ValueError(f"Expected s3_dir to start with s3://, but got: {s3_dir}")
    bucket, key = extract_bucketname_and_keyname(s3path=s3_dir)
    if not key.strip():
        raise ValueError(f"s3 path too short: {s3_dir}")
    if len(key.split("/")) < 3:
        raise ValueError(f"s3 path too short: {s3_dir}")
    if not key.endswith("/"):
        key += "/"
    bucket_handle = s3_resource.Bucket(bucket)
    bucket_handle.objects.filter(Prefix=key).delete()


def extract_bucketname_and_keyname(s3path: str) -> tuple[str, str]:
    """Parse the provided s3:// object path and return its bucket name and key."""
    if not s3path.startswith("s3://"):
        raise ValueError(f"s3path does not start with s3://: {s3path}")
    bucket, _, key = s3path[5:].partition("/")
    return bucket, key


def s3_upload_status_file(
    stac_href: str,
    s3_bucket: str,
    s3_client: botocore.client.BaseClient,
    e: Exception | None,
):
    """
    Upload a status file to s3.

    If e is a Python exception, then upload a 'fail' json file to the href's standard
    output location on s3.  If e is None, then upload a 'succeed' json file.  Either file
    will have key "time" indicating the time that the file was uploaded.  A 'fail' file will
    also have keys "err" and "traceback" containing the exception as a string and the Python
    traceback of the exception, respectively.
    """
    s3_output_key_succeed, s3_output_key_fail = s3_get_ripple1d_status_file_key_names(stac_href, s3_bucket, s3_client)

    time_now_str = datetime.now(tz=timezone.utc).isoformat()
    if e is None:
        s3_output_key = s3_output_key_succeed
        body = {"time": time_now_str}
    elif isinstance(e, Exception):
        s3_output_key = s3_output_key_fail
        body = {
            "time": time_now_str,
            "err": str(e),
            "traceback": "".join(traceback.format_tb(e.__traceback__)),
        }
    else:
        raise TypeError(f"For e, expected None or type Exception, but got type: {type(e)}")

    logging.debug(f"Deleting if exists: {s3_output_key_succeed}")
    s3_client.delete_object(Bucket=s3_bucket, Key=s3_output_key_succeed)
    logging.debug(f"Deleting if exists: {s3_output_key_fail}")
    s3_client.delete_object(Bucket=s3_bucket, Key=s3_output_key_fail)

    body_str = json.dumps(body, indent=2)
    logging.debug(f"Writing: {s3_output_key} with body: {body_str}")
    s3_client.put_object(
        Body=body_str,
        Bucket=s3_bucket,
        Key=s3_output_key,
        ContentType="application/json",
    )


def s3_ripple1d_status_succeed_file_exists(
    stac_href: str, s3_bucket: str, s3_client: botocore.client.BaseClient
) -> bool:
    """Check if the standard ripple1d succeed sentinel file exists.  If it does, return True, otherwise return False."""
    s3_output_key_succeed, _ = s3_get_ripple1d_status_file_key_names(stac_href, s3_bucket, s3_client)
    logging.debug(f"Checking if s3 file exists: s3://{s3_bucket}/{s3_output_key_succeed}")
    try:
        s3_client.head_object(Bucket=s3_bucket, Key=s3_output_key_succeed)
    except botocore.exceptions.ClientError as e:
        if "Not Found" in str(e):
            return False  # typical ClientError when the object does not exist
        else:
            raise  # unexpected ClientError
    return True


def s3_get_ripple1d_status_file_key_names(
    stac_href: str, s3_bucket: str, s3_client: botocore.client.BaseClient
) -> tuple[str, str]:
    """Return two S3 key paths, the first to a succeed sentinel file, the second t oa failure sentinel file.

    This function does not check if the keys exist.
    """
    _, s3_output_dir_key = extract_bucketname_and_keyname(s3_get_output_s3path(s3_bucket, stac_href))
    s3_output_key_succeed = posixpath.join(s3_output_dir_key, "ripple1d-succeed.json")
    s3_output_key_fail = posixpath.join(s3_output_dir_key, "ripple1d-fail.json")
    return s3_output_key_succeed, s3_output_key_fail


def s3_get_output_s3path(s3_bucket: str, stac_href: str) -> str:
    """Return the s3 path to the output directory for the given stac_href."""
    return f"s3://{s3_bucket}/mip/dev/ripple1d/output{urlparse(stac_href).path}/"
