from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
import os
import json
import asyncio
from dotenv import load_dotenv
import logging
import re

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

# Define available tools (with argument support)
TOOLS = {
    "get_time": lambda: "The current time is 2025-02-01T08:45:56Z.",
    "calculate_sum": lambda x, y: f"The sum of {x} and {y} is {x + y}."
}

# Generate system prompt describing available tools
def generate_system_prompt():
    tool_descriptions = "\n".join(
        [f"- `{name}`: {func.__doc__ or 'No description available.'}" for name, func in TOOLS.items()]
    )
    return f"""
    You are a helpful assistant with access to external functions.
    You can use the following tools:
    {tool_descriptions}
    When a function is needed, describe its call explicitly in your response, like `calculate_sum(x=3, y=5)` or `get_time()`. DO NOT FORGET THE PARANTHESIS.
    """

async def stream_response(response):
    """ Converts a blocking requests stream into an async generator """
    for chunk in response.iter_content(chunk_size=4096):
        yield chunk

def extract_function_calls(text):
    """ Extracts function calls from a response using regex """
    function_calls = []
    pattern = r"(\w+)(?:\((.*?)\))?"  # Matches function calls with or without parentheses

    for match in re.finditer(pattern, text):
        func_name = match.group(1)
        args_str = match.group(2) if match.group(2) else ""  # Handle missing args

        args = {}
        if args_str:  # Parse arguments only if they exist
            for arg in args_str.split(","):
                key_value = arg.split("=")
                if len(key_value) == 2:
                    key, value = key_value
                    key, value = key.strip(), value.strip().strip('"').strip("'")
                    try:
                        value = eval(value)  # Convert numbers if possible
                    except:
                        pass  # Keep as string if eval fails
                    args[key] = value

        function_calls.append((func_name, args))

    return function_calls

@app.post("/v1/chat/completions")
async def chat(request: Request):
    try:
        # Parse request JSON
        request_data = await request.json()
        logging.info(f"Incoming Request:\n{json.dumps(request_data, indent=2)}")

        # Inject system message
        system_message = {"role": "system", "content": generate_system_prompt()}
        request_data["messages"].insert(0, system_message)

        logging.info(f"Sending Request:\n{json.dumps(request_data, indent=3)}")

        # Forward request to LLM server
        headers = {"Content-Type": "application/json", **AUTH_HEADER}
        response = await asyncio.to_thread(lambda: requests.post(BASE_URL, json=request_data, headers=headers, stream=True))

        async def event_stream():
            collected_message = ""
            response_chunks = []

            async for chunk in stream_response(response):
                chunk_str = chunk.decode()
                response_chunks.append(chunk_str)  # Store the chunk
                chunk_content = chunk_str.split(': ', 1)[-1]
                try:
                    content = json.loads(
                        chunk_content.split(': ', 1)[-1])['choices'][0]['delta']['content']
                except:
                    content = ""

                collected_message += content

            # Extract function calls from response
            function_calls = extract_function_calls(collected_message)
            tool_results = {}

            # Execute each function sequentially
            for func_name, args in function_calls:

                if func_name in TOOLS:
                    try:
                        logging.info(f"Triggering function: {func_name} with args {args}")
                        # 🔥 FIX: Call functions properly even if args are empty
                        tool_results[func_name] = TOOLS[func_name](**args) if args else TOOLS[func_name]()  
                    except Exception as e:
                        logging.error(f"Function execution error for {func_name}: {str(e)}")
                        tool_results[func_name] = f"Error executing function {func_name}"


            if tool_results:
                # Append tool results to conversation
                request_data["messages"].append({"role": "assistant", "content": collected_message})
                for func_name, result in tool_results.items():
                    request_data["messages"].append({"role": "system", "content": f"Herer are tool results;Only you can see these results. Let the user know the results : Tool result ({func_name}): {result}"})

                # Send new request with tool results
                final_response = await asyncio.to_thread(lambda: requests.post(BASE_URL, json=request_data, headers=headers, stream=True))
                # Continue streaming the new response
                async for new_chunk in stream_response(final_response):
                    yield new_chunk.decode()
            else:
                # No functions triggered, return normal response
                for chunk in response_chunks:
                    yield chunk

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
