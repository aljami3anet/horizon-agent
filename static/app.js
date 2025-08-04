const chatContainer = document.getElementById('chatContainer');
const input = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const saveChatBtn = document.getElementById('saveChatBtn');
const newChatBtn = document.getElementById('newChatBtn');
const chatList = document.getElementById('chatList');
const projectTree = document.getElementById('projectTree');
const refreshTree = document.getElementById('refreshTree');
const diffContainer = document.getElementById('diffContainer');
const diffContent = document.getElementById('diffContent');
const closeDiffBtn = document.getElementById('closeDiff');
const commandPalette = document.getElementById('commandPalette');
const commandInput = document.getElementById('commandInput');
const commandResults = document.getElementById('commandResults');
const fileModal = document.getElementById('fileModal');
const fileModalTitle = document.getElementById('fileModalTitle');
const fileModalContent = document.getElementById('fileModalContent');
const closeFileModal = document.getElementById('closeFileModal');

marked.setOptions({
  breaks: true,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return hljs.highlightAuto(code).value;
  }
});

// Command palette commands
const commands = [
  { name: 'Read File', action: 'read', description: 'Read a file from the project' },
  { name: 'Search Files', action: 'search', description: 'Search for text in files' },
  { name: 'Create File', action: 'create', description: 'Create a new file' },
  { name: 'List Files', action: 'list', description: 'List files in directory' },
  { name: 'Run Tests', action: 'test', description: 'Run project tests' },
  { name: 'Format Code', action: 'format', description: 'Format code files' }
];

function createMessage(role, text) {
  const wrapper = document.createElement('div');
  wrapper.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? '🧑' : '🤖';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const content = document.createElement('div');
  content.className = 'content';
  content.innerHTML = marked.parse(text || '');

  bubble.appendChild(content);
  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);

  chatContainer.appendChild(wrapper);
  enhanceCodeBlocks(content);
  chatContainer.scrollTop = chatContainer.scrollHeight;
  return wrapper;
}

function createStreamingMessage() {
  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = '🤖';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const content = document.createElement('div');
  content.className = 'content';

  bubble.appendChild(content);
  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);

  chatContainer.appendChild(wrapper);
  chatContainer.scrollTop = chatContainer.scrollHeight;
  return content;
}

function createConfirmationPrompt(action) {
  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = '🤖';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const content = document.createElement('div');
  content.className = 'content';
  
  // Debug: Log the action object to see its structure
  console.log('Action object:', action);
  
  // Fix: Handle both possible structures
  const toolName = action.name || action.tool_name;
  const toolArgs = action.args || action.arguments || {};
  const formattedArgs = JSON.stringify(toolArgs, null, 2);
  
  content.innerHTML = `
    <p>I am about to perform the following action:</p>
    <pre><code class="language-json hljs">${toolName}(${formattedArgs})</code></pre>
    <p>Do you want to proceed?</p>
  `;

  const buttonGroup = document.createElement('div');
  buttonGroup.className = 'confirmation-buttons';

  const confirmBtn = document.createElement('button');
  confirmBtn.className = 'btn btn-primary';
  confirmBtn.textContent = 'Confirm';
  confirmBtn.onclick = () => {
    buttonGroup.remove();
    // Use the correct structure for the API call
    executeConfirmedAction({
      name: toolName,
      args: toolArgs
    });
  };

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => {
    buttonGroup.remove();
    createMessage('assistant', "Action cancelled by user.");
  };

  const diffBtn = document.createElement('button');
  diffBtn.className = 'btn';
  diffBtn.textContent = 'Preview Diff';
  diffBtn.onclick = async () => {
    try {
      let resp;
      if (toolName === 'replace_code') {
        resp = await fetch('/api/preview_replace_diff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: toolArgs.filename,
            old_code: toolArgs.old_code,
            new_code: toolArgs.new_code,
          })
        });
        const data = await resp.json();
        if (data.ok) {
          showDiff(data.file_diff);
        } else {
          showDiff(`Error: ${data.error || 'Failed to generate replace diff'}`);
        }
      } else if (toolName === 'write_file') {
        resp = await fetch('/api/preview_write_diff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: toolArgs.filename,
            content: toolArgs.content,
          })
        });
        const data = await resp.json();
        if (data.ok) {
          showDiff(data.file_diff);
        } else {
          showDiff(`Error: ${data.error || 'Failed to generate write diff'}`);
        }
      } else {
        showDiff('Diff preview is available for replace_code and write_file only.');
      }
    } catch (e) {
      showDiff(`Network error while previewing diff: ${e.message}`);
    }
  };

  buttonGroup.appendChild(confirmBtn);
  buttonGroup.appendChild(cancelBtn);
  buttonGroup.appendChild(diffBtn);
  content.appendChild(buttonGroup);

  bubble.appendChild(content);
  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  chatContainer.appendChild(wrapper);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function showThinking(show = true) {
  let pending = document.getElementById('pending-indicator');
  if (show) {
    if (!pending) {
      pending = document.createElement('div');
      pending.id = 'pending-indicator';
      pending.className = 'message assistant';
      pending.innerHTML = '<div class="avatar">🤖</div><div class="bubble"><div class="content">Thinking…</div></div>';
      chatContainer.appendChild(pending);
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }
  } else {
    if (pending) {
      chatContainer.removeChild(pending);
    }
  }
}

