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
import requests  # type: ignore  # for mypy if types-requests is missing

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

# Session management for current directory tracking
current_directories = {}  # session_id -> current_directory

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
    
    def log(self, level: str, message: str, request_id: Optional[str] = None, **kwargs):
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

def get_current_directory(session_id=None):
    """Get the current directory for a session, defaulting to workspace root."""
    if session_id and session_id in current_directories:
        return current_directories[session_id]
    return os.path.abspath('.')

def save_chat_history():
    """Save chat history to file."""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"chat_{timestamp}.md"
        filepath = os.path.join(CHATS_DIR, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            for msg in assistant.messages:
                role = msg['role']
                content = msg['content']
                f.write(f"## {role.upper()}\n\n{content}\n\n---\n\n")
        
        logger.log("info", f"Chat history saved to {filepath}")
    except Exception as e:
        logger.log("error", f"Failed to save chat history: {e}")

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
            "content": """You are an AI coding assistant with access to file system tools. 
            You can read, write, and modify files, search through code, and execute commands.
            Always be helpful, precise, and follow best practices.
            When working with files, be aware of the current working directory context."""
        }]
        self.current_directory = os.path.abspath('.')
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
    
    def set_current_directory(self, directory):
        """Set the current working directory for this assistant instance."""
        self.current_directory = os.path.abspath(directory)
    
    def get_current_directory(self):
        """Get the current working directory."""
        return self.current_directory

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
        """List files in the specified directory."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(directory):
                directory = os.path.join(self.current_directory, directory)
            
            files = os.listdir(directory)
            return {"files": files, "directory": directory}
        except Exception as e:
            return {"error": str(e)}

    def read_file(self, filename, start_line=None, end_line=None):
        """Read a file with optional line range."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(filename):
                filename = os.path.join(self.current_directory, filename)
            
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if start_line is not None or end_line is not None:
                lines = content.splitlines()
                start = start_line - 1 if start_line else 0
                end = end_line if end_line else len(lines)
                content = '\n'.join(lines[start:end])
            
            return {"content": content, "filename": filename}
        except Exception as e:
            return {"error": str(e)}

    def write_file(self, filename, content):
        """Write content to a file."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(filename):
                filename = os.path.join(self.current_directory, filename)
            
            # Create backup before writing
            if os.path.exists(filename):
                backup_path = os.path.join(BACKUP_DIR, f"{os.path.basename(filename)}.{int(time.time())}.bak")
                with open(filename, 'r', encoding='utf-8') as f:
                    with open(backup_path, 'w', encoding='utf-8') as bf:
                        bf.write(f.read())
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            return {"success": True, "filename": filename}
        except Exception as e:
            return {"error": str(e)}

    def delete_file(self, filename):
        """Delete a file."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(filename):
                filename = os.path.join(self.current_directory, filename)
            
            # Create backup before deleting
            if os.path.exists(filename):
                backup_path = os.path.join(BACKUP_DIR, f"{os.path.basename(filename)}.{int(time.time())}.bak")
                with open(filename, 'r', encoding='utf-8') as f:
                    with open(backup_path, 'w', encoding='utf-8') as bf:
                        bf.write(f.read())
            
            os.remove(filename)
            return {"success": True, "filename": filename}
        except Exception as e:
            return {"error": str(e)}

    def create_directory(self, directory_name):
        """Create a new directory."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(directory_name):
                directory_name = os.path.join(self.current_directory, directory_name)
            
            os.makedirs(directory_name, exist_ok=True)
            return {"success": True, "directory": directory_name}
        except Exception as e:
            return {"error": str(e)}

    def insert_at_line(self, filename, code_to_insert, line_number):
        """Insert code at a specific line number."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(filename):
                filename = os.path.join(self.current_directory, filename)
            
            with open(filename, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Create backup
            backup_path = os.path.join(BACKUP_DIR, f"{os.path.basename(filename)}.{int(time.time())}.bak")
            with open(filename, 'r', encoding='utf-8') as f:
                with open(backup_path, 'w', encoding='utf-8') as bf:
                    bf.write(f.read())
            
            # Insert at line (1-indexed to 0-indexed)
            lines.insert(line_number - 1, code_to_insert + '\n')
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            return {"success": True, "filename": filename, "line": line_number}
        except Exception as e:
            return {"error": str(e)}

    def replace_code(self, filename, old_code, new_code):
        """Replace specific code in a file."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(filename):
                filename = os.path.join(self.current_directory, filename)
            
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Create backup
            backup_path = os.path.join(BACKUP_DIR, f"{os.path.basename(filename)}.{int(time.time())}.bak")
            with open(backup_path, 'w', encoding='utf-8') as bf:
                bf.write(content)
            
            # Replace the code
            new_content = content.replace(old_code, new_code)
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            return {"success": True, "filename": filename}
        except Exception as e:
            return {"error": str(e)}

    def search_files(self, pattern, directory=".", file_pattern=None):
        """Search for text in files."""
        try:
            # Use current directory as base if relative path
            if not os.path.isabs(directory):
                directory = os.path.join(self.current_directory, directory)
            
            results = []
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file_pattern and not file.endswith(file_pattern):
                        continue
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if pattern.lower() in content.lower():
                                results.append(filepath)
                    except:
                        continue
            return {"results": results, "pattern": pattern}
        except Exception as e:
            return {"error": str(e)}

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
    return send_from_directory('static', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    response = send_from_directory('static', filename)
    # Add cache control headers to prevent caching
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/metrics')
def metrics():
    return Response(prometheus_client.generate_latest(), mimetype='text/plain')

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/api/test-tree', methods=['GET'])
@log_request
def api_test_tree():
    """Test endpoint to verify tree API format."""
    return jsonify({
        'message': 'Tree API test',
        'tree': [
            {'name': 'test.txt', 'type': 'file', 'path': '/test.txt'},
            {'name': 'testdir', 'type': 'directory', 'path': '/testdir'}
        ],
        'current_path': '/workspace',
        'parent_path': None
    })

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

    request_id = getattr(request, 'request_id', 'unknown')

    def generate():
        for chunk in process_user_message_stream(user_text, request_id):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')

def process_user_message(user_text: str) -> dict:
    """Process user message and return response."""
    with assistant_lock:
        assistant.messages.append({"role": "user", "content": user_text})
        
        # Add current directory context to the system message
        current_dir = assistant.get_current_directory()
        context_message = f"Current working directory: {current_dir}\n\nUser request: {user_text}"
        
        try:
            response = assistant._execute_model_call()
            assistant.messages.append({"role": "assistant", "content": response})
            
            # Save chat history
            save_chat_history()
            
            return {"reply": response}
        except Exception as e:
            error_msg = f"Error processing message: {str(e)}"
            logger.log("error", error_msg)
            return {"error": error_msg}

def process_user_message_stream(user_text: str, request_id: str):
    """Process user message with streaming response."""
    with assistant_lock:
        assistant.messages.append({"role": "user", "content": user_text})
        
        # Add current directory context to the system message
        current_dir = assistant.get_current_directory()
        context_message = f"Current working directory: {current_dir}\n\nUser request: {user_text}"
        
        try:
            for chunk in assistant._execute_model_call_stream():
                yield chunk
        except Exception as e:
            error_msg = f"Error processing message: {str(e)}"
            logger.log("error", error_msg, request_id)
            yield {"error": error_msg}

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
    """Get project tree structure with navigation support."""
    path = request.args.get('path', None)
    session_id = request.args.get('session_id', 'default')
    
    try:
        # Use session's current directory if no path specified
        if path is None:
            path = get_current_directory(session_id)
        else:
            # Ensure path is within allowed bounds
            if not os.path.isabs(path):
                path = os.path.abspath(path)
            
            # Security: prevent directory traversal attacks
            if '..' in path or path.startswith('/'):
                path = os.path.abspath('.')
            
            # Update current directory for this session
            current_directories[session_id] = path
        
        tree = []
        try:
            items = os.listdir(path)
            # Sort: directories first, then files
            dirs = sorted([item for item in items if os.path.isdir(os.path.join(path, item))])
            files = sorted([item for item in items if os.path.isfile(os.path.join(path, item))])
            
            for item in dirs:
                tree.append({
                    'name': item,
                    'type': 'directory',
                    'path': os.path.join(path, item)
                })
            
            for item in files:
                tree.append({
                    'name': item,
                    'type': 'file',
                    'path': os.path.join(path, item)
                })
        except PermissionError:
            tree.append({'error': 'Permission denied'})
        
        return jsonify({
            'tree': tree,
            'current_path': path,
            'parent_path': os.path.dirname(path) if path != os.path.abspath('.') else None
        })
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
        # Security: prevent directory traversal attacks
        if '..' in filename or filename.startswith('/'):
            return jsonify({'error': 'Invalid path'}), 400
        
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'content': content, 'filename': filename})
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/current_directory', methods=['GET'])
@log_request
def api_current_directory():
    """Get the current directory for a session."""
    session_id = request.args.get('session_id', 'default')
    current_dir = get_current_directory(session_id)
    return jsonify({'current_directory': current_dir})

@app.route('/api/change_directory', methods=['POST'])
@log_request
def api_change_directory():
    """Change the current directory for a session."""
    data = request.get_json(force=True)
    session_id = data.get('session_id', 'default')
    new_directory = data.get('directory', '.')
    
    try:
        if not os.path.isabs(new_directory):
            new_directory = os.path.abspath(new_directory)
        
        # Security: prevent directory traversal attacks but allow workspace navigation
        workspace_root = os.path.abspath('.')
        if '..' in new_directory or not new_directory.startswith(workspace_root):
            return jsonify({'error': 'Invalid directory path'}), 400
        
        if not os.path.exists(new_directory):
            return jsonify({'error': 'Directory does not exist'}), 404
        
        if not os.path.isdir(new_directory):
            return jsonify({'error': 'Path is not a directory'}), 400
        
        current_directories[session_id] = new_directory
        
        # Update assistant's current directory if it exists
        if hasattr(app, 'assistant'):
            app.assistant.set_current_directory(new_directory)
        
        return jsonify({
            'success': True,
            'current_directory': new_directory
        })
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