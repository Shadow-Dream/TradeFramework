#!/usr/bin/env python3
"""Dataset use cases which compose repository, authority, and Runtime layers."""

from engine.authority import dataset as dataset_authority
from engine.repository import datasets
from engine.runtime import dataset as dataset_runtime


def get_dataset_records(config, version_id, limit=None):
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("Dataset record limit must be a positive integer.")
    version = datasets.verify_dataset_version_id(config, version_id)
    _version, authority = dataset_authority.verify_dataset_version_storage_authority(
        config["releaseRoot"], version
    )
    return dataset_runtime.load_dataset_records(authority, limit=limit)
