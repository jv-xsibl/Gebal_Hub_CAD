"""Adapter contracts for CAD-focused product input parsing."""

from gebal_cad_normalizer.adapters.base import CadInputAdapter
from gebal_cad_normalizer.adapters.bluestone import BluestoneAdapter
from gebal_cad_normalizer.adapters.local import LocalAdapter
from gebal_cad_normalizer.adapters.unified import UnifiedAdapter

__all__ = ["BluestoneAdapter", "CadInputAdapter", "LocalAdapter", "UnifiedAdapter"]
