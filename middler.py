from fastapi import FastAPI, HTTPException, Request, APIRouter, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
import os
import json
import asyncio
from dotenv import load_dotenv
import logging
import importlib.util
from tools.tools_base import Tool

# Load environment variables
load_dotenv()

# Initialize OpenAI async client with your API key and base URL
aclient = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url=os.getenv('BASE_URL'))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Global dictionary for tools
TOOLS = {}

def load_tools():
    """Dynamically load tools from the 'tools' directory."""
    tools_directory = "tools"
    for filename in os.listdir(tools_directory):
        if filename.endswith(".py") and filename != "__init__.py":
            tool_name = filename[:-3]  # Remove .py extension
            module_path = os.path.join(tools_directory, filename)
            spec = importlib.util.spec_from_file_location(tool_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool:
                    TOOLS[tool_name] = attr()  # Instantiate and add to TOOLS
                    logging.info(f"Loaded tool: {tool_name}")

load_tools()  # Load tools on startup

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust to your needs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenAI API configuration
BASE_URL = os.getenv('BASE_URL', 'https://api.together.xyz/v1')

def extract_function_calls(text):
    """
    Extract function calls from a JSON-formatted string.
    (Assumes that the assistant returns a valid JSON object if function calls are present.)
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "function" in data and "args" in data:
            return [(data["function"], data["args"])]
    except json.JSONDecodeError:
        pass
    return []

def generate_system_prompt():
    """Generate a system message that describes available tools."""
    tool_descriptions = "\n".join(
        [f"- `{name}`: {func.__doc__ or 'No description available.'}" for name, func in TOOLS.items()]
    )
    return f"""
    You are a helpful assistant with access to external functions.
    If the user request suggests function calling, you MUST return function calls in **valid JSON format** inside a single JSON object.
    Available tools:
    {tool_descriptions}
    Example of valid function call:
    {{
        "function": "create_note",
        "args": {{
            "content": "Quadratic Equations explained",
            "name": "math_notes.md"
        }}
    }}
    Also, you can execute a function multiple times—respond with one function call at a time.
    DO NOT RETURN ANY FUNCTION CALL IN PLAIN TEXT!
    **AVOID UNNECESSARY FUNCTION CALLING!**
    """

# The new event_stream function: it streams each chunk immediately while accumulating text.
async def event_stream(request_data):
    max_function_calls = 5  # Limit to avoid infinite loops
    function_execution_rounds = 0

    # Start the initial streaming request
    openai_stream = await aclient.chat.completions.create(
        model=request_data["model"],
        messages=request_data["messages"],
        temperature=request_data.get("temperature", 0.1),
        stream=True
    )

    collected_message = ""
    
    # Stream and yield chunks as they arrive
    async for chunk in openai_stream:
        # Immediately yield each chunk to the client in SSE format
        # (We use json.dumps to serialize the chunk, you can adjust as needed.)
        yield f"data: {json.dumps(chunk.model_dump(), separators=(',', ':'))}\n\n"
        # Accumulate the textual content for function-call checking
        content = getattr(chunk.choices[0].delta, "content", "")
        collected_message += content

    logging.info(f"Initial collected message: {collected_message}\n-- loop {function_execution_rounds} --")

    # Check for function calls in the collected message
    while function_execution_rounds < max_function_calls:
        function_calls = extract_function_calls(collected_message)
        if not function_calls:
            break  # No function call found, exit loop

        # Execute each function call found
        tool_results = {}
        for func_name, args in function_calls:
            if func_name in TOOLS:
                logging.info(f"Executing function: {func_name} with args {args} (loop {function_execution_rounds})")
                try:
                    tool_results[func_name] = TOOLS[func_name].execute(**args) if args else TOOLS[func_name].execute()
                except Exception as e:
                    logging.error(f"Function execution error for {func_name}: {str(e)}")
                    tool_results[func_name] = f"Error executing function {func_name}"

        # Append the function results into the conversation
        request_data["messages"].append({"role": "assistant", "content": collected_message})
        for func_name, result in tool_results.items():
            request_data["messages"].append({
                "role": "system",
                "content": f"Execution {function_execution_rounds} results:\n{func_name} → {result}"
            })

        function_execution_rounds += 1

        # Start a new streaming request with the updated messages
        openai_stream = await aclient.chat.completions.create(
            model=request_data["model"],
            messages=request_data["messages"],
            temperature=request_data.get("temperature", 0.1),
            stream=True
        )
        # Reset the accumulator
        collected_message = ""
        # Stream and yield chunks from the new request immediately
        async for chunk in openai_stream:
            yield f"data: {json.dumps(chunk.model_dump(), separators=(',', ':'))}\n\n"
            content = getattr(chunk.choices[0].delta, "content", "")
            collected_message += content

        logging.info(f"Collected message after loop {function_execution_rounds}: {collected_message}")

    # End of event_stream; nothing further to yield.

@app.post("/v1/chat/completions")
async def chat(request: Request):
    try:
        request_data = await request.json()
        logging.info(f"Incoming Request:\n{json.dumps(request_data, indent=2)}")

        # Insert system prompt at the beginning
        system_message = {"role": "system", "content": generate_system_prompt()}
        request_data["messages"].insert(0, system_message)

        logging.info(f"Sending Request to OpenAI API:\n{json.dumps(request_data, indent=3)}")

        # Return a streaming response using our event_stream function
        return StreamingResponse(event_stream(request_data), media_type="text/event-stream")

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


proxy_router = APIRouter()

@proxy_router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_request(path: str, request: Request):
    """Acts as a transparent proxy, forwarding all requests to BASE_URL."""
    try:
        target_url = f"{BASE_URL}/{path}"
        headers = {key: value for key, value in request.headers.items() if key.lower() != "host"}
        headers["Authorization"] = f"Bearer {aclient.api_key}"  # Using aclient's API key

        method = request.method
        body = await request.body()

        # Using the OpenAI SDK's request method (if available) or fallback to requests
        response = await asyncio.to_thread(lambda: requests.request(method, target_url, headers=headers, data=body))
        return Response(content=response.content, status_code=response.status_code, headers=dict(response.headers))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(proxy_router, prefix="/v1")
