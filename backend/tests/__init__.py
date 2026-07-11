import os

# Pin secrets before any app import (this package __init__ runs before
# conftest.py) so the first-run auto-generation in app.core.bootstrap never
# fires — and persists no file — during the test suite.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "test-only-admin-password")
