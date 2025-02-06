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


load_dotenv()


aclient = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url=os.getenv('BASE_URL'))

# Configure logging
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
            spec = importlib.util.spec_from_file_location(tool_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool:
                    TOOLS[tool_name] = attr()  # Instantiate and add to TOOLS
                    logging.info(f"Loaded tool: {tool_name}")

load_tools()  # Load tools on startup

load_dotenv()

app = FastAPI()

# CORS Setup (Fixes 405 OPTIONS issue in Obsidian)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change this to restrict access
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (POST, GET, OPTIONS, etc.)
    allow_headers=["*"],
)

# OpenAI API Configuration
BASE_URL = os.getenv('BASE_URL', 'https://api.together.xyz/v1')

def extract_function_calls(text):
    """Extract function calls from a JSON response instead of regex."""
    try:
        data = json.loads(text)  # Parse the response as JSON
        if isinstance(data, dict) and "function" in data and "args" in data:
            return [(data["function"], data["args"])]
    except json.JSONDecodeError:
        pass
    
    return []

def generate_system_prompt():
    """Generate system message with available tools."""
    tool_descriptions = "\n".join(
        [f"- `{name}`: {func.__doc__ or 'No description available.'}" for name, func in TOOLS.items()]
    )
    return f"""
    You are a helpful assistant with access to external functions.
    If the user request suggests function calling, You MUST return function calls in **valid JSON format** inside a single JSON object.
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
    Also, you can execute a function multiple times, BY RESPONDING AGAIN AND AGAIN WITH THE FUNCTION.
    IF YOU WANT TO  CALL FUNCTIONS, **YOU HAVE TO RESPOND WITH 1 FUNCTION CALL EACH TIME!** ELSE, RESPOND NORMALLY.
    DO NOT RETURN ANY FUNCTION CALL IN PLAIN TEXT OR ALONG A PLAIN TEXT!
    **AVOID UNNECESSARY FUNCTION CALLING!**
    """

async def stream_response(openai_stream):
    async for chunk in openai_stream:
        yield chunk

@app.post("/v1/chat/completions")
async def chat(request: Request):
    try:

        count_loop= 1
        request_data = await request.json()
        logging.info(f"Incoming Request:\n{json.dumps(request_data, indent=2)}")

        # Insert system message
        system_message = {"role": "system", "content": generate_system_prompt()}
        request_data["messages"].insert(0, system_message)

        logging.info(f"Sending Request to OpenAI API:\n{json.dumps(request_data, indent=3)}")

        # Streaming response from OpenAI SDK
        openai_stream = await aclient.chat.completions.create(model=request_data["model"],
        messages=request_data["messages"],
        temperature=request_data.get("temperature", 0.1),
        stream=True)

        # Function execution loop
        max_function_calls = 5  # Prevent infinite loops
        function_execution_rounds = 0

        collected_message = ""
        response_chunks = []

        async for chunk in openai_stream:
            response_chunks.append(chunk)
            content = getattr(chunk.choices[0].delta, "content", "")  # ✅ Access via attributes
            collected_message += content if content else ""

        function_calls = extract_function_calls(collected_message)
        logging.info(f"1st clct msg:{collected_message}\n------ count loop:{function_execution_rounds}------")


        while function_execution_rounds < max_function_calls and function_calls:
            tool_results = {}

            for func_name, args in function_calls:
                if func_name in TOOLS:

                    try:
                        tool_instance = TOOLS[func_name]
                        tool_results[func_name] = tool_instance.execute(**args) if args else tool_instance.execute()
                        logging.info(f"Assistant Called: {func_name} with args {args}, \n------ count loop:{function_execution_rounds}------")

                    except Exception as e:
                        logging.error(f"Function execution error for {func_name}: {str(e)}")
                        tool_results[func_name] = f"Error executing function {func_name}"

                    if tool_results:
                        request_data["messages"].append({"role": "assistant", "content": collected_message})

                        for func_name, result in tool_results.items():
                            request_data["messages"].append({
                                "role": "system",
                                "content": f"""Execution {function_execution_rounds} results:
                                                {func_name} → {result}"""
                            })

                        # Get new response with updated messages
                        function_execution_rounds += 1
                        openai_stream = await aclient.chat.completions.create(model=request_data["model"],
                        messages=request_data["messages"],
                        temperature=request_data.get("temperature", 0.1),
                        stream=True)

                        collected_message = ""
                        response_chunks = []  # Reset collected messages for new function call extraction

                        async for chunk in openai_stream:
                            response_chunks.append(chunk)
                            content = getattr(chunk.choices[0].delta, "content", "")  # ✅ Access via attributes
                            collected_message += content if content else ""
                        logging.info(f"2nd clct msg:{collected_message}\n------ count loop:{function_execution_rounds}------")

                        function_calls = extract_function_calls(collected_message)

                else:
                    break  # No tool results, exit loop

            function_execution_rounds+=1
        return StreamingResponse(stream_response(openai_stream), media_type="text/event-stream")

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
        headers["Authorization"] = f"Bearer {openai.api_key}"

        method = request.method
        body = await request.body()

        response = await openai.request(
            method=method,
            url=target_url,
            headers=headers,
            data=body
        )

        return Response(content=response.content, status_code=response.status_code, headers=dict(response.headers))

    except Exception as e:
        return HTTPException(status_code=500, detail=str(e))

app.include_router(proxy_router, prefix="/v1")
