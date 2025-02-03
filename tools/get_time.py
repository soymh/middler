from tools.tools_base import Tool  # Corrected import
from datetime import datetime

class GetTime(Tool):
    """ A tool that returns the current time. """

    def execute(self, **kwargs):
        return f"The current time is {datetime.utcnow().isoformat()}Z."
