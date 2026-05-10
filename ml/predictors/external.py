"""Provider-agnostic external-model predictor (S-AI-WS6-PART-1).

Concrete subclasses wrap an external model (Hugging Face
transformer, locally-hosted vLLM, a vendor API, etc.) and expose
the same :class:`ml.predictors.base.Predictor` interface the rest
of the training / shadow / evaluation pipeline already speaks.

Why this layer exists:

- WS5 baselines are in-house Predictor implementations. WS6 adds
  open-source models. We do NOT want the open-source integration
  to leak provider-specific concepts (HF `pipeline` objects,
  vendor SDK clients) into trainers, evaluators, or the shadow
  harness.
- The :class:`ExternalPredictor` ABC pins the contract:
  subclasses promise (a) a deterministic ``predict(row)``, (b) a
  ``provider`` + ``model_identifier`` pair for audit, and (c)
  errors raised as :exc:`ProviderError` so callers can distinguish
  external-system flakiness from logic bugs.
- The shadow harness already wraps Predictor instances in
  :class:`ml.predictors.shadow.ShadowPredictor`; any
  :class:`ExternalPredictor` subclass plugs into shadow mode
  without further work — including the per-tick try/except
  isolation in ``with_shadow_preds``.

NOT in this module:

- A concrete HF integration. That belongs in a follow-up PR
  (PART-2) that picks a specific use case + model and ships the
  approval-criteria documentation per
  ``docs/architecture/model-inventory.md``.
- Network / API client logic. ``ExternalPredictor`` is the
  interface; subclasses own the wire format.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Mapping

from .base import Predictor


class ProviderError(RuntimeError):
    """Raised by :class:`ExternalPredictor` subclasses when the
    backing model / API fails in a way distinct from a Python
    logic bug.

    Catching :exc:`ProviderError` lets callers distinguish
    "external system is flaky" from "predict() returned wrong
    value" or "feature row is malformed". The shadow harness'
    per-predictor ``try/except`` catches both — but logging /
    alerting downstream can use the type tag to route alerts.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model_identifier: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model_identifier = model_identifier


class ExternalPredictor(Predictor):
    """ABC for predictors backed by an external model.

    Subclasses MUST:

    - Provide ``provider`` (e.g. ``"huggingface"``, ``"vllm"``,
      ``"local-ollama"``) as a class attribute.
    - Provide ``model_identifier`` (e.g. ``"distilbert-base-uncased
      @v1.0"``) — pin the version. Identifier shape is
      provider-specific.
    - Implement :meth:`predict` returning a float. Raise
      :exc:`ProviderError` on backend failures.
    - Implement :meth:`describe` returning a short string for
      audit logs / model-registry display.

    Subclasses MAY:

    - Cache predictions internally if the backing model is
      expensive. Cache invalidation is the subclass's problem.
    - Batch predictions if the backend supports it; the
      :class:`Predictor` interface is single-row but subclasses
      can buffer if their use case allows.

    Subclasses MUST NOT:

    - Write to disk in ``predict``. Audit logging is
      :class:`ml.predictors.shadow.ShadowPredictor`'s job;
      duplicating it from inside the predictor breaks the
      "one log path per predictor" contract.
    - Mutate ``row``. The caller shares the dict; predictors are
      read-only consumers.
    """

    provider: str = "abstract"
    model_identifier: str = "abstract"

    @abstractmethod
    def predict(self, row: Mapping[str, Any]) -> float:
        ...

    @abstractmethod
    def describe(self) -> str:
        """One-line human-readable description for audit /
        registry display, e.g.
        ``huggingface:distilbert-base-uncased@v1.0``.
        """
        ...

    # --- Convenience -----------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self.provider!r}, "
            f"model_identifier={self.model_identifier!r})"
        )
