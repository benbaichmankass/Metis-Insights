"""Top-level `ml` package marker.

Empty marker so `ml` is an explicit regular package and `ml.datasets`
(plus future `ml.features`, `ml.trainers`, ...) resolve cleanly under
pytest's default `importmode=prepend` collection without relying on
namespace-package fallback behavior.
"""