function showDiff(diffText) {
  // Convert diff text to diff2html format
  const diffHtml = Diff2Html.html(diffText, {
    drawFileList: false,
    matching: 'lines',
    outputFormat: 'side-by-side'
  });
  
  diffContent.innerHTML = diffHtml;
  diffContainer.classList.remove('hidden');
}

function showFileModal(filename, content) {
  fileModalTitle.textContent = filename;
  fileModalContent.textContent = content;
  fileModal.classList.remove('hidden');
}

async function executeConfirmedAction(action) {
  showThinking(true);
  try {
    const res = await fetch('/api/execute_action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action)
    });
    const data = await res.json();
    handleApiResponse(data);
  } catch(e) {
    createMessage('assistant', `Network error while executing action: ${e.message}`);
  } finally {
    showThinking(false);
  }
}

function handleApiResponse(data) {
  if (data.error) {
    createMessage('assistant', `Error: ${data.error}`);
  }
  if (data.reply) {
    createMessage('assistant', data.reply);
  }
  if (data.action_request) {
    createConfirmationPrompt(data.action_request);
  }
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  createMessage('user', text);
  input.value = '';

  showThinking(true);

  try {
    // Try streaming first
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });

    if (res.ok) {
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let streamingContent = createStreamingMessage();
      let fullResponse = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') break;

            try {
              const parsed = JSON.parse(data);
              if (parsed.type === 'content') {
                fullResponse += parsed.content;
                streamingContent.innerHTML = marked.parse(fullResponse);
                enhanceCodeBlocks(streamingContent);
                chatContainer.scrollTop = chatContainer.scrollHeight;
              } else if (parsed.type === 'tool_call') {
                handleApiResponse({ action_request: parsed.tool_call });
                break;
              } else if (parsed.type === 'complete') {
                handleApiResponse({ reply: parsed.reply });
                break;
              }
            } catch (e) {
              // Ignore parse errors
            }
          }
        }
      }
    } else {
      // Fallback to non-streaming
      const res2 = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      const data = await res2.json();
      handleApiResponse(data);
    }
  } catch (e) {
    createMessage('assistant', 'Network error. Is the backend running on port 5051?');
  } finally {
    showThinking(false);
  }
}

async function loadProjectTree() {
  try {
    const sessionId = 'default'; // You can generate unique session IDs if needed
    const timestamp = Date.now(); // Cache busting
    const version = 'v2'; // Version parameter to force cache refresh
    const url = `/api/tree?session_id=${sessionId}&t=${timestamp}&v=${version}`;
    
    console.log('Fetching tree from:', url);
    console.log('Current time:', new Date().toISOString());
    
    const res = await fetch(url, {
      method: 'GET',
      headers: {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
      }
    });
    
    console.log('Response status:', res.status);
    console.log('Response headers:', res.headers);
    
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }
    
    const data = await res.json();
    
    console.log('Raw API response:', data);
    console.log('Type of data:', typeof data);
    console.log('Type of data.tree:', typeof data.tree);
    console.log('Is data.tree an array?', Array.isArray(data.tree));
    console.log('data.tree value:', data.tree);
    
    if (data.tree && Array.isArray(data.tree)) {
      console.log('Processing tree with', data.tree.length, 'items');
      projectTree.innerHTML = '';
      
      // Add current path display
      const pathDisplay = document.createElement('div');
      pathDisplay.className = 'current-path';
      pathDisplay.innerHTML = `<strong>📁 ${data.current_path}</strong>`;
      projectTree.appendChild(pathDisplay);
      
      // Add parent directory link if not at root
      if (data.parent_path) {
        const parentLink = document.createElement('div');
        parentLink.className = 'tree-item folder parent-link';
        parentLink.innerHTML = '📁 .. (Parent Directory)';
        parentLink.onclick = () => navigateToDirectory(data.parent_path);
        projectTree.appendChild(parentLink);
      }
      
      // Add directories and files
      data.tree.forEach((item, index) => {
        console.log(`Processing item ${index}:`, item);
        const treeItem = document.createElement('div');
        treeItem.className = 'tree-item';
        
        if (item.type === 'directory') {
          treeItem.classList.add('folder');
          treeItem.innerHTML = `📁 ${item.name}`;
          treeItem.onclick = () => navigateToDirectory(item.path);
        } else {
          treeItem.classList.add('file');
          treeItem.innerHTML = `📄 ${item.name}`;
          treeItem.onclick = () => openFilePreview(item.path);
        }
        
        projectTree.appendChild(treeItem);
      });
      
      console.log('Tree loaded successfully');
    } else {
      console.error('Invalid tree data:', data);
      console.error('data.tree type:', typeof data.tree);
      console.error('data.tree value:', data.tree);
      projectTree.innerHTML = '<div class="error">Invalid tree data received</div>';
    }
  } catch (e) {
    console.error('Failed to load project tree:', e);
    console.error('Error details:', e.message, e.stack);
    projectTree.innerHTML = '<div class="error">Failed to load project tree</div>';
  }
}

