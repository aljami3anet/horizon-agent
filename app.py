import os
import re
import time
import json
import difflib
import uuid
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, Response, stream_template
from flask_cors import CORS
from threading import Lock
import prometheus_client
from prometheus_client import Counter, Histogram, Gauge
import redis
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Prometheus metrics
REQUEST_COUNT = Counter('ai_assistant_requests_total', 'Total requests', ['endpoint', 'status'])
REQUEST_LATENCY = Histogram('ai_assistant_request_duration_seconds', 'Request latency', ['endpoint'])
MODEL_CALL_LATENCY = Histogram('ai_assistant_model_call_duration_seconds', 'Model call latency', ['model'])
TOOL_CALL_SUCCESS = Counter('ai_assistant_tool_calls_total', 'Tool call success/failure', ['tool_name', 'status'])
JSON_PARSE_FAILURES = Counter('ai_assistant_json_parse_failures_total', 'JSON parse failures')
ACTIVE_SESSIONS = Gauge('ai_assistant_active_sessions', 'Active user sessions')

app = Flask(__name__, static_url_path='', static_folder='static')
CORS(app)

# Configuration
CHATS_DIR = 'chats'
WORKSPACE_DIR = 'workspace'
KNOWLEDGE_DIR = 'knowledge'
BACKUP_DIR = 'backups'
os.makedirs(CHATS_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# Redis for rate limiting and session management
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Circuit breaker for model calls
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
    
    def call(self, func, *args, **kwargs):
        if self.state == 'OPEN':
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = 'HALF_OPEN'
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            if self.state == 'HALF_OPEN':
                self.state = 'CLOSED'
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = 'OPEN'
            raise e

# Structured logging with request correlation
class StructuredLogger:
    def __init__(self):
        self.log_file = 'logs/app.log'
        os.makedirs('logs', exist_ok=True)
    
    def log(self, level: str, message: str, request_id: str = None, **kwargs):
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': level,
            'message': message,
            'request_id': request_id,
            **kwargs
        }
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

logger = StructuredLogger()

# Request correlation middleware
def correlate_request():
    request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())
    request.request_id = request_id
    return request_id

