"""Pipeline boundary for future CAD normalization orchestration.

Future processing stages will accept a
:class:`gebal_cad_normalizer.models.CadProcessingRequest`.

Stage 1A intentionally adds typed CAD input contracts only. This module does
not download, convert, inspect, normalize, or validate CAD files yet.
"""

