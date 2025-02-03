from tools.tools_base import Tool  # Corrected import

class CalculateSum(Tool):
    """ A tool that adds two numbers. """

    def execute(self, x: int, y: int, **kwargs):
        return f"The sum of {x} and {y} is {x + y}."