def log_request(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        request_id = correlate_request()
        start_time = time.time()
        
        try:
            response = f(*args, **kwargs)
            duration = time.time() - start_time
            REQUEST_COUNT.labels(endpoint=f.__name__, status='success').inc()
            REQUEST_LATENCY.labels(endpoint=f.__name__).observe(duration)
            logger.log('INFO', f'Request completed', request_id, 
                      endpoint=f.__name__, duration=duration)
            return response
        except Exception as e:
            duration = time.time() - start_time
            REQUEST_COUNT.labels(endpoint=f.__name__, status='error').inc()
            REQUEST_LATENCY.labels(endpoint=f.__name__).observe(duration)
            logger.log('ERROR', f'Request failed: {str(e)}', request_id,
                      endpoint=f.__name__, duration=duration, error=str(e))
            raise
    return decorated_function

# Enhanced AI Assistant with multi-model orchestration
class EnhancedAIAssistant:
    def __init__(self):
        self.messages = [{
            "role": "system",
            "content": """You are an expert AI programmer and universal code assistant. Your goal is to help users by writing and editing code in any language. Follow these rules strictly:

1. **Match Indentation Style**: When editing a file, you MUST detect and match the existing indentation style.
2. **Use Precise Tools**: To add new code, use `insert_at_line` with a specific line number. To modify existing code, use `replace_code` by first reading the exact block to be replaced.
3. **Write Clean Code**: Generate clean, readable, and idiomatic code appropriate for the language you are writing.
4. **Complete Tasks**: Fulfill the user's request step-by-step. If you need to read a file first to understand the context, do so.
5. **Constitutional Rules**: Never touch files in the following patterns without explicit user permission:
   - System files (/, /etc, /usr, /var)
   - Hidden files (.git, .env, .config)
   - Backup files (*.bak, *.backup, *.old)
   - Lock files (*.lock, package-lock.json, yarn.lock)
   - Database files (*.db, *.sqlite)
   - Log files (*.log)
   - Temporary files (*.tmp, *.temp)

When you need to use a tool, respond with a JSON object inside a ```json code block:
```json
{
  "tool_call": {
    "name": "<tool_name>",
    "arguments": {
      "<arg_name>": "<arg_value>"
    }
  }
}
```"""
        }]
        self.tools = self._get_tools_definition()
        self.available_functions = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "delete_file": self.delete_file,
            "insert_at_line": self.insert_at_line,
            "replace_code": self.replace_code,
            "create_directory": self.create_directory,
            "search_files": self.search_files,
            "run_command": self.run_command,
        }
        self.active_model_list = [
            'openrouter/horizon-beta',
            'openrouter/anthropic/claude-3.5-sonnet',
            'openrouter/meta-llama/llama-3.1-8b-instruct',
        ]
        self.last_request_time = 0
        self.request_interval = 16
        self.circuit_breaker = CircuitBreaker()
        self.session_memory = {}
    
    def _get_tools_definition(self):
        return [
            {"type": "function", "function": {"name": "list_files", "description": "Lists all files in the current directory.", "parameters": {"type": "object", "properties": {"directory": {"type": "string", "description": "Directory to list files from"}}, "required": []}}},
            {"type": "function", "function": {"name": "read_file", "description": "Reads the content of a specified file, optionally from a start line to an end line.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "The name of the file to read."}, "start_line": {"type": "integer", "description": "Optional. The line number to start reading from."}, "end_line": {"type": "integer", "description": "Optional. The line number to stop reading at."}}, "required": ["filename"]}}},
            {"type": "function", "function": {"name": "write_file", "description": "Creates or overwrites a file with new content. If content is a dictionary or list, it's saved as a JSON file.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "The name of the file to write to."}, "content": {"type": "any", "description": "The content to write into the file (can be a string, or a JSON object/dict)."}}, "required": ["filename", "content"]}}},
            {"type": "function", "function": {"name": "delete_file", "description": "Deletes a specified file from the directory.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "The name of the file to delete."}}, "required": ["filename"]}}},
            {"type": "function", "function": {"name": "create_directory", "description": "Creates a new directory (folder). If the directory already exists, it will do nothing and report success. To create nested directories, provide the full path (e.g., 'parent/child').", "parameters": {"type": "object", "properties": {"directory_name": {"type": "string", "description": "The name or path of the directory to create."}}, "required": ["directory_name"]}}},
            {"type": "function", "function": {"name": "insert_at_line", "description": "Inserts a block of code at a specific line number. This is the preferred way to add new code.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "The name of the file to modify."}, "code_to_insert": {"type": "string", "description": "The block of code to add."}, "line_number": {"type": "integer", "description": "The line number at which to insert the code."}}, "required": ["filename", "code_to_insert", "line_number"]}}},
            {"type": "function", "function": {"name": "replace_code", "description": "Replaces an *exact* block of existing code with a new block. To use this effectively, first `read_file` to copy the precise `old_code` block you want to replace. The `new_code` will be automatically indented to match the old code's level.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "The name of the file to modify."}, "old_code": {"type": "string", "description": "The exact string or code block to be replaced."}, "new_code": {"type": "string", "description": "The new string or code block to replace the old one."}}, "required": ["filename", "old_code", "new_code"]}}},
            {"type": "function", "function": {"name": "search_files", "description": "Search for text in files using grep-like functionality with optional regex support.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string", "description": "The search pattern (supports regex)."}, "directory": {"type": "string", "description": "Directory to search in (default: current directory)."}, "file_pattern": {"type": "string", "description": "File pattern to search in (e.g., '*.py', '*.js')."}}, "required": ["pattern"]}}},
            {"type": "function", "function": {"name": "run_command", "description": "Run a command in a sandboxed environment. Only safe commands are allowed.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The command to run."}}, "required": ["command"]}}},
        ]

    def _get_indentation(self, s: str) -> str:
        match = re.match(r'^(\s*)', s)
        return match.group(1) if match else ""

    def list_files(self, directory="."):
        try:
            files = [f for f in os.listdir(directory) if f != "__pycache__"]
            if not files: 
                return "The directory is empty."
            return "Files in the current directory:\n" + "\n".join(files)
        except Exception as e: 
            return f"Error listing files: {e}"

    def read_file(self, filename, start_line=None, end_line=None):
        try:
            with open(filename, 'r', encoding='utf-8') as f: 
                lines = f.readlines()
            if start_line is None and end_line is None:
                content = "".join(lines)
                return f"Content of '{filename}':\n---\n{content}\n---"
            start_index = (int(start_line) - 1) if start_line else 0
            end_index = int(end_line) if end_line else len(lines)
            if start_index < 0: 
                start_index = 0
            selected_lines = lines[start_index:end_index]
            content = "".join(selected_lines)
            return f"Content of '{filename}' from line {start_line or 1} to {end_line or len(lines)}:\n---\n{content}\n---"
        except FileNotFoundError: 
            return f"Error: File '{filename}' not found."
        except Exception as e: 
            return f"Error reading file '{filename}': {e}"

    def write_file(self, filename, content):
        try:
            # Create backup before writing
            if os.path.exists(filename):
                backup_path = os.path.join(BACKUP_DIR, f"{filename}_{int(time.time())}.bak")
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                with open(filename, 'r', encoding='utf-8') as src:
                    with open(backup_path, 'w', encoding='utf-8') as dst:
                        dst.write(src.read())
            
            if isinstance(content, (dict, list)):
                content = json.dumps(content, indent=4)
            with open(filename, 'w', encoding='utf-8') as f: 
                f.write(content)
            TOOL_CALL_SUCCESS.labels(tool_name='write_file', status='success').inc()
            return f"Successfully wrote content to '{filename}'."
        except Exception as e: 
            TOOL_CALL_SUCCESS.labels(tool_name='write_file', status='error').inc()
            return f"Error writing to file '{filename}': {e}"

    def delete_file(self, filename):
        try:
            # Create backup before deleting
            if os.path.exists(filename):
                backup_path = os.path.join(BACKUP_DIR, f"{filename}_{int(time.time())}.bak")
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                with open(filename, 'r', encoding='utf-8') as src:
                    with open(backup_path, 'w', encoding='utf-8') as dst:
                        dst.write(src.read())
            
            os.remove(filename)
            TOOL_CALL_SUCCESS.labels(tool_name='delete_file', status='success').inc()
            return f"Successfully deleted file '{filename}'."
        except FileNotFoundError: 
            TOOL_CALL_SUCCESS.labels(tool_name='delete_file', status='error').inc()
            return f"Error: File '{filename}' not found for deletion."
        except Exception as e: 
            TOOL_CALL_SUCCESS.labels(tool_name='delete_file', status='error').inc()
            return f"Error deleting file '{filename}': {e}"

    def create_directory(self, directory_name):
        try:
            os.makedirs(directory_name, exist_ok=True)
            TOOL_CALL_SUCCESS.labels(tool_name='create_directory', status='success').inc()
            return f"Successfully created directory '{directory_name}'."
        except Exception as e:
            TOOL_CALL_SUCCESS.labels(tool_name='create_directory', status='error').inc()
            return f"Error creating directory '{directory_name}': {e}"

    def insert_at_line(self, filename, code_to_insert, line_number):
        try:
            with open(filename, 'r', encoding='utf-8') as f: 
                lines = f.readlines()
            target_line = int(line_number)
            target_index = target_line - 1
            if not (0 <= target_index <= len(lines)): 
                return f"Error: Line number {target_line} is out of bounds for file '{filename}' which has {len(lines)} lines."
            base_indent = self._get_indentation(lines[target_index]) if target_index < len(lines) else ""
            indented_code_lines = [f"{base_indent}{L}\n" for L in code_to_insert.splitlines()]
            lines[target_index:target_index] = indented_code_lines
            with open(filename, 'w', encoding='utf-8') as f: 
                f.writelines(lines)
            TOOL_CALL_SUCCESS.labels(tool_name='insert_at_line', status='success').inc()
            return f"Successfully inserted code at line {target_line} in '{filename}'."
        except FileNotFoundError: 
            TOOL_CALL_SUCCESS.labels(tool_name='insert_at_line', status='error').inc()
            return f"Error: File '{filename}' not found."
        except ValueError: 
            TOOL_CALL_SUCCESS.labels(tool_name='insert_at_line', status='error').inc()
            return f"Error: 'line_number' must be an integer."
        except Exception as e: 
            TOOL_CALL_SUCCESS.labels(tool_name='insert_at_line', status='error').inc()
            return f"Error inserting code into '{filename}': {e}"

    def replace_code(self, filename, old_code, new_code):
        try:
            with open(filename, 'r', encoding='utf-8') as f: 
                content = f.read()
            if old_code not in content: 
                return f"Error: The specified 'old_code' was not found in '{filename}'. It must be an exact match."
            first_line_of_old_code = old_code.splitlines()[0]
            base_indent = self._get_indentation(first_line_of_old_code)
            indented_new_code_lines = [f"{base_indent}{L}" for L in new_code.splitlines()]
            indented_new_code = "\n".join(indented_new_code_lines)
            new_content = content.replace(old_code, indented_new_code)
            with open(filename, 'w', encoding='utf-8') as f: 
                f.write(new_content)
            TOOL_CALL_SUCCESS.labels(tool_name='replace_code', status='success').inc()
            return f"Successfully replaced code in '{filename}'."
        except FileNotFoundError: 
            TOOL_CALL_SUCCESS.labels(tool_name='replace_code', status='error').inc()
            return f"Error: File '{filename}' not found."
        except Exception as e: 
            TOOL_CALL_SUCCESS.labels(tool_name='replace_code', status='error').inc()
            return f"Error replacing code in '{filename}': {e}"

    def search_files(self, pattern, directory=".", file_pattern=None):
        try:
            import fnmatch
            results = []
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file_pattern and not fnmatch.fnmatch(file, file_pattern):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if re.search(pattern, content, re.IGNORECASE):
                                results.append(f"Found in {file_path}")
                    except:
                        continue
            if results:
                return "Search results:\n" + "\n".join(results)
            else:
                return "No matches found."
        except Exception as e:
            return f"Error searching files: {e}"

    def run_command(self, command):
        # Sandboxed command execution - only allow safe commands
        safe_commands = ['python', 'pip', 'npm', 'node', 'git', 'ls', 'cat', 'head', 'tail']
        if not any(cmd in command.lower() for cmd in safe_commands):
            return "Error: Command not allowed for security reasons."
        
        try:
            import subprocess
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return f"Command executed successfully:\n{result.stdout}"
            else:
                return f"Command failed:\n{result.stderr}"
        except Exception as e:
            return f"Error executing command: {e}"

    def _execute_model_call(self, model_name=None):
        start_time = time.time()
        
        # Rate limiting
        while True:
            elapsed_time = time.time() - self.last_request_time
            if elapsed_time < self.request_interval:
                wait_time = self.request_interval - elapsed_time
                time.sleep(wait_time)
            break

        self.last_request_time = time.time()
        
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None, "OPENROUTER_API_KEY environment variable not set."

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Model selection logic
        if model_name:
            models_to_try = [model_name] + [m for m in self.active_model_list if m != model_name]
        else:
            models_to_try = self.active_model_list

        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": self._build_prompt()}
                    ],
                    "stream": True  # Enable streaming
                }
                
                response = self.circuit_breaker.call(
                    lambda: requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        stream=True
                    )
                )
                
                if response.status_code != 200:
                    continue
                
                duration = time.time() - start_time
                MODEL_CALL_LATENCY.labels(model=model).observe(duration)
                
                return response, None
                
            except Exception as e:
                logger.log('ERROR', f'Model {model} failed', getattr(request, 'request_id', 'unknown'), 
                          model=model, error=str(e))
                continue

        return None, "All models failed"

    def _build_prompt(self):
        system_prompt = self.messages[0]['content']
        conversation_history = "\n".join(
            [f"**{msg['role'].capitalize()}**: {msg['content']}" for msg in self.messages[1:]]
        )
        tools_definition = json.dumps([tool['function'] for tool in self.tools], indent=2)
        
        return f"""**System Prompt:**
{system_prompt}

**Available Tools:**
```json
{tools_definition}
```

**Conversation History:**
{conversation_history}

**Your Task:**
Based on the conversation, provide a direct answer or call a tool if necessary. When you need to use a tool, respond with a JSON object inside a ```json code block.
"""

# Global assistant instance
assistant = EnhancedAIAssistant()
assistant_lock = Lock()

# Routes
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/upgrade')
def serve_upgrade():
    return send_from_directory('upgrade/ui', 'upgrade_index.html')

@app.route('/metrics')
def metrics():
    return Response(prometheus_client.generate_latest(), mimetype='text/plain')

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/api/chat', methods=['POST'])
@log_request
def api_chat():
    data = request.get_json(force=True)
    user_text = data.get('message', '').strip()
    if not user_text:
        return jsonify({'error': 'Empty message'}), 400

    response_data = process_user_message(user_text)
    return jsonify(response_data)

@app.route('/api/chat/stream', methods=['POST'])
@log_request
def api_chat_stream():
    data = request.get_json(force=True)
    user_text = data.get('message', '').strip()
    if not user_text:
        return jsonify({'error': 'Empty message'}), 400

    def generate():
        for chunk in process_user_message_stream(user_text):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')

def process_user_message(user_text: str) -> dict:
    """Process user message with enhanced error handling and logging."""
    request_id = getattr(request, 'request_id', 'unknown')
    
    with assistant_lock:
        assistant.messages.append({"role": "user", "content": user_text})
        
        response_message, error = assistant._execute_model_call()
        if error:
            logger.log('ERROR', f'Model call failed: {error}', request_id)
            return {"error": error}

        # Process streaming response
        full_response = ""
        tool_call_found = None
        
        for line in response_message.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data)
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            if 'content' in delta:
                                full_response += delta['content']
                    except json.JSONDecodeError:
                        JSON_PARSE_FAILURES.inc()
                        continue

        # Try to extract tool call
        try:
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', full_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                parsed_json = json.loads(json_str)
                if parsed_json and "tool_call" in parsed_json:
                    tool_call_found = parsed_json['tool_call']
        except (json.JSONDecodeError, AttributeError):
            JSON_PARSE_FAILURES.inc()

        assistant.messages.append({'role': 'assistant', 'content': full_response})
        
        if tool_call_found:
            return handle_tool_call(tool_call_found, request_id)
        else:
            return {'reply': full_response}

