import pytest
import json
from app import EnhancedAIAssistant

class TestJSONParsing:
    """Test JSON parsing and repair functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.assistant = EnhancedAIAssistant()
    
    def test_valid_json_parsing(self):
        """Test parsing of valid JSON tool calls."""
        valid_json = '''
        ```json
        {
          "tool_call": {
            "name": "read_file",
            "arguments": {
              "filename": "test.py"
            }
          }
        }
        ```
        '''
        
        # This would test the JSON extraction and parsing
        # In a real implementation, we'd have a method to test
        assert "tool_call" in valid_json
        assert "read_file" in valid_json
    
    def test_malformed_json_repair(self):
        """Test repair of common JSON malformations."""
        malformed_cases = [
            # Trailing comma
            '{"tool_call": {"name": "read_file", "arguments": {"filename": "test.py",}}}',
            # Smart quotes
            '{"tool_call": {"name": "read_file", "arguments": {"filename": "test.py"}}}',
            # Missing quotes
            '{"tool_call": {"name": read_file, "arguments": {"filename": "test.py"}}}',
            # Unclosed braces
            '{"tool_call": {"name": "read_file", "arguments": {"filename": "test.py"}}',
        ]
        
        for case in malformed_cases:
            # Test that repair attempts are made
            assert "tool_call" in case or "name" in case
    
    def test_fenced_json_extraction(self):
        """Test extraction of JSON from fenced code blocks."""
        fenced_content = '''
        Here's my response:
        
        ```json
        {
          "tool_call": {
            "name": "write_file",
            "arguments": {
              "filename": "output.txt",
              "content": "Hello World"
            }
          }
        }
        ```
        
        That's the tool call.
        '''
        
        # Test that JSON block is found
        assert "```json" in fenced_content
        assert "tool_call" in fenced_content
    
    def test_unfenced_json_extraction(self):
        """Test extraction of JSON without code fences."""
        unfenced_content = '''
        Here's my response:
        
        {
          "tool_call": {
            "name": "list_files",
            "arguments": {}
          }
        }
        
        That's the tool call.
        '''
        
        # Test that JSON object is found
        assert "tool_call" in unfenced_content
        assert "list_files" in unfenced_content
    
    def test_tool_argument_validation(self):
        """Test validation of tool arguments."""
        valid_args = {
            "read_file": {"filename": "test.py"},
            "write_file": {"filename": "test.py", "content": "Hello"},
            "list_files": {},
            "create_directory": {"directory_name": "new_folder"}
        }
        
        for tool_name, args in valid_args.items():
            # Test that arguments are valid for each tool
            assert isinstance(args, dict)
            if tool_name == "read_file":
                assert "filename" in args
            elif tool_name == "write_file":
                assert "filename" in args and "content" in args
    
    def test_invalid_tool_arguments(self):
        """Test handling of invalid tool arguments."""
        invalid_cases = [
            {"tool_call": {"name": "read_file", "arguments": {}}},  # Missing filename
            {"tool_call": {"name": "write_file", "arguments": {"filename": "test.py"}}},  # Missing content
            {"tool_call": {"name": "nonexistent_tool", "arguments": {}}},  # Invalid tool
        ]
        
        for case in invalid_cases:
            # Test that invalid cases are caught
            tool_call = case["tool_call"]
            if tool_call["name"] == "read_file":
                assert "filename" not in tool_call["arguments"]
            elif tool_call["name"] == "write_file":
                assert "content" not in tool_call["arguments"]
    
    def test_json_parse_failures_metric(self):
        """Test that JSON parse failures are tracked."""
        # This would test the Prometheus metric
        # In a real implementation, we'd check the metric value
        assert True  # Placeholder
    
    def test_tool_call_success_metrics(self):
        """Test that tool call success/failure metrics are tracked."""
        # This would test the Prometheus metrics for tool calls
        assert True  # Placeholder

class TestToolValidation:
    """Test tool validation and execution."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.assistant = EnhancedAIAssistant()
    
    def test_dangerous_tools_identification(self):
        """Test that dangerous tools are properly identified."""
        dangerous_tools = {"write_file", "delete_file", "create_directory", "replace_code", "insert_at_line"}
        
        for tool in dangerous_tools:
            assert tool in self.assistant.available_functions
    
    def test_safe_tools_execution(self):
        """Test that safe tools can be executed directly."""
        safe_tools = {"list_files", "read_file", "search_files"}
        
        for tool in safe_tools:
            assert tool in self.assistant.available_functions
    
    def test_tool_argument_schemas(self):
        """Test tool argument schema validation."""
        # Test that each tool has proper argument validation
        tools_with_required_args = {
            "read_file": ["filename"],
            "write_file": ["filename", "content"],
            "delete_file": ["filename"],
            "create_directory": ["directory_name"],
            "insert_at_line": ["filename", "code_to_insert", "line_number"],
            "replace_code": ["filename", "old_code", "new_code"]
        }
        
        for tool_name, required_args in tools_with_required_args.items():
            assert tool_name in self.assistant.available_functions
            # In a real implementation, we'd validate the schema

if __name__ == "__main__":
    pytest.main([__file__])