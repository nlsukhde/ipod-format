from __future__ import annotations


class IpodPrepError(Exception):
    """Base class for all app errors."""
    pass


class ConfigError(IpodPrepError):
    """Bad/missing configuration or invalid values."""
    pass


class PlanError(IpodPrepError):
    """Failed to build a TrackPlan for a given input file."""
    pass
