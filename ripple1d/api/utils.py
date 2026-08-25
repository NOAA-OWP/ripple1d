"""Generic utilities supporting the huey + Flask REST API."""

import inspect
import logging
import os
import sysconfig
import traceback
import typing
from functools import wraps


def get_unexpected_and_missing_args(func: typing.Callable, kwargs_provided: set) -> tuple[list, list]:
    """Inspect func and evaluate whether the provided kwargs will satisfy it, considering its required & optional args.

    Return a list of unexpected kwargs (provided, but not called for),
    and return a list of missing kwargs (required but not provided).
    WARNING: assumes the func does not use *args or **kwargs features.
    """
    argspec = inspect.getfullargspec(func)
    unexpected = kwargs_provided - set(argspec.args)
    if argspec.defaults:
        required = set(argspec.args[: -len(argspec.defaults)])
    else:
        required = set(argspec.args)
    missing = required - kwargs_provided
    return sorted(unexpected), sorted(missing)


def find_huey_consumer():
    """Find the huey_consumer.py file in the installed packages and return its path."""
    site_packages_path = sysconfig.get_paths()["purelib"]

    for root, dirs, files in os.walk(site_packages_path):
        if "huey_consumer.py" in files:
            huey_consumer_path = os.path.join(root, "huey_consumer.py")
            print(f"Found huey_consumer.py at: {huey_consumer_path}")
            return huey_consumer_path

    print("huey_consumer.py not found in installed packages.")
    return None
