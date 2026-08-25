"""Utils for working with STAC items/catalogs."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import quote

import boto3
import pystac
import pystac_client
import requests
from ripple1d.utils.s3_utils import s3_get_output_s3path


def key_to_uri(key: str, bucket: str) -> str:
    """Convert a key to a uri."""
    return f"https://{bucket}.s3.amazonaws.com/{quote(key)}"


def uri_to_key(href: str, bucket: str) -> str:
    """Convert a uri to a key."""
    return href.replace(f"https://{bucket}.s3.amazonaws.com/", "")


def collection_exists(endpoint: str, collection_id: str):
    """Check if a collection exists in a STAC API."""
    return requests.get(f"{endpoint}/collections/{collection_id}")


def create_collection(
    models: List[pystac.Item], id: str, description: str = None, title: str = None
) -> pystac.Collection:
    """Create a STAC collection from a list of STAC items."""
    extent = pystac.Extent.from_items(models)
    return pystac.Collection(
        id=id,
        description=description,
        title=title,
        extent=extent,
    )


def upsert_collection(endpoint: str, collection: pystac.Collection, headers: dict):
    """Upsert a collection to a STAC API."""
    collections_url = f"{endpoint}/collections"
    response = requests.post(collections_url, json=collection.to_dict(), headers=headers)
    if response.status_code == 409:
        logging.warning("collection already exists, updating...")
        collections_update_url = f"{collections_url}/{collection.id}"
        response = requests.put(collections_update_url, json=collection.to_dict(), headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"Error putting collection {(response.status_code)}")
    elif response.status_code not in (201, 200):
        raise RuntimeError(f"Error posting collection {(response.status_code)}")


def upsert_item(endpoint: str, collection_id: str, item: pystac.Item, headers: dict):
    """Upsert an item to a STAC API."""
    items_url = f"{endpoint}/collections/{collection_id}/items"
    response = requests.post(items_url, json=item.to_dict(), headers=headers)

    if response.status_code == 409:
        item_update_url = f"{items_url}/{item.id}"
        response = requests.put(item_update_url, json=item.to_dict(), headers=headers)
    elif response.status_code != 200:
        return f"Response from STAC API: {response.status_code}"
    if not response.ok:
        return f"Response from STAC API: {response.status_code}"


def delete_collection(endpoint: str, collection_id: str, headers: dict):
    """Upsert a collection to a STAC API."""
    collections_url = f"{endpoint}/collections/{collection_id}"
    return requests.delete(collections_url, headers=headers)


def download_model_from_stac_item(
    stac_item: pystac.Item,
    ras_directory: str,
    client: boto3.session.Session.client,
    bucket: str,
) -> str:
    """Download HEC-RAS model from stac href. Return the terrain_name."""
    # make RAS directory if it does not exists
    if not os.path.exists(ras_directory):
        os.makedirs(ras_directory)

    # download HEC-RAS model files
    for _, asset in stac_item.get_assets(role="hec-ras").items():
        s3_key = asset.extra_fields["s3_key"]

        file = os.path.join(ras_directory, Path(s3_key).name)
        client.download_file(bucket, s3_key, file)

    # download HEC-RAS topo files
    for _, asset in stac_item.get_assets(role="ras-topo").items():
        s3_key = asset.extra_fields["s3_key"]

        file = os.path.join(ras_directory, Path(s3_key).name)
        client.download_file(bucket, s3_key, file)

        if ".hdf" in Path(s3_key).name:
            terrain_name = Path(s3_key).name.rstrip(".hdf")

    return terrain_name
