# ripple1d
[![CI](https://github.com/dewberry/ripple1d/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/dewberry/ripple1d/actions/workflows/ci.yaml)
[![Documentation Status](https://readthedocs.org/projects/ripple1d/badge/?version=latest)](https://ripple1d.readthedocs.io/en/latest/?badge=latest)
[![Release](https://github.com/dewberry/ripple1d/actions/workflows/release.yaml/badge.svg)](https://github.com/dewberry/ripple1d/actions/workflows/release.yaml)
[![PyPI version](https://badge.fury.io/py/ripple1d.svg)](https://badge.fury.io/py/ripple1d)

Utilities for repurposing HEC-RAS models for use in the production of Flood Inundation Maps (FIMs) and scenario databases for use in near-real time flood forecasting on the NOAA National Water Model network. Go to [ReadTheDocs](http://ripple1d.readthedocs.io/) for more information on ripple1d.

## Contents

 - [api](api/) : Source code for the [Flask](https://flask.palletsprojects.com/en/3.0.x/) API and [Huey](https://huey.readthedocs.io/en/latest/) queueing system for managing parallel compute.
 - [ripple1d](ripple1d/): Source code for the ripple1d library.
 - [tests](tests/): Unit tests.up


## Requirements

*OS Dependency*: Ripple requires Python version >=3.10 and a Windows environment with Desktop Experience (GUI, not a headless Windows server) and [HEC-RAS](https://www.hec.usace.army.mil/software/hec-ras/download.aspx) installed (currently version 6.3.1 is supported).


## Installing Ripple

##### *NOTE: Using a python virtual environment is not required but is highly recommended.*

### Using pip

Activate virtual environment as shown below and install the `ripple1d` package using `pip` using PowerShell:

```powershell
    pip install ripple1d
```


### Verify the Installation

Verify the installation by importing `ripple1d` in a Python shell:

```powershell
    python
    >>> import ripple1d
    >>> print(ripple1d.__version__)
```


---


### Credits and References
1. [Office of Water Prediction (OWP)](https://water.noaa.gov/)
1. [Dewberry](https://www.dewberry.com/)
1. [Raytheon](https://www.rtx.com/)
1. [ Earth Resources Technology, Inc.](https://www.ertcorp.com/)
1. [ras2fim](https://github.com/NOAA-OWP/ras2fim)
1. [USACE HEC-RAS](https://www.hec.usace.army.mil/software/hec-ras/)
1. NOAA National Water Model [(NWM)](https://water.noaa.gov/about/nwm)




**Special Thanks to:** David Bascom (FEMA), Christina Lindemer (FEMA), Dave Rosa (FEMA), Paul Rooney (FEMA),  Julia Signell and Dan Pilone of [Element84](https://www.element84.com/), and the developers of [STAC](https://stacspec.org/en).
