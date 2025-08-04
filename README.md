# AI Coder Agent - Enhanced Edition

A production-grade AI coding assistant with advanced features including streaming responses, rich diff previews, project tree exploration, and comprehensive monitoring.

## 🚀 Features

### Core Functionality
- **Multi-Model Orchestration**: Automatic fallback between different AI models
- **Streaming Responses**: Real-time token streaming with Server-Sent Events (SSE)
- **Rich Diff Previews**: Beautiful side-by-side diffs using diff2html
- **Project Tree Explorer**: Browse and preview files directly in the UI
- **Command Palette**: Quick actions with Ctrl+K shortcut

### Safety & Reliability
- **Circuit Breaker**: Automatic failure detection and recovery
- **Constitutional Rules**: Hard constraints to prevent dangerous operations
- **Automatic Backups**: File operations create backups before changes
- **Structured Logging**: Request correlation and audit trails
- **JSON Auto-Repair**: Robust handling of malformed tool-call JSON

### Monitoring & Observability
- **Prometheus Metrics**: Comprehensive metrics for monitoring
- **Health Checks**: Built-in health and readiness endpoints
- **Structured Logs**: JSON-formatted logs with request correlation
- **Grafana Dashboards**: Pre-configured monitoring dashboards

### Developer Experience
- **Type Safety**: Gradual typing with mypy
- **Code Quality**: Automated linting with flake8/ruff
- **Comprehensive Testing**: pytest suite with coverage reporting
- **CI/CD Pipeline**: GitHub Actions for automated testing and deployment

## 📦 Installation

### Prerequisites
- Python 3.11+
- Redis (optional, for rate limiting)
- OpenRouter API key

### Quick Start

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd ai-coder-agent
   ```

2. **Set up environment**
   ```bash
   cp .env.example .env
   # Edit .env with your OpenRouter API key
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   python app.py
   ```

5. **Access the application**
   - Main UI: http://localhost:5051
   - Metrics: http://localhost:5051/metrics
   - Health: http://localhost:5051/health

### Docker Deployment

1. **Using docker-compose (recommended)**
   ```bash
   docker-compose up -d
   ```

2. **Using Docker directly**
   ```bash
   docker build -t ai-coder-agent .
   docker run -p 5051:5051 -e OPENROUTER_API_KEY=your_key docker-user/docker-image:latest
   ```

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENROUTER_API_KEY` | Your OpenRouter API key | Required |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |
| `PORT` | Application port | `5051` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | Circuit breaker threshold | `5` |
| `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | Recovery timeout (seconds) | `60` |

### Model Configuration

The application supports multiple AI models with automatic fallback:

```python
active_model_list = [
    'openrouter/horizon-beta',
    'openrouter/anthropic/claude-3.5-sonnet',
    'openrouter/meta-llama/llama-3.1-8b-instruct',
]
```

## 🛠️ Development

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=app --cov-report=html

# Run specific test file
pytest tests/test_json_parsing.py -v
```

### Code Quality

```bash
# Linting
flake8 . --max-line-length=127

# Type checking
mypy app.py --ignore-missing-imports

# Security checks
bandit -r .
safety check
```

### Pre-commit Hooks

```bash
# Install pre-commit
pip install pre-commit
pre-commit install

# Run manually
pre-commit run --all-files
```

## 📊 Monitoring

### Prometheus Metrics

The application exposes comprehensive metrics at `/metrics`:

- `ai_assistant_requests_total`: Total requests by endpoint and status
- `ai_assistant_request_duration_seconds`: Request latency
- `ai_assistant_model_call_duration_seconds`: Model call latency
- `ai_assistant_tool_calls_total`: Tool call success/failure rates
- `ai_assistant_json_parse_failures_total`: JSON parsing failures

### Grafana Dashboards

Access Grafana at http://localhost:3000 (admin/admin) for pre-configured dashboards:

- Request latency and throughput
- Model performance metrics
- Tool call success rates
- Error rates and circuit breaker status

## 🔒 Security Features

### Constitutional Rules

The AI assistant follows strict safety rules:

- **System Files**: Never touch `/`, `/etc`, `/usr`, `/var`
- **Hidden Files**: Never touch `.git`, `.env`, `.config`
- **Backup Files**: Never touch `*.bak`, `*.backup`, `*.old`
- **Lock Files**: Never touch `*.lock`, `package-lock.json`, `yarn.lock`
- **Database Files**: Never touch `*.db`, `*.sqlite`
- **Log Files**: Never touch `*.log`
- **Temporary Files**: Never touch `*.tmp`, `*.temp`

### File Operations Safety

- **Automatic Backups**: All file operations create backups
- **Diff Previews**: See changes before confirming
- **Sandboxed Commands**: Only safe commands allowed
- **Request Correlation**: Full audit trail for all operations

## 🎯 Usage Examples

### Basic File Operations

```bash
# List files in current directory
curl -X POST http://localhost:5051/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "List all files in the current directory"}'

# Read a file
curl -X POST http://localhost:5051/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Read the contents of app.py"}'

# Create a new file
curl -X POST http://localhost:5051/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Create a new Python file called hello.py with a simple hello world function"}'
```

### Streaming Responses

```bash
# Use streaming endpoint for real-time responses
curl -X POST http://localhost:5051/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a Python function to calculate fibonacci numbers"}'
```

### Project Exploration

```bash
# Get project tree
curl http://localhost:5051/api/tree

# Get file content
curl "http://localhost:5051/api/file?path=app.py"
```

## 🏗️ Architecture

### Components

- **Flask Application**: Main web server with REST API
- **EnhancedAIAssistant**: Core AI assistant with multi-model support
- **CircuitBreaker**: Failure detection and recovery
- **StructuredLogger**: Request correlation and audit logging
- **Prometheus Metrics**: Comprehensive monitoring
- **Redis**: Rate limiting and session management (optional)

### Data Flow

1. **Request Processing**: User message received via API
2. **Model Selection**: Choose appropriate AI model
3. **Tool Call Parsing**: Extract and validate JSON tool calls
4. **Safety Validation**: Check constitutional rules
5. **Execution**: Execute safe tools or request confirmation
6. **Response**: Stream results back to user
7. **Logging**: Record all operations for audit

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Follow PEP 8 style guidelines
- Add tests for new features
- Update documentation for API changes
- Ensure all tests pass before submitting PR

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [OpenRouter](https://openrouter.ai/) for AI model access
- [diff2html](https://diff2html.rtfpessoa.xyz/) for beautiful diff rendering
- [Prometheus](https://prometheus.io/) for monitoring
- [Grafana](https://grafana.com/) for dashboards

## 🆘 Support

- **Issues**: Report bugs and feature requests on GitHub
- **Documentation**: Check the `/docs` folder for detailed guides
- **Discussions**: Use GitHub Discussions for questions and ideas

---

**Made with ❤️ for developers who want to code faster and safer.**