async function navigateToDirectory(path) {
  try {
    const sessionId = 'default';
    const res = await fetch('/api/change_directory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        directory: path
      })
    });
    
    const data = await res.json();
    if (data.success) {
      // Reload the tree with the new directory
      await loadProjectTree();
      
      // Update the AI assistant's awareness
      updateAIAssistantContext();
    } else {
      console.error('Failed to change directory:', data.error);
    }
  } catch (e) {
    console.error('Error navigating to directory:', e);
  }
}

async function updateAIAssistantContext() {
  try {
    const sessionId = 'default';
    const res = await fetch(`/api/current_directory?session_id=${sessionId}`);
    const data = await res.json();
    
    // Add a system message to inform the AI about the directory change
    const contextMessage = `Current working directory changed to: ${data.current_directory}`;
    createMessage('system', contextMessage);
  } catch (e) {
    console.error('Failed to update AI context:', e);
  }
}

async function openFilePreview(filename) {
  try {
    const res = await fetch(`/api/file?path=${encodeURIComponent(filename)}`);
    const data = await res.json();
    if (data.content) {
      showFileModal(filename, data.content);
    } else {
      alert('Failed to load file content');
    }
  } catch (e) {
    alert('Error loading file');
  }
}

function showCommandPalette() {
  commandPalette.classList.remove('hidden');
  commandInput.focus();
  commandInput.value = '';
  commandResults.innerHTML = '';
}

function hideCommandPalette() {
  commandPalette.classList.add('hidden');
}

function filterCommands(query) {
  return commands.filter(cmd => 
    cmd.name.toLowerCase().includes(query.toLowerCase()) ||
    cmd.description.toLowerCase().includes(query.toLowerCase())
  );
}

function updateCommandResults(query) {
  commandResults.innerHTML = '';
  if (!query) return;

  const filtered = filterCommands(query);
  filtered.forEach(cmd => {
    const item = document.createElement('div');
    item.className = 'command-result';
    item.innerHTML = `
      <div><strong>${cmd.name}</strong></div>
      <div style="color: var(--muted); font-size: 12px;">${cmd.description}</div>
    `;
    item.onclick = () => {
      hideCommandPalette();
      input.value = `Please ${cmd.description.toLowerCase()}`;
      input.focus();
    };
    commandResults.appendChild(item);
  });
}

// Event listeners
sendBtn.addEventListener('click', sendMessage);

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

newChatBtn.addEventListener('click', () => {
  chatContainer.innerHTML = '';
  createMessage('assistant', 'Hello! I\'m your AI coding assistant. I can help you with:\n\n• Reading and editing files\n• Searching through code\n• Creating new files and directories\n• Running commands\n• Navigating your project structure\n\nWhat would you like to work on today?');
});

refreshTree.addEventListener('click', loadProjectTree);

closeDiffBtn.addEventListener('click', () => {
  diffContainer.classList.add('hidden');
  diffContent.innerHTML = '';
});

closeFileModal.addEventListener('click', () => {
  fileModal.classList.add('hidden');
});

// Command palette events
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'k') {
    e.preventDefault();
    showCommandPalette();
  }
  if (e.key === 'Escape') {
    hideCommandPalette();
  }
});

commandInput.addEventListener('input', (e) => {
  updateCommandResults(e.target.value);
});

commandInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const firstResult = commandResults.querySelector('.command-result');
    if (firstResult) {
      firstResult.click();
    }
  }
});

// Click outside to close modals
fileModal.addEventListener('click', (e) => {
  if (e.target === fileModal) {
    fileModal.classList.add('hidden');
  }
});

commandPalette.addEventListener('click', (e) => {
  if (e.target === commandPalette) {
    hideCommandPalette();
  }
});

// Existing functions (save, load chats) can remain the same
function enhanceCodeBlocks(scopeEl) {
  const pres = scopeEl.querySelectorAll('pre > code');
  pres.forEach(code => {
    const pre = code.parentElement;
    pre.classList.add('hljs');
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.innerText);
        btn.textContent = 'Copied!';
        setTimeout(() => (btn.textContent = 'Copy'), 1200);
      } catch (e) {
        btn.textContent = 'Error';
        setTimeout(() => (btn.textContent = 'Copy'), 1200);
      }
    });
    pre.appendChild(btn);
  });
}

async function saveChat() {
  const blocks = chatContainer.querySelectorAll('.message');
  const lines = [];
  lines.push(`# Chat Transcript - ${new Date().toLocaleString()}`);
  lines.push('');
  blocks.forEach(b => {
    const isUser = b.classList.contains('user');
    const role = isUser ? 'User' : 'Assistant';
    const content = b.querySelector('.content');
    if (!content.querySelector('.confirmation-buttons')) {
        let text = content ? content.innerText : '';
        lines.push(`## ${role}`);
        lines.push('');
        lines.push(text);
        lines.push('');
    }
  });

  const md = lines.join('\n');
  try {
    const res = await fetch('/api/save_chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ markdown: md })
    });
    const data = await res.json();
    if (data.ok) {
      await loadChatList();
      alert(`Saved as ${data.filename}`);
    } else {
      alert('Failed to save chat');
    }
  } catch (e) {
    alert('Network error while saving chat');
  }
}

saveChatBtn.addEventListener('click', saveChat);

async function loadChatList() {
  try {
    const res = await fetch('/api/chats');
    const data = await res.json();
    chatList.innerHTML = '';
    data.files.reverse().forEach(f => {
      const item = document.createElement('div');
      item.className = 'chat-item';
      item.innerHTML = `<div class="dot"></div><div class="name">${f.replace('.md','')}</div>`;
      item.title = f;
      item.addEventListener('click', () => loadChat(f));
      chatList.appendChild(item);
    });
  } catch (e) {
    // ignore
  }
}

async function loadChat(filename) {
    alert('Loading a previous chat will clear the current session. Please refresh the page after loading to start a new conversation based on it.');
    try {
        const res = await fetch(`/api/chats/${encodeURIComponent(filename)}`);
        const data = await res.json();
        if (data.error) return;

        chatContainer.innerHTML = '';

        const content = data.content || '';
        const parts = content.split(/\n##\s+(User|Assistant)\n/);
        for (let i = 1; i < parts.length; i += 2) {
        const role = parts[i].toLowerCase();
        const text = parts[i + 1] || '';
        createMessage(role === 'user' ? 'user' : 'assistant', text.trim());
        }

        if (parts.length <= 1) {
        createMessage('assistant', content);
        }
    } catch (e) {
        // ignore
    }
}

// Initialize the app
loadChatList();
loadProjectTree();

// Global test function for debugging
window.testTreeAPI = async function() {
  console.log('=== Testing Tree API ===');
  try {
    const res = await fetch('/api/tree?session_id=test&t=' + Date.now());
    console.log('Response status:', res.status);
    const data = await res.json();
    console.log('API Response:', data);
    console.log('Tree type:', typeof data.tree);
    console.log('Tree is array:', Array.isArray(data.tree));
    console.log('Tree length:', data.tree ? data.tree.length : 'undefined');
    return data;
  } catch (e) {
    console.error('Test failed:', e);
    return null;
  }
};

// Simple test function that bypasses loadProjectTree
window.testDirectAPI = async function() {
  console.log('=== Direct API Test ===');
  try {
    const response = await fetch('/api/tree?session_id=test&t=' + Date.now());
    const data = await response.json();
    
    console.log('Direct API call result:');
    console.log('- Status:', response.status);
    console.log('- Tree type:', typeof data.tree);
    console.log('- Is array:', Array.isArray(data.tree));
    console.log('- Tree length:', data.tree ? data.tree.length : 'undefined');
    console.log('- First item:', data.tree ? data.tree[0] : 'none');
    
    return data;
  } catch (error) {
    console.error('Direct API test failed:', error);
    return null;
  }
};