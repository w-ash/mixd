"""Service registry for track conversion functions.

Provides a clean way to register and dispatch track conversion functions
without hardcoding service-specific logic in base classes.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.config import get_logger

if TYPE_CHECKING:
    from src.domain.entities.track import ConnectorTrack

logger = get_logger(__name__).bind(service="connectors")

# Type alias for track conversion functions
TrackConverterFunc = Callable[[dict[str, Any]], "ConnectorTrack"]

# Global registry of track conversion functions
_track_converters: dict[str, TrackConverterFunc] = {}


def register_track_converter(
    service_name: str, converter_func: TrackConverterFunc
) -> None:
    """Register a track conversion function for a service.

    Args:
        service_name: Name of the service (e.g., "spotify", "lastfm")
        converter_func: Function that converts raw track data to ConnectorTrack
    """
    _track_converters[service_name] = converter_func
    logger.debug(f"Registered track converter for {service_name}")


def get_track_converter(service_name: str) -> TrackConverterFunc | None:
    """Get the track conversion function for a service.

    Args:
        service_name: Name of the service

    Returns:
        Conversion function or None if not registered
    """
    return _track_converters.get(service_name)


def convert_track_for_service(
    service_name: str, track_data: dict[str, Any]
) -> "ConnectorTrack":
    """Convert track data using the registered converter for a service.

    Args:
        service_name: Name of the service
        track_data: Raw track data from service API

    Returns:
        Standardized ConnectorTrack object

    Raises:
        NotImplementedError: If no converter is registered for the service
    """
    converter = get_track_converter(service_name)
    if converter is None:
        raise NotImplementedError(
            f"Track conversion not supported by {service_name} connector. "
            f"Register a converter using register_track_converter()."
        )

    return converter(track_data)


def get_registered_services() -> list[str]:
    """Get list of services with registered track converters."""
    return list(_track_converters.keys())
