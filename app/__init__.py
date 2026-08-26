"""EventHub application layer: composition, entrypoint, config, paths."""

# Canonical application version — single source of truth.
# pyproject.toml derives its version from this attribute (setuptools
# dynamic attr); app/server.py imports it. Do not duplicate the literal.
__version__ = "0.3.0-rc.1"