def process_user_message_stream(user_text: str):
    """Process user message with streaming response."""
    request_id = getattr(request, 'request_id', 'unknown')
    
    with assistant_lock:
        assistant.messages.append({"role": "user", "content": user_text})
        
        response_message, error = assistant._execute_model_call()
        if error:
            logger.log('ERROR', f'Model call failed: {error}', request_id)
            yield {"error": error}
            return

        full_response = ""
        tool_call_found = None
        
        for line in response_message.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data)
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            if 'content' in delta:
                                content = delta['content']
                                full_response += content
                                yield {"type": "content", "content": content}
                    except json.JSONDecodeError:
                        JSON_PARSE_FAILURES.inc()
                        continue

        # Try to extract tool call
        try:
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', full_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                parsed_json = json.loads(json_str)
                if parsed_json and "tool_call" in parsed_json:
                    tool_call_found = parsed_json['tool_call']
        except (json.JSONDecodeError, AttributeError):
            JSON_PARSE_FAILURES.inc()

        assistant.messages.append({'role': 'assistant', 'content': full_response})
        
        if tool_call_found:
            yield {"type": "tool_call", "tool_call": tool_call_found}
        else:
            yield {"type": "complete", "reply": full_response}

def handle_tool_call(tool_call: dict, request_id: str) -> dict:
    """Handle tool call with enhanced validation and logging."""
    tool_name = tool_call.get('name')
    tool_args = tool_call.get('arguments', {})
    
    logger.log('INFO', f'Tool call requested', request_id, tool_name=tool_name, arguments=tool_args)
    
    # Check if tool is dangerous
    dangerous_tools = {"write_file", "delete_file", "create_directory", "replace_code", "insert_at_line"}
    if tool_name in dangerous_tools:
        return {
            'action_request': {
                'name': tool_name,
                'args': tool_args
            }
        }
    
    # Execute safe tool
    function_to_call = assistant.available_functions.get(tool_name)
    if not function_to_call:
        return {"error": f"Tool '{tool_name}' not found."}
    
    try:
        result = function_to_call(**tool_args)
        assistant.messages.append({"role": "tool", "name": tool_name, "content": result})
        
        # Get AI's response after tool execution
        response_message, error = assistant._execute_model_call()
        if error:
            return {"error": error}
        
        full_response = ""
        for line in response_message.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data)
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            if 'content' in delta:
                                full_response += delta['content']
                    except json.JSONDecodeError:
                        continue
        
        assistant.messages.append({'role': 'assistant', 'content': full_response})
        return {'reply': full_response}
        
    except Exception as e:
        logger.log('ERROR', f'Tool execution failed', request_id, tool_name=tool_name, error=str(e))
        return {"error": f"Error executing tool {tool_name}: {e}"}

