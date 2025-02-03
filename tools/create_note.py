import os
from datetime import datetime
from tools.tools_base import Tool
import logging

class CreateMarkdownFile(Tool):
    """ A tool that creates a Markdown file from the input text.

    :param content: The text to be saved in the markdown file (MUST be provided). Provide all text at once.
    """

    def execute(self, content: str, directory: str = "~/Documents/obsidian", **kwargs):
        logging.info(f"Received request to create markdown file:\n{content}")  # Log input
        logging.getLogger().handlers[0].flush()  # Force log flush

        # Ensure the target directory exists
        os.makedirs(directory, exist_ok=True)

        # Generate a filename based on timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f"{timestamp}.md"
        file_path = os.path.join(directory, file_name)

        try:
            with open(file_path, "w", encoding="utf-8") as md_file:
                md_file.write(content.replace("\\n", "\n"))  # Ensure proper newlines

            logging.info(f"Markdown file created successfully at {file_path}")
            logging.getLogger().handlers[0].flush()
            return f"Markdown file created successfully at {file_path}"

        except Exception as e:
            logging.error(f"Error creating markdown file: {str(e)}")
            logging.getLogger().handlers[0].flush()
            return f"Error creating markdown file: {str(e)}"
