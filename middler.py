from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
import os
import json
import asyncio
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()

# CORS Setup (Fixes 405 OPTIONS issue in Obsidian)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change this to restrict access
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (POST, GET, OPTIONS, etc.)
    allow_headers=["*"],
)

# API credentials
API_KEY = os.getenv('API_KEY')
BASE_URL = os.getenv('BASE_URL', 'https://api.together.xyz/v1')

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}

# Define available tools
TOOLS = {
    "get_time": lambda: "The current time is 2025-02-01T12:34:56Z.",
    "calculate_sum": lambda x, y: f"The sum of {x} and {y} is {x + y}."
}

# Generate system prompt describing available tools
def generate_system_prompt():
    tool_descriptions = "\n".join([f"- `{name}`: {func.__doc__ or 'No description available.'}" for name, func in TOOLS.items()])
    return f"""
    You are a helpful assistant with access to external functions.
    You can use the following tools:
    {tool_descriptions}
    When a function is needed, describe its call explicitly in your response, like `calculate_sum(x=3, y=5)`.
    """

async def stream_response(response):
    """ Converts a blocking requests stream into an async generator """
    for chunk in response.iter_content(chunk_size=512):
        yield chunk

@app.post("/v1/chat/completions")
async def chat(request: Request):
    try:
        # Parse request JSON
        request_data = await request.json()
        logging.info(f"Incoming Request: {json.dumps(request_data, indent=2)}")

        # Inject system message
        system_message = {"role": "system", "content": generate_system_prompt()}
        request_data["messages"].insert(0, system_message)

        # Forward request to LLM server
        headers = {"Content-Type": "application/json", **AUTH_HEADER}
        response = await asyncio.to_thread(lambda: requests.post(BASE_URL, json=request_data, headers=headers, stream=True))

        async def event_stream():
            collected_message = ""
            async for chunk in stream_response(response):
                chunk_str = chunk.decode()
                collected_message += chunk_str
                yield chunk_str

                # Check if any tool is mentioned in the response
                for func_name in TOOLS:
                    if func_name in collected_message:
                        try:
                            logging.info(f"Triggering function: {func_name} with args: {collected_message}")
                            tool_result = eval(f"{collected_message}")  # Execute function call
                        except Exception as e:
                            logging.error(f"Function execution error: {str(e)}")
                            continue  # Skip if parsing fails

                        collected_message = tool_result
                        yield json.dumps({"tool_call": func_name, "result": tool_result}).encode()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
