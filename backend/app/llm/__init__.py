"""Universal LLM layer (ADR-031).

Callers build an `LLMRequest`, the gateway resolves a provider account and
model, and an adapter translates to the provider's official HTTP API. Nothing
above the adapter layer sees a provider payload, SDK or credential.
"""