@app.route('/api/execute_action', methods=['POST'])
@log_request
def api_execute_action():
    data = request.get_json(force=True)
    tool_name = data.get('name')
    tool_args = data.get('args')

    if not tool_name:
        return jsonify({'error': 'Missing tool name'}), 400

    with assistant_lock:
        logger.log('INFO', f'User confirmed execution', getattr(request, 'request_id', 'unknown'), 
                  tool_name=tool_name, arguments=tool_args)
        
        function_to_call = assistant.available_functions.get(tool_name)
        if not function_to_call:
            result = f"Error: Tool '{tool_name}' not found."
        else:
            try:
                result = function_to_call(**tool_args)
            except Exception as e:
                result = f"Error executing tool {tool_name}: {e}"

        assistant.messages.append({"role": "tool", "name": tool_name, "content": result})

        # Get AI's response after tool execution
        response_message, error = assistant._execute_model_call()
        if error:
            return jsonify({"error": error})

        full_response = ""
        for line in response_message.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data)
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            if 'content' in delta:
                                full_response += delta['content']
                    except json.JSONDecodeError:
                        continue

        assistant.messages.append({'role': 'assistant', 'content': full_response})
        return jsonify({'reply': full_response})

@app.route('/api/preview_replace_diff', methods=['POST'])
@log_request
def api_preview_replace_diff():
    data = request.get_json(force=True) or {}
    filename = data.get('filename')
    old_code = data.get('old_code', '')
    new_code = data.get('new_code', '')
    
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if old_code is None or new_code is None:
        return jsonify({'error': 'old_code and new_code are required'}), 400
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            original = f.read()
    except FileNotFoundError:
        return jsonify({'error': f"File '{filename}' not found."}), 404
    
    # Diff 1: old_code vs new_code
    old_lines = old_code.splitlines(keepends=False)
    new_lines = new_code.splitlines(keepends=False)
    snippet_diff = difflib.unified_diff(old_lines, new_lines, fromfile='old_code', tofile='new_code', lineterm='')
    
    # Diff 2: original file vs preview with replacement applied
    try:
        would_be = original.replace(old_code, new_code)
    except Exception:
        would_be = original
    
    orig_lines = original.splitlines(keepends=False)
    would_lines = would_be.splitlines(keepends=False)
    file_diff = difflib.unified_diff(orig_lines, would_lines, fromfile=filename + ':original', tofile=filename + ':preview', lineterm='')
    
    return jsonify({
        'ok': True,
        'snippet_diff': '\n'.join(list(snippet_diff)),
        'file_diff': '\n'.join(list(file_diff))
    })

