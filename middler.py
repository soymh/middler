from fastapi import FastAPI, HTTPException, Request, APIRouter, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
import os
import json
import asyncio
from dotenv import load_dotenv
import logging
import re
import importlib.util
from tools.tools_base import Tool

# Configure logging globally
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

TOOLS = {}

def load_tools():
    tools_directory = "tools"
    
    for filename in os.listdir(tools_directory):
        if filename.endswith(".py") and filename != "__init__.py":
            tool_name = filename[:-3]  # Remove .py extension
            module_path = os.path.join(tools_directory, filename)
            
            # Load module dynamically
            spec = importlib.util.spec_from_file_location(tool_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find the tool class
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool:
                    TOOLS[tool_name] = attr()  # Instantiate and add to TOOLS
                    logging.info(f"Loaded tool: {tool_name}")

# Load tools at startup
load_tools()

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

def generate_system_prompt():
    """Generate system message with available tools."""
    tool_descriptions = "\n".join(
        [f"- `{name}`: {func.__doc__ or 'No description available.'}" for name, func in TOOLS.items()]
    )
    return f"""
    You are a helpful assistant with access to external functions.
    You can use the following tools:
    {tool_descriptions}
    When a function is needed, describe its call explicitly in your response, like `calculate_sum(x=3, y=5)` or `get_time()`. DO NOT FORGET THE PARENTHESES.and try NOT to use backticks :`
    """

async def stream_response(response):
    """ Converts a blocking requests stream into an async generator """
    for chunk in response.iter_content(chunk_size=4096):
        yield chunk

def extract_function_calls(text):
    """Extracts function calls from a response while preserving string values."""
    function_calls = []
    pattern = r"(\w+)\((.*?)\)"  # Match function name + anything inside parentheses

    for match in re.finditer(pattern, text):
        func_name = match.group(1)
        args_str = match.group(2)

        args = {}
        if args_str:
            key_value_pairs = re.findall(r"(\w+)\s*=\s*(\".*?\"|'.*?'|\S+)", args_str)
            for key, value in key_value_pairs:
                value = value.strip('"').strip("'")  # Remove extra quotes
                args[key] = value

        function_calls.append((func_name, args))

    logging.info(f"Extracted function calls: {function_calls}")
    return function_calls


@app.post("/v1/chat/completions")
async def chat(request: Request):
    try:
        request_data = await request.json()
        logging.info(f"Incoming Request:\n{json.dumps(request_data, indent=2)}")

        system_message = {"role": "system", "content": generate_system_prompt()}
        request_data["messages"].insert(0, system_message)

        logging.info(f"Sending Request:\n{json.dumps(request_data, indent=3)}")

        headers = {"Content-Type": "application/json", **AUTH_HEADER}
        response = await asyncio.to_thread(lambda: requests.post(BASE_URL + "/chat/completions", json=request_data, headers=headers, stream=True))

        async def event_stream():
            collected_message = ""
            response_chunks = []

            async for chunk in stream_response(response):
                chunk_str = chunk.decode()
                response_chunks.append(chunk_str)
                chunk_content = chunk_str.split(': ', 1)[-1]
                try:
                    content = json.loads(
                        chunk_content.split(': ', 1)[-1])['choices'][0]['delta']['content']
                except:
                    content = ""

                collected_message += content

            logging.info(f"Assistant collected messages : {collected_message}")

            function_calls = extract_function_calls(collected_message)
            tool_results = {}

            for func_name, args in function_calls:
                logging.info(f"Assistant Called : {function_calls}")

                if func_name in TOOLS:
                    try:
                        logging.debug(f"Triggering function: {func_name} with args {args}")
                        tool_instance = TOOLS[func_name]
                        tool_results[func_name] = tool_instance.execute(**args) if args else tool_instance.execute()
                    except Exception as e:
                        logging.error(f"Function execution error for {func_name}: {str(e)}")
                        tool_results[func_name] = f"Error executing function {func_name}"

            if tool_results:
                request_data["messages"].append({"role": "assistant", "content": collected_message})
                
                for func_name, result in tool_results.items():
                    request_data["messages"].append({
                        "role": "system",
                        "content": f"Here are tool results; Only you can see these results. Let the user know the results : {func_name} → {result}"
                    })

                final_response = await asyncio.to_thread(lambda: requests.post(BASE_URL+"/chat/completions", json=request_data, headers=headers, stream=True))
                
                async for new_chunk in stream_response(final_response):
                    yield new_chunk.decode()
            else:
                for chunk in response_chunks:
                    yield chunk

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

proxy_router = APIRouter()

@proxy_router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_request(path: str, request: Request):
    """
    Acts as a transparent proxy, forwarding all requests to BASE_URL.
    """
    try:
        base_url = os.getenv("BASE_URL", "https://api.together.xyz/v1")
        target_url = f"{base_url}/{path}"

        headers = {key: value for key, value in request.headers.items() if key.lower() != "host"}
        headers["Authorization"] = f"Bearer {API_KEY}"

        method = request.method
        body = await request.body()

        response = requests.request(method, target_url, headers=headers, data=body)

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
    
    except Exception as e:
        return HTTPException(status_code=500, detail=str(e))

# ✅ Register the proxy router AFTER defining specific endpoints
app.include_router(proxy_router, prefix="/v1")
