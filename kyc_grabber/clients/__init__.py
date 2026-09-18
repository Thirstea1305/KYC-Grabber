"""Client adapters for the two data sources used by the pipeline."""

from .external_db import ExternalDatabaseClient, ExternalLookupError
from .internal_db import InternalDatabaseClient

__all__ = ["ExternalDatabaseClient", "ExternalLookupError", "InternalDatabaseClient"]
