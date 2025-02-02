from fastapi import FastAPI, HTTPException, Request
import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

API_KEY = os.environ['API_KEY']
BASE_URL = os.getenv('BASE_URL', 'https://api.together.xyz/v1')

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}  # Replace with your actual API key

get_time = lambda: "The current time is 2025-02-01T12:34:56Z."
print(get_time())

# Define tools (prompt-based approach)
TOOLS = {
    "get_time": lambda: "The current time is 2025-02-01T12:34:56Z.",
    "calculate_sum": lambda x, y: f"The sum of {x} and {y} is {x + y}."
}

# Create a dynamic system prompt describing available tools
def generate_system_prompt():
    tool_descriptions = "\n".join([f"- `{name}`: {func.__doc__ or 'No description available.'}" for name, func in TOOLS.items()])
    return f"""
    You are a helpful assistant with access to external functions.
    You can use the following tools:
    {tool_descriptions}
    When a function is needed, describe its call explicitly in your response.only one word of calling it, like : `calculate_sum(x=3, y=5)`
    """

@app.post("/v1/chat/completions")
async def chat(request: Request):
    try:
        request_data = await request.json()

        # Inject system message with tool descriptions
        system_message = {"role": "system", "content": generate_system_prompt()}
        request_data["messages"].insert(0, system_message)

        # Forward request to OpenWebUI LLM
        headers = {"Content-Type": "application/json", **AUTH_HEADER}
        response = requests.post(BASE_URL, json=request_data, headers=headers)
        response_data = response.json()

        # Check if LLM suggests a function call in its response
        if response_data["choices"]:
            assistant_message = response_data["choices"][0]["message"]["content"]

            # Check if any tool is mentioned in the response
            for func_name in TOOLS:
                if func_name in assistant_message:
                    try:
                        tool_result = eval(f"{assistant_message}")  # Convert to proper data type
                    except Exception:
                        continue  # Skip if parsing fails

                    assistant_message = tool_result

                    # Update the response
                    response_data["choices"][0]["message"]["content"] = assistant_message

        return response_data  # Return updated response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
