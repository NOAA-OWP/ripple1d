"""Errors for the ras module."""


class GeometryAssetInvalidCRSError(Exception):
    """Invalid crs provided to geometry asset."""


class GeometryAssetMissingCRSError(Exception):
    """Required crs is missing from geometry asset definition."""


class Invalid1DGeometryError(Exception):
    """1D geometry asset either has no cross sections or reaches."""


class InvalidStructureDataError(Exception):
    """Raised when a HEC-RAS geometry structure is invalid."""
