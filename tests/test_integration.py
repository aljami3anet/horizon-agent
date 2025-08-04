import pytest
import json
import tempfile
import os
from app import app

class TestFlaskEndpoints:
    """Integration tests for Flask application endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create a test client for the Flask app."""
        os.environ['OPENROUTER_API_KEY'] = 'test-key'  # Set dummy key for tests
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_health_endpoint(self, client):
        """Test the health check endpoint."""
        response = client.get('/health')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'healthy'
        assert 'timestamp' in data
    
    def test_metrics_endpoint(self, client):
        """Test the Prometheus metrics endpoint."""
        response = client.get('/metrics')
        assert response.status_code == 200
        assert 'ai_assistant_requests_total' in response.data.decode()
    
    def test_chat_endpoint(self, client):
        """Test the chat endpoint."""
        response = client.post('/api/chat', 
                             json={'message': 'Hello, can you list files?'})
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should either have a reply or an action_request
        assert 'reply' in data or 'action_request' in data or 'error' in data
    
    @pytest.mark.skipif(os.environ.get("CI") == "true", reason="Skip streaming test in CI")
    def test_chat_stream_endpoint(self, client):
        """Test the streaming chat endpoint."""
        response = client.post('/api/chat/stream', 
                             json={'message': 'Hello'})
        assert response.status_code == 200
        assert response.headers['Content-Type'] == 'text/event-stream'
    
    def test_preview_replace_diff_endpoint(self, client):
        """Test the diff preview endpoint for replace operations."""
        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('Hello World\nThis is a test file\n')
            temp_file = f.name
        
        try:
            response = client.post('/api/preview_replace_diff', 
                                 json={
                                     'filename': temp_file,
                                     'old_code': 'Hello World',
                                     'new_code': 'Hello Universe'
                                 })
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['ok'] == True
            assert 'file_diff' in data
        finally:
            os.unlink(temp_file)
    
    def test_preview_write_diff_endpoint(self, client):
        """Test the diff preview endpoint for write operations."""
        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('Original content\n')
            temp_file = f.name
        
        try:
            response = client.post('/api/preview_write_diff', 
                                 json={
                                     'filename': temp_file,
                                     'content': 'New content\n'
                                 })
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['ok'] == True
            assert 'file_diff' in data
        finally:
            os.unlink(temp_file)
    
    def test_tree_endpoint(self, client):
        """Test the project tree endpoint."""
        response = client.get('/api/tree')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'tree' in data
    
    def test_file_endpoint(self, client):
        """Test the file content endpoint."""
        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('Test file content\n')
            temp_file = f.name
        
        try:
            response = client.get(f'/api/file?path={temp_file}')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert 'content' in data
            assert 'Test file content' in data['content']
        finally:
            os.unlink(temp_file)
    
    def test_file_endpoint_not_found(self, client):
        """Test the file endpoint with non-existent file."""
        response = client.get('/api/file?path=nonexistent.txt')
        assert response.status_code == 404
    
    def test_execute_action_endpoint(self, client):
        """Test the execute action endpoint."""
        response = client.post('/api/execute_action', 
                             json={
                                 'name': 'list_files',
                                 'args': {}
                             })
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'reply' in data or 'error' in data
    
    def test_save_chat_endpoint(self, client):
        """Test the save chat endpoint."""
        response = client.post('/api/save_chat', 
                             json={
                                 'markdown': '# Test Chat\n\n## User\nHello\n\n## Assistant\nHi there!'
                             })
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['ok'] == True
        assert 'filename' in data
    
    def test_list_chats_endpoint(self, client):
        """Test the list chats endpoint."""
        response = client.get('/api/chats')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'files' in data
        assert isinstance(data['files'], list)

class TestErrorHandling:
    """Test error handling in endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create a test client for the Flask app."""
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_empty_message(self, client):
        """Test handling of empty messages."""
        response = client.post('/api/chat', json={'message': ''})
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
    
    def test_missing_message(self, client):
        """Test handling of missing message field."""
        response = client.post('/api/chat', json={})
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
    
    def test_invalid_json(self, client):
        """Test handling of invalid JSON."""
        response = client.post('/api/chat', 
                             data='invalid json',
                             content_type='application/json')
        assert response.status_code == 400
    
    def test_missing_tool_name(self, client):
        """Test handling of missing tool name in execute action."""
        response = client.post('/api/execute_action', 
                             json={'args': {}})
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data

class TestCircuitBreaker:
    """Test circuit breaker functionality."""
    
    def test_circuit_breaker_initial_state(self):
        """Test that circuit breaker starts in CLOSED state."""
        from app import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.state == 'CLOSED'
        assert cb.failure_count == 0
    
    def test_circuit_breaker_failure_threshold(self):
        """Test that circuit breaker opens after failure threshold."""
        from app import CircuitBreaker
        
        def failing_function():
            raise Exception("Test failure")
        
        cb = CircuitBreaker(failure_threshold=2)
        
        # First failure
        try:
            cb.call(failing_function)
        except:
            pass
        assert cb.state == 'CLOSED'
        assert cb.failure_count == 1
        
        # Second failure - should open circuit
        try:
            cb.call(failing_function)
        except:
            pass
        assert cb.state == 'OPEN'
        assert cb.failure_count == 2

if __name__ == "__main__":
    pytest.main([__file__])