"""Build wheel ensuring Windows OS and python >=3.10."""

import os
import re
import subprocess
import sys
import warnings


def update_pyproject_version():
    """Get version from version.py and update pyproject.toml."""
    with open("ripple1d/version.py", "r") as f:
        version_file_content = f.read()

    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file_content, re.M)
    if version_match:
        version = version_match.group(1)
    else:
        raise RuntimeError("Unable to find version string in version.py")

    with open("pyproject.toml", "r") as f:
        pyproject_content = f.read()

    pyproject_content = re.sub(r'^version = ".*"$', f'version = "{version}"', pyproject_content, flags=re.M)

    with open("pyproject.toml", "w") as f:
        f.write(pyproject_content)


update_pyproject_version()


if os.name != "nt":
    warnings.warn("Windows OS is required to run ripple1d. Many features will not work on other OS's.")

if sys.version_info < (3, 10):
    sys.exit("Python 3.10 or greater is required to build this package.")

platform_tag = "win_amd64"


subprocess.check_call([os.sys.executable, "-m", "build"])


dist_dir = "dist"
for filename in os.listdir(dist_dir):
    if filename.endswith(".whl"):
        parts = filename.split("-")
        parts[2] = "py310"
        parts[-1] = platform_tag + ".whl"
        new_filename = "-".join(parts)
        os.rename(os.path.join(dist_dir, filename), os.path.join(dist_dir, new_filename))
        break