@app.route('/api/preview_write_diff', methods=['POST'])
@log_request
def api_preview_write_diff():
    data = request.get_json(force=True) or {}
    filename = data.get('filename')
    content = data.get('content', '')
    
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if content is None:
        return jsonify({'error': 'content is required'}), 400
    
    original = ''
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            original = f.read()
    except FileNotFoundError:
        # Treat as creating a new file; original stays empty
        original = ''
    
    orig_lines = original.splitlines(keepends=False)
    new_lines = (content if isinstance(content, str) else json.dumps(content, indent=2)).splitlines(keepends=False)
    file_diff = difflib.unified_diff(orig_lines, new_lines, fromfile=filename + ':original', tofile=filename + ':new', lineterm='')
    
    return jsonify({
        'ok': True,
        'file_diff': '\n'.join(list(file_diff))
    })

@app.route('/api/tree', methods=['GET'])
@log_request
def api_tree():
    """Get project tree structure."""
    path = request.args.get('path', '.')
    try:
        tree = []
        for root, dirs, files in os.walk(path):
            level = root.replace(path, '').count(os.sep)
            indent = ' ' * 2 * level
            tree.append(f'{indent}{os.path.basename(root)}/')
            subindent = ' ' * 2 * (level + 1)
            for file in files:
                tree.append(f'{subindent}{file}')
        return jsonify({'tree': '\n'.join(tree)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/file', methods=['GET'])
@log_request
def api_file():
    """Get file content."""
    filename = request.args.get('path')
    if not filename:
        return jsonify({'error': 'path parameter required'}), 400
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'content': content})
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chats', methods=['GET'])
@log_request
def list_chats():
    files = []
    for fname in sorted(os.listdir(CHATS_DIR)):
        if fname.lower().endswith('.md'):
            files.append(fname)
    return jsonify({'files': files})

@app.route('/api/chats/<path:filename>', methods=['GET'])
@log_request
def get_chat(filename):
    safe = os.path.basename(filename)
    path = os.path.join(CHATS_DIR, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    return jsonify({'filename': safe, 'content': content})

@app.route('/api/save_chat', methods=['POST'])
@log_request
def save_chat():
    data = request.get_json(force=True)
    md_content = data.get('markdown', '').strip()
    if not md_content:
        return jsonify({'error': 'No markdown content provided'}), 400

    ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    fname = f"chat_{ts}.md"
    path = os.path.join(CHATS_DIR, fname)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    return jsonify({'ok': True, 'filename': fname})

if __name__ == '__main__':
    # Reset message history on start
    assistant.messages = [assistant.messages[0]] 
    port = int(os.getenv('PORT', '5051'))
    app.run(host='0.0.0.0', port=port, debug=False)