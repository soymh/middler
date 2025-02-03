import logging

# Ensure logging is configured at module level
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class Tool:
    """Base class for all tools."""

    def execute(self, *args, **kwargs):
        raise NotImplementedError("Each tool must implement the execute method.")
