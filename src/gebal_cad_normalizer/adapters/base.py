"""Base adapter contract for typed CAD input parsing."""

from typing import Any, Mapping, Protocol

from gebal_cad_normalizer.models import AdapterResult


class CadInputAdapter(Protocol):
    """Adapter interface for vendor or unified product payloads."""

    def parse(self, payload: Mapping[str, Any]) -> AdapterResult:
        """Parse a raw payload into a CAD processing request or issues."""

