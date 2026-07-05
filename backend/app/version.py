"""Single source of the running backend version (from package metadata)."""

from importlib.metadata import PackageNotFoundError, version

try:
    APP_VERSION = version("lycosa-backend")
except PackageNotFoundError:  # running from a bare source tree
    APP_VERSION = "0.0.0+dev"
