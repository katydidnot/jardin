"""
backend/core — cross-cutting infrastructure
============================================

Modules
-------
logging     Structlog + stdlib integration, JSON output in production.
tracing     Per-run node tracer; writes JSON traces to traces/ directory.
resilience  @with_retry decorator for Anthropic API calls.
"""
