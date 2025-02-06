import os
from datetime import datetime
from tools.tools_base import Tool
import logging
import os

class CreateMarkdownFile(Tool):
    """ A tool that creates a Markdown file from the input text.You can create multiple N notes by calling this funciton for N times.
        BUT YOU HAVE TO CALL IT ONCE EACH TIME!
    :param content: The text to be saved in the markdown file (MUST be provided). Provide all text at once.
    :param name: The name for the file to be saved for the markdown file (MUST be provided).default is the date.
    """

    def execute(self, content: str,name: str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), directory: str = f"{os.getenv("HOME")}/Documents/obsidian", **kwargs):
        logging.info(f"Received request to create markdown file:\n{content}")  # Log input
        logging.getLogger().handlers[0].flush()  # Force log flush

        # Ensure the target directory exists
        os.makedirs(directory, exist_ok=True)

        file_name = f"{name}"
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
