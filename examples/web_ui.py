#!/usr/bin/env python3
"""
Simple Web UI for Taurus Executor
"""

import sys
import os

# Add project root directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import json
import logging
import uuid
from typing import Dict, Any, Optional
from aiohttp import web, WSMsgType
import aiohttp_cors

from manage.sdk.client import TaurusClient
from src.executor_core.infra.logger import CommandLogger

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables to store active executions
active_executions: Dict[str, asyncio.Task] = {}

# Global variable to store execution output buffers
execution_buffers: Dict[str, list] = {}
execution_finished: Dict[str, dict] = {}  # stores {exit_code, error} when finished

# Global variables to store active sessions
active_sessions: Dict[str, Any] = {}  # session_id -> Session object
session_ws_connections: Dict[str, list] = {}  # session_id -> list of WebSocket connections


class WebUI:
    def __init__(self, client_address: str, cert_file: Optional[str] = None, 
                 key_file: Optional[str] = None, ca_file: Optional[str] = None,
                 default_cwd: str = "/tmp"):
        self.client_address = client_address
        self.cert_file = cert_file
        self.key_file = key_file
        self.ca_file = ca_file
        self.default_cwd = default_cwd
        self.app = web.Application()
        self.client_client = None  # Persistent gRPC client
        self.setup_routes()
        self.setup_cors()

    def setup_routes(self):
        """Setup routes for the web application."""
        self.app.router.add_get('/', self.index)
        self.app.router.add_post('/execute', self.execute_command)
        self.app.router.add_post('/terminate', self.terminate_command)
        self.app.router.add_post('/pause', self.pause_command)
        self.app.router.add_post('/resume', self.resume_command)
        self.app.router.add_get('/executions', self.list_executions)
        self.app.router.add_get('/ws', self.websocket_handler)
        
        # Session routes
        self.app.router.add_post('/session/create', self.create_session)
        self.app.router.add_post('/session/execute', self.execute_in_session)
        self.app.router.add_post('/session/close', self.close_session)
        self.app.router.add_get('/session/list', self.list_sessions)
        self.app.router.add_get('/session/ws', self.session_websocket_handler)
        
        # Script execution route
        self.app.router.add_post('/execute_script', self.execute_script)
        
        # File transfer routes
        self.app.router.add_get('/list_dir', self.list_dir)
        self.app.router.add_post('/upload', self.upload_file)
        self.app.router.add_get('/download', self.download_file)
        self.app.router.add_get('/check_file', self.check_file)
        
        # Check if static directory exists before adding static route
        if os.path.exists('static'):
            self.app.router.add_static('/static', 'static')
        else:
            logger.warning("Static directory not found, skipping static file serving")

    def setup_cors(self):
        """Setup CORS for the web application."""
        cors = aiohttp_cors.setup(self.app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        })

        # Add CORS to all routes
        for route in list(self.app.router.routes()):
            cors.add(route)

    async def index(self, request: web.Request) -> web.StreamResponse:
        """Serve the main HTML page."""
        html_content = '''
<!DOCTYPE html>
<html>
<head>
    <title>Taurus Executor Web UI</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #333; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input, textarea { width: 100%; padding: 8px; box-sizing: border-box; }
        button { background-color: #007bff; color: white; padding: 10px 20px; border: none; cursor: pointer; }
        button:hover { background-color: #0056b3; }
        button:disabled { background-color: #ccc; cursor: not-allowed; }
        #output { 
            background-color: #000; 
            color: #00ff00; 
            padding: 15px; 
            height: 400px; 
            overflow-y: auto; 
            font-family: monospace;
            white-space: pre-wrap;
        }
        #sessionOutput { 
            background-color: #000; 
            color: #00ff00; 
            padding: 15px; 
            height: 400px; 
            overflow-y: auto; 
            font-family: monospace;
            white-space: pre-wrap;
        }
        .status { margin-top: 10px; padding: 10px; border-radius: 4px; }
        .success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .running { background-color: #fff3cd; color: #856404; border: 1px solid #ffeaa7; }
        
        /* Tabs */
        .tabs { display: flex; border-bottom: 2px solid #ddd; margin-bottom: 20px; }
        .tab { padding: 10px 20px; cursor: pointer; border: 1px solid transparent; border-bottom: none; }
        .tab.active { background-color: #fff; border-color: #ddd; border-bottom-color: #fff; margin-bottom: -2px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        /* Session info */
        .session-info { background-color: #e7f3ff; padding: 10px; border-radius: 4px; margin-bottom: 15px; }
        .session-list { margin-top: 10px; }
        .session-item { padding: 5px 10px; border: 1px solid #ddd; margin-bottom: 5px; border-radius: 4px; cursor: pointer; }
        .session-item:hover { background-color: #f0f0f0; }
        .session-item.active { background-color: #007bff; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Taurus Executor Web UI</h1>
        
        <div class="tabs">
            <div class="tab active" data-tab="command">Command Execution</div>
            <div class="tab" data-tab="session">Interactive Session</div>
            <div class="tab" data-tab="script">Script Execution</div>
            <div class="tab" data-tab="files">File Manager</div>
        </div>
        
        <!-- Command Execution Tab -->
        <div id="commandTab" class="tab-content active">
            <form id="commandForm">
                <div class="form-group">
                    <label for="command">Command:</label>
                    <input type="text" id="command" placeholder="Enter command to execute" required>
                </div>
                
                <div class="form-group">
                    <label for="args">Arguments (space-separated):</label>
                    <input type="text" id="args" placeholder="Enter arguments">
                </div>
                
                <div class="form-group">
                    <label for="timeout">Timeout (seconds):</label>
                    <input type="number" id="timeout" value="30" min="1">
                </div>
                
                <div class="form-group">
                    <label>
                        <input type="checkbox" id="privileged"> Privileged Execution
                    </label>
                </div>
                
                <div class="form-group">
                    <label>
                        <input type="checkbox" id="useShell" checked> Use Shell (for built-in commands like history, cd, etc.)
                    </label>
                </div>
                
                <div id="suOptions" style="display: none;">
                    <div class="form-group">
                        <label for="su_user">Switch to User (su):</label>
                        <input type="text" id="su_user" placeholder="Target username">
                    </div>
                    
                    <div class="form-group">
                        <label for="su_password">Target User Password:</label>
                        <input type="password" id="su_password" placeholder="Enter target user password">
                    </div>
                </div>
                
                <button type="submit" id="executeBtn">Execute Command</button>
                <button type="button" id="terminateBtn" disabled>Terminate Command</button>
                <button type="button" id="pauseBtn" disabled style="background-color: #f0ad4e;">Pause</button>
                <button type="button" id="resumeBtn" disabled style="background-color: #5cb85c;">Resume</button>
            </form>
            
            <div id="status" class="status" style="display: none;"></div>
            
            <!-- Running Executions Panel -->
            <div class="form-group" style="margin-top: 20px;">
                <label>Running Executions:</label>
                <button type="button" id="refreshExecutionsBtn" style="margin-left: 10px; padding: 4px 12px; font-size: 12px;">Refresh</button>
                <div id="executionsList" style="margin-top: 10px; padding: 10px; background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; min-height: 40px;">
                    <span style="color: #999;">Click "Refresh" to see running executions</span>
                </div>
            </div>
            
            <div class="form-group">
                <label for="output">Output:</label>
                <div id="output"></div>
            </div>
        </div>
        
        <!-- Interactive Session Tab -->
        <div id="sessionTab" class="tab-content">
            <div id="sessionCreateForm">
                <div class="form-group">
                    <label for="sessionUser">Username (optional, leave empty for current user):</label>
                    <input type="text" id="sessionUser" placeholder="Target username">
                </div>
                
                <div class="form-group">
                    <label for="sessionPassword">Password (required if username specified):</label>
                    <input type="password" id="sessionPassword" placeholder="User password">
                </div>
                
                <div class="form-group">
                    <label for="sessionCwd">Working Directory (optional):</label>
                    <input type="text" id="sessionCwd" placeholder="/home/user">
                </div>
                
                <div class="form-group">
                    <label for="sessionTimeout">Session Timeout (seconds):</label>
                    <input type="number" id="sessionTimeout" value="3600" min="60">
                </div>
                
                <button type="button" id="createSessionBtn">Create Session</button>
                <button type="button" id="listSessionsBtn">List Sessions</button>
            </div>
            
            <div id="sessionInfo" class="session-info" style="display: none;">
                <strong>Active Session:</strong> <span id="sessionId"></span>
                <button type="button" id="closeSessionBtn" style="float: right; background-color: #dc3545;">Close Session</button>
            </div>
            
            <div id="sessionList" class="session-list" style="display: none;"></div>
            
            <div id="sessionTerminal" style="display: none;">
                <div class="form-group">
                    <label for="sessionCommand">Command:</label>
                    <div style="display: flex; gap: 10px;">
                        <input type="text" id="sessionCommand" placeholder="Enter command" style="flex: 1;">
                        <button type="button" id="executeSessionCmd" style="width: auto;">Execute</button>
                    </div>
                </div>
                
                <div class="form-group">
                    <label for="sessionOutput">Output:</label>
                    <div id="sessionOutput"></div>
                </div>
            </div>
        </div>
        
        <!-- Script Execution Tab -->
        <div id="scriptTab" class="tab-content">
            <form id="scriptForm">
                <div class="form-group">
                    <label for="scriptContent">Script Content:</label>
                    <textarea id="scriptContent" rows="10" placeholder="Paste your script here, or upload a file below"></textarea>
                </div>
                
                <div class="form-group">
                    <label for="scriptFile">Or Upload Script File:</label>
                    <input type="file" id="scriptFile" accept=".sh,.py,.pl,.rb,.js,.bash,.zsh,.fish,.ps1,.bat,.cmd">
                </div>
                
                <div class="form-group">
                    <label for="scriptInterpreter">Interpreter:</label>
                    <select id="scriptInterpreter" style="width: 100%; padding: 8px; box-sizing: border-box;">
                        <option value="/bin/bash">/bin/bash</option>
                        <option value="/bin/sh">/bin/sh</option>
                        <option value="/usr/bin/python3">/usr/bin/python3</option>
                        <option value="/usr/bin/python">/usr/bin/python</option>
                        <option value="/usr/bin/perl">/usr/bin/perl</option>
                        <option value="/usr/bin/ruby">/usr/bin/ruby</option>
                        <option value="/usr/bin/node">/usr/bin/node</option>
                        <option value="/usr/bin/pwsh">/usr/bin/pwsh</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label for="scriptArgs">Arguments (space-separated, optional):</label>
                    <input type="text" id="scriptArgs" placeholder="Enter arguments">
                </div>
                
                <div class="form-group">
                    <label for="scriptTimeout">Timeout (seconds):</label>
                    <input type="number" id="scriptTimeout" value="60" min="1">
                </div>
                
                <div class="form-group">
                    <label>
                        <input type="checkbox" id="scriptPrivileged"> Privileged Execution
                    </label>
                </div>
                
                <div id="scriptSuOptions" style="display: none;">
                    <div class="form-group">
                        <label for="scriptSuUser">Switch to User (su):</label>
                        <input type="text" id="scriptSuUser" placeholder="Target username">
                    </div>
                    
                    <div class="form-group">
                        <label for="scriptSuPassword">Target User Password:</label>
                        <input type="password" id="scriptSuPassword" placeholder="Enter target user password">
                    </div>
                </div>
                
                <button type="submit" id="executeScriptBtn">Execute Script</button>
                <button type="button" id="terminateScriptBtn" disabled>Terminate Script</button>
            </form>
            
            <div id="scriptStatus" class="status" style="display: none;"></div>
            
            <div class="form-group">
                <label for="scriptOutput">Output:</label>
                <div id="scriptOutput"></div>
            </div>
        </div>
        
        <!-- File Manager Tab -->
        <div id="filesTab" class="tab-content">
            <div class="form-group">
                <label for="fileBrowserPath">Current Path:</label>
                <div style="display: flex; gap: 8px;">
                    <input type="text" id="fileBrowserPath" value="/" style="flex: 1;" placeholder="/path/to/directory">
                    <button type="button" id="browseDirBtn" style="padding: 8px 16px;">Browse</button>
                </div>
            </div>
            
            <div id="fileList" style="border: 1px solid #ddd; border-radius: 4px; min-height: 200px; padding: 10px; background: #fafafa;">
                <span style="color: #999;">Click "Browse" to list directory contents</span>
            </div>
            
            <div class="form-group" style="margin-top: 20px;">
                <label>Upload File:</label>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <input type="file" id="uploadFileInput" style="flex: 1;">
                    <input type="text" id="uploadRemotePath" placeholder="Remote path (e.g., /tmp/uploaded.txt)" style="flex: 2;">
                    <button type="button" id="uploadFileBtn" style="padding: 8px 16px;">Upload</button>
                </div>
                <div id="uploadStatus" style="margin-top: 8px; font-size: 13px;"></div>
            </div>
            
            <div class="form-group">
                <label>Download File:</label>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <input type="text" id="downloadRemotePath" placeholder="Remote file path" style="flex: 2;">
                    <button type="button" id="downloadFileBtn" style="padding: 8px 16px;">Download</button>
                </div>
                <div id="downloadStatus" style="margin-top: 8px; font-size: 13px;"></div>
            </div>
        </div>
    </div>

    <script>
        let executionId = null;
        let ws = null;
        let currentSessionId = null;
        let sessionWs = null;
        
        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', function() {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                this.classList.add('active');
                document.getElementById(this.dataset.tab + 'Tab').classList.add('active');
            });
        });
        
        // Toggle SU options visibility
        document.getElementById('privileged').addEventListener('change', function() {
            document.getElementById('suOptions').style.display = this.checked ? 'block' : 'none';
        });
        
        // Command execution
        document.getElementById('commandForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const command = document.getElementById('command').value;
            const args = document.getElementById('args').value;
            const timeout = parseInt(document.getElementById('timeout').value);
            const privileged = document.getElementById('privileged').checked;
            const useShell = document.getElementById('useShell').checked;
            const su_user = document.getElementById('su_user').value;
            const su_password = document.getElementById('su_password').value;
            
            // Reset UI
            document.getElementById('output').textContent = '';
            document.getElementById('status').style.display = 'none';
            
            try {
                const requestBody = {
                    command: command,
                    args: args ? args.split(' ') : [],
                    timeout: timeout,
                    privileged: privileged,
                    use_shell: useShell
                };
                
                if (privileged && su_user) {
                    requestBody.su_user = su_user;
                    requestBody.su_password = su_password;
                }
                
                const response = await fetch('/execute', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(requestBody)
                });
                
                const result = await response.json();
                
                if (result.success) {
                    executionId = result.execution_id;
                    document.getElementById('executeBtn').disabled = true;
                    document.getElementById('terminateBtn').disabled = false;
                    document.getElementById('pauseBtn').disabled = false;
                    document.getElementById('resumeBtn').disabled = true;
                    
                    // Show status
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = 'Command is running...';
                    statusEl.className = 'status running';
                    statusEl.style.display = 'block';
                    
                    // Connect to WebSocket for real-time output
                    connectWebSocket(result.execution_id);
                } else {
                    showError(result.error || 'Failed to start command execution');
                }
            } catch (error) {
                showError('Error: ' + error.message);
            }
        });
        
        document.getElementById('terminateBtn').addEventListener('click', async () => {
            if (!executionId) return;
            
            try {
                const response = await fetch('/terminate', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        execution_id: executionId
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('executeBtn').disabled = false;
                    document.getElementById('terminateBtn').disabled = true;
                    document.getElementById('pauseBtn').disabled = true;
                    document.getElementById('resumeBtn').disabled = true;
                    executionId = null;
                    
                    if (ws) {
                        ws.close();
                        ws = null;
                    }
                    
                    // Show status
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = 'Command terminated by user';
                    statusEl.className = 'status success';
                    statusEl.style.display = 'block';
                } else {
                    showError(result.error || 'Failed to terminate command');
                }
            } catch (error) {
                showError('Error: ' + error.message);
            }
        });
        
        document.getElementById('pauseBtn').addEventListener('click', async () => {
            if (!executionId) return;
            
            try {
                const response = await fetch('/pause', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        execution_id: executionId
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('pauseBtn').disabled = true;
                    document.getElementById('resumeBtn').disabled = false;
                    
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = result.message || 'Command paused';
                    statusEl.className = 'status warning';
                    statusEl.style.display = 'block';
                } else {
                    showError(result.error || 'Failed to pause command');
                }
            } catch (error) {
                showError('Error: ' + error.message);
            }
        });
        
        document.getElementById('resumeBtn').addEventListener('click', async () => {
            if (!executionId) return;
            
            try {
                const response = await fetch('/resume', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        execution_id: executionId
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('pauseBtn').disabled = false;
                    document.getElementById('resumeBtn').disabled = true;
                    
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = result.message || 'Command resumed';
                    statusEl.className = 'status success';
                    statusEl.style.display = 'block';
                } else {
                    showError(result.error || 'Failed to resume command');
                }
            } catch (error) {
                showError('Error: ' + error.message);
            }
        });
        
        // Running executions panel
        document.getElementById('refreshExecutionsBtn').addEventListener('click', async () => {
            try {
                const response = await fetch('/executions');
                const result = await response.json();
                
                const listEl = document.getElementById('executionsList');
                
                if (result.success && result.executions && result.executions.length > 0) {
                    let html = '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">';
                    html += '<tr style="background: #e0e0e0;"><th style="padding: 6px; text-align: left; border: 1px solid #ccc;">Execution ID</th><th style="padding: 6px; text-align: left; border: 1px solid #ccc;">Command</th><th style="padding: 6px; text-align: left; border: 1px solid #ccc;">PID</th><th style="padding: 6px; text-align: left; border: 1px solid #ccc;">Status</th></tr>';
                    
                    for (const exec of result.executions) {
                        const statusColor = exec.status === 'paused' ? '#f0ad4e' : '#5cb85c';
                        html += `<tr>`;
                        html += `<td style="padding: 6px; border: 1px solid #ccc; font-family: monospace; font-size: 11px;">${exec.execution_id.substring(0, 8)}...</td>`;
                        html += `<td style="padding: 6px; border: 1px solid #ccc;">${exec.command} ${exec.args ? exec.args.join(' ') : ''}</td>`;
                        html += `<td style="padding: 6px; border: 1px solid #ccc;">${exec.pid}</td>`;
                        html += `<td style="padding: 6px; border: 1px solid #ccc;"><span style="background: ${statusColor}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px;">${exec.status}</span></td>`;
                        html += `</tr>`;
                    }
                    html += '</table>';
                    listEl.innerHTML = html;
                } else {
                    listEl.innerHTML = '<span style="color: #999;">No running executions</span>';
                }
            } catch (error) {
                listEl.innerHTML = '<span style="color: red;">Error: ' + error.message + '</span>';
            }
        });
        
        // Session management
        document.getElementById('createSessionBtn').addEventListener('click', async () => {
            const username = document.getElementById('sessionUser').value;
            const password = document.getElementById('sessionPassword').value;
            const cwd = document.getElementById('sessionCwd').value;
            const timeout = parseInt(document.getElementById('sessionTimeout').value);
            
            try {
                const response = await fetch('/session/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: username,
                        password: password,
                        working_directory: cwd,
                        timeout: timeout
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    currentSessionId = result.session_id;
                    document.getElementById('sessionId').textContent = result.session_id;
                    document.getElementById('sessionInfo').style.display = 'block';
                    document.getElementById('sessionTerminal').style.display = 'block';
                    document.getElementById('sessionOutput').textContent = 'Session created successfully\\n';
                    connectSessionWebSocket(result.session_id);
                } else {
                    alert('Failed to create session: ' + result.message);
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        });
        
        document.getElementById('listSessionsBtn').addEventListener('click', async () => {
            try {
                const response = await fetch('/session/list');
                const result = await response.json();
                
                const listEl = document.getElementById('sessionList');
                listEl.innerHTML = '';
                
                if (result.sessions && result.sessions.length > 0) {
                    listEl.style.display = 'block';
                    result.sessions.forEach(s => {
                        const item = document.createElement('div');
                        item.className = 'session-item' + (s.session_id === currentSessionId ? ' active' : '');
                        item.textContent = `${s.username} @ ${s.working_directory} (${s.session_id.substring(0, 8)}...)`;
                        item.onclick = () => {
                            currentSessionId = s.session_id;
                            document.getElementById('sessionId').textContent = s.session_id;
                            document.getElementById('sessionInfo').style.display = 'block';
                            document.getElementById('sessionTerminal').style.display = 'block';
                            connectSessionWebSocket(s.session_id);
                        };
                        listEl.appendChild(item);
                    });
                } else {
                    listEl.innerHTML = '<p>No active sessions</p>';
                    listEl.style.display = 'block';
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        });
        
        document.getElementById('closeSessionBtn').addEventListener('click', async () => {
            if (!currentSessionId) return;
            
            try {
                const response = await fetch('/session/close', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentSessionId })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('sessionOutput').textContent += '\\nSession closed\\n';
                    document.getElementById('sessionInfo').style.display = 'none';
                    document.getElementById('sessionTerminal').style.display = 'none';
                    currentSessionId = null;
                    if (sessionWs) {
                        sessionWs.close();
                        sessionWs = null;
                    }
                } else {
                    alert('Failed to close session: ' + result.message);
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        });
        
        document.getElementById('executeSessionCmd').addEventListener('click', async () => {
            if (!currentSessionId) {
                alert('No active session');
                return;
            }
            
            const command = document.getElementById('sessionCommand').value;
            if (!command) return;
            
            document.getElementById('sessionOutput').textContent += '$ ' + command + '\\n';
            document.getElementById('sessionCommand').value = '';
            
            try {
                const response = await fetch('/session/execute', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: currentSessionId,
                        command: command,
                        timeout: 30
                    })
                });
                
                const result = await response.json();
                
                if (result.output) {
                    document.getElementById('sessionOutput').textContent += result.output;
                }
                
                if (result.error) {
                    document.getElementById('sessionOutput').textContent += 'Error: ' + result.error + '\\n';
                }
                
                if (result.exit_code !== undefined) {
                    document.getElementById('sessionOutput').textContent += '\\n[Exit code: ' + result.exit_code + ']\\n';
                }
            } catch (error) {
                document.getElementById('sessionOutput').textContent += 'Error: ' + error.message + '\\n';
            }
        });
        
        document.getElementById('sessionCommand').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                document.getElementById('executeSessionCmd').click();
            }
        });
        
        // Script execution
        let scriptExecutionId = null;
        let scriptWs = null;
        
        document.getElementById('scriptPrivileged').addEventListener('change', function() {
            document.getElementById('scriptSuOptions').style.display = this.checked ? 'block' : 'none';
        });
        
        // When a file is uploaded, read its content into the textarea
        document.getElementById('scriptFile').addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;
            
            // Auto-detect interpreter from file extension
            const ext = file.name.split('.').pop().toLowerCase();
            const interpreterMap = {
                'sh': '/bin/bash',
                'bash': '/bin/bash',
                'zsh': '/bin/zsh',
                'fish': '/usr/bin/fish',
                'py': '/usr/bin/python3',
                'pl': '/usr/bin/perl',
                'rb': '/usr/bin/ruby',
                'js': '/usr/bin/node',
                'ps1': '/usr/bin/pwsh',
                'bat': 'cmd.exe',
                'cmd': 'cmd.exe'
            };
            if (interpreterMap[ext]) {
                document.getElementById('scriptInterpreter').value = interpreterMap[ext];
            }
            
            const reader = new FileReader();
            reader.onload = function(event) {
                document.getElementById('scriptContent').value = event.target.result;
            };
            reader.readAsText(file);
        });
        
        document.getElementById('scriptForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const scriptContent = document.getElementById('scriptContent').value;
            const interpreter = document.getElementById('scriptInterpreter').value;
            const args = document.getElementById('scriptArgs').value;
            const timeout = parseInt(document.getElementById('scriptTimeout').value);
            const privileged = document.getElementById('scriptPrivileged').checked;
            const su_user = document.getElementById('scriptSuUser').value;
            const su_password = document.getElementById('scriptSuPassword').value;
            
            if (!scriptContent.trim()) {
                showError('Script content is required');
                return;
            }
            
            // Reset UI
            document.getElementById('scriptOutput').textContent = '';
            document.getElementById('scriptStatus').style.display = 'none';
            
            try {
                const requestBody = {
                    script: scriptContent,
                    interpreter: interpreter,
                    args: args ? args.split(' ') : [],
                    timeout: timeout,
                    privileged: privileged,
                };
                
                if (privileged && su_user) {
                    requestBody.su_user = su_user;
                    requestBody.su_password = su_password;
                }
                
                const response = await fetch('/execute_script', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(requestBody)
                });
                
                const result = await response.json();
                
                if (result.success) {
                    scriptExecutionId = result.execution_id;
                    document.getElementById('executeScriptBtn').disabled = true;
                    document.getElementById('terminateScriptBtn').disabled = false;
                    
                    // Show status
                    const statusEl = document.getElementById('scriptStatus');
                    statusEl.textContent = 'Script is running...';
                    statusEl.className = 'status running';
                    statusEl.style.display = 'block';
                    
                    // Connect to WebSocket for real-time output
                    connectScriptWebSocket(result.execution_id);
                } else {
                    showError(result.error || 'Failed to start script execution');
                }
            } catch (error) {
                showError('Error: ' + error.message);
            }
        });
        
        document.getElementById('terminateScriptBtn').addEventListener('click', async () => {
            if (!scriptExecutionId) return;
            
            try {
                const response = await fetch('/terminate', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        execution_id: scriptExecutionId
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('executeScriptBtn').disabled = false;
                    document.getElementById('terminateScriptBtn').disabled = true;
                    scriptExecutionId = null;
                    
                    if (scriptWs) {
                        scriptWs.close();
                        scriptWs = null;
                    }
                    
                    // Show status
                    const statusEl = document.getElementById('scriptStatus');
                    statusEl.textContent = 'Script terminated by user';
                    statusEl.className = 'status success';
                    statusEl.style.display = 'block';
                } else {
                    showError(result.error || 'Failed to terminate script');
                }
            } catch (error) {
                showError('Error: ' + error.message);
            }
        });
        
        function connectScriptWebSocket(execId) {
            if (scriptWs) {
                scriptWs.close();
            }
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            scriptWs = new WebSocket(`${protocol}//${window.location.host}/ws?execution_id=${execId}`);
            
            scriptWs.onmessage = function(event) {
                const data = JSON.parse(event.data);
                const outputEl = document.getElementById('scriptOutput');
                
                if (data.stdout) {
                    outputEl.textContent += data.stdout;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                
                if (data.stderr) {
                    outputEl.textContent += data.stderr;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                
                if (data.finished !== undefined) {
                    document.getElementById('executeScriptBtn').disabled = false;
                    document.getElementById('terminateScriptBtn').disabled = true;
                    scriptExecutionId = null;
                    
                    if (scriptWs) {
                        scriptWs.close();
                        scriptWs = null;
                    }
                    
                    // Show status
                    const statusEl = document.getElementById('scriptStatus');
                    statusEl.textContent = `Script finished with exit code: ${data.finished}`;
                    statusEl.className = 'status success';
                    statusEl.style.display = 'block';
                }
                
                if (data.error) {
                    document.getElementById('executeScriptBtn').disabled = false;
                    document.getElementById('terminateScriptBtn').disabled = true;
                    scriptExecutionId = null;
                    
                    if (scriptWs) {
                        scriptWs.close();
                        scriptWs = null;
                    }
                    
                    // Show status
                    const statusEl = document.getElementById('scriptStatus');
                    statusEl.textContent = `Error: ${data.error}`;
                    statusEl.className = 'status error';
                    statusEl.style.display = 'block';
                }
            };
            
            scriptWs.onerror = function(error) {
                console.error('Script WebSocket error:', error);
            };
            
            scriptWs.onclose = function() {
                console.log('Script WebSocket connection closed');
            };
        }
        
        function connectSessionWebSocket(sessionId) {
            if (sessionWs) {
                sessionWs.close();
            }
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            sessionWs = new WebSocket(`${protocol}//${window.location.host}/session/ws?session_id=${sessionId}`);
            
            sessionWs.onmessage = function(event) {
                const data = JSON.parse(event.data);
                const outputEl = document.getElementById('sessionOutput');
                
                if (data.stdout) {
                    outputEl.textContent += data.stdout;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                
                if (data.stderr) {
                    outputEl.textContent += data.stderr;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                
                if (data.finished !== undefined) {
                    outputEl.textContent += '\\n[Exit code: ' + data.exit_code + ']\\n';
                }
                
                if (data.error) {
                    outputEl.textContent += 'Error: ' + data.error + '\\n';
                }
            };
        }
        
        function connectWebSocket(execId) {
            if (ws) {
                ws.close();
            }
            
            // Use wss:// for secure connections
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws?execution_id=${execId}`);
            
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                const outputEl = document.getElementById('output');
                
                if (data.stdout) {
                    outputEl.textContent += data.stdout;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                
                if (data.stderr) {
                    outputEl.textContent += data.stderr;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                
                if (data.finished !== undefined) {
                    document.getElementById('executeBtn').disabled = false;
                    document.getElementById('terminateBtn').disabled = true;
                    executionId = null;
                    
                    if (ws) {
                        ws.close();
                        ws = null;
                    }
                    
                    // Show status
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = `Command finished with exit code: ${data.finished}`;
                    statusEl.className = 'status success';
                    statusEl.style.display = 'block';
                }
                
                if (data.error) {
                    document.getElementById('executeBtn').disabled = false;
                    document.getElementById('terminateBtn').disabled = true;
                    executionId = null;
                    
                    if (ws) {
                        ws.close();
                        ws = null;
                    }
                    
                    // Show status
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = `Error: ${data.error}`;
                    statusEl.className = 'status error';
                    statusEl.style.display = 'block';
                }
            };
            
            ws.onerror = function(error) {
                console.error('WebSocket error:', error);
            };
            
            ws.onclose = function() {
                console.log('WebSocket connection closed');
            };
        }
        
        function showError(message) {
            const statusEl = document.getElementById('status');
            statusEl.textContent = message;
            statusEl.className = 'status error';
            statusEl.style.display = 'block';
        }
        
        // File Manager
        document.getElementById('browseDirBtn').addEventListener('click', async () => {
            const path = document.getElementById('fileBrowserPath').value || '/';
            const listEl = document.getElementById('fileList');
            listEl.innerHTML = '<span style="color: #999;">Loading...</span>';
            
            try {
                const response = await fetch(`/list_dir?path=${encodeURIComponent(path)}`);
                const result = await response.json();
                
                if (result.success && result.entries) {
                    let html = '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">';
                    html += '<tr style="background: #e0e0e0;"><th style="padding: 8px; text-align: left; border: 1px solid #ccc;">Name</th><th style="padding: 8px; text-align: right; border: 1px solid #ccc;">Size</th><th style="padding: 8px; text-align: left; border: 1px solid #ccc;">Permissions</th><th style="padding: 8px; text-align: left; border: 1px solid #ccc;">Owner</th><th style="padding: 8px; text-align: left; border: 1px solid #ccc;">Modified</th></tr>';
                    
                    for (const entry of result.entries) {
                        const icon = entry.is_dir ? '📁' : '📄';
                        const size = entry.is_dir ? '-' : formatSize(entry.size);
                        const date = new Date(entry.modified_at * 1000).toLocaleString();
                        const clickable = entry.is_dir ? `style="cursor: pointer; color: #0066cc;" onclick="navigateTo('${path.replace(/\\/$/, '')}/${entry.name}')"` : '';
                        
                        html += `<tr>`;
                        html += `<td style="padding: 8px; border: 1px solid #ccc;" ${clickable}>${icon} ${entry.name}</td>`;
                        html += `<td style="padding: 8px; border: 1px solid #ccc; text-align: right;">${size}</td>`;
                        html += `<td style="padding: 8px; border: 1px solid #ccc; font-family: monospace;">${entry.permissions}</td>`;
                        html += `<td style="padding: 8px; border: 1px solid #ccc;">${entry.owner}:${entry.group}</td>`;
                        html += `<td style="padding: 8px; border: 1px solid #ccc;">${date}</td>`;
                        html += `</tr>`;
                    }
                    html += '</table>';
                    listEl.innerHTML = html;
                } else {
                    listEl.innerHTML = `<span style="color: red;">Error: ${result.error || 'Failed to list directory'}</span>`;
                }
            } catch (error) {
                listEl.innerHTML = `<span style="color: red;">Error: ${error.message}</span>`;
            }
        });
        
        document.getElementById('uploadFileBtn').addEventListener('click', async () => {
            const fileInput = document.getElementById('uploadFileInput');
            const remotePath = document.getElementById('uploadRemotePath').value;
            const statusEl = document.getElementById('uploadStatus');
            
            if (!fileInput.files || fileInput.files.length === 0) {
                statusEl.innerHTML = '<span style="color: red;">Please select a file</span>';
                return;
            }
            
            const file = fileInput.files[0];
            const formData = new FormData();
            formData.append('file', file);
            
            statusEl.innerHTML = '<span style="color: #0066cc;">Uploading...</span>';
            
            try {
                const url = remotePath ? `/upload?path=${encodeURIComponent(remotePath)}` : '/upload';
                const response = await fetch(url, {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                
                if (result.success) {
                    statusEl.innerHTML = `<span style="color: green;">✓ Uploaded successfully (${formatSize(result.bytes_received)})</span>`;
                } else {
                    statusEl.innerHTML = `<span style="color: red;">✗ Failed: ${result.message || result.error}</span>`;
                }
            } catch (error) {
                statusEl.innerHTML = `<span style="color: red;">✗ Error: ${error.message}</span>`;
            }
        });
        
        document.getElementById('downloadFileBtn').addEventListener('click', async () => {
            const remotePath = document.getElementById('downloadRemotePath').value;
            const statusEl = document.getElementById('downloadStatus');
            
            if (!remotePath) {
                statusEl.innerHTML = '<span style="color: red;">Please enter a remote file path</span>';
                return;
            }
            
            statusEl.innerHTML = '<span style="color: #0066cc;">Checking file...</span>';
            
            try {
                // First check if file exists
                const checkResponse = await fetch(`/check_file?path=${encodeURIComponent(remotePath)}`);
                const checkResult = await checkResponse.json();
                
                if (!checkResult.success) {
                    statusEl.innerHTML = `<span style="color: red;"> Error: ${checkResult.error}</span>`;
                    return;
                }
                
                if (!checkResult.exists) {
                    statusEl.innerHTML = `<span style="color: red;"> File not found: ${remotePath}</span>`;
                    return;
                }
                
                statusEl.innerHTML = '<span style="color: #0066cc;">Downloading...</span>';
                
                // Try to use File System Access API for save location selection
                if (window.showSaveFilePicker) {
                    try {
                        const fileName = remotePath.split('/').pop() || 'download';
                        const fileHandle = await window.showSaveFilePicker({
                            suggestedName: fileName,
                            types: [{
                                description: 'All Files',
                                accept: {'*/*': ['.*']}
                            }]
                        });
                        
                        const writable = await fileHandle.createWritable();
                        const response = await fetch(`/download?path=${encodeURIComponent(remotePath)}`);
                        
                        if (response.ok) {
                            const reader = response.body.getReader();
                            while (true) {
                                const { done, value } = await reader.read();
                                if (done) break;
                                await writable.write(value);
                            }
                            await writable.close();
                            statusEl.innerHTML = '<span style="color: green;">✓ Downloaded successfully</span>';
                        } else {
                            const result = await response.json();
                            statusEl.innerHTML = `<span style="color: red;">✗ Failed: ${result.error || 'Download failed'}</span>`;
                        }
                    } catch (err) {
                        if (err.name === 'AbortError') {
                            statusEl.innerHTML = '<span style="color: #999;">Download cancelled</span>';
                        } else {
                            throw err;
                        }
                    }
                } else {
                    // Fallback for browsers without File System Access API
                    const response = await fetch(`/download?path=${encodeURIComponent(remotePath)}`);
                    
                    if (response.ok) {
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = remotePath.split('/').pop();
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                        statusEl.innerHTML = '<span style="color: green;">✓ Downloaded successfully</span>';
                    } else {
                        const result = await response.json();
                        statusEl.innerHTML = `<span style="color: red;">✗ Failed: ${result.error || 'Download failed'}</span>`;
                    }
                }
            } catch (error) {
                statusEl.innerHTML = `<span style="color: red;">✗ Error: ${error.message}</span>`;
            }
        });
        
        function navigateTo(path) {
            document.getElementById('fileBrowserPath').value = path;
            document.getElementById('browseDirBtn').click();
        }
        
        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }
    </script>
</body>
</html>
        '''
        return web.Response(text=html_content, content_type='text/html')

    async def _safe_json(self, request: web.Request) -> dict:
        """Safely parse the JSON request body.

        Raises ValueError with a clear message if the body is missing,
        empty, or not a valid JSON object.
        """
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            return data
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            raise ValueError(f"Invalid request body: {e}")

    async def execute_command(self, request: web.Request) -> web.Response:
        """Handle command execution requests."""
        try:
            try:
                data = await self._safe_json(request)
            except ValueError as e:
                return web.json_response({
                    'success': False,
                    'error': str(e)
                }, status=400)
            command = data.get('command')
            args = data.get('args', [])
            timeout = data.get('timeout', 30)
            merge_streams = data.get('merge_streams', False)
            privileged = data.get('privileged', False)
            su_user = data.get('su_user')
            su_password = data.get('su_password')
            # Respect the frontend's explicit shell request; fall back to
            # auto-detection when the user did not check the box.
            client_use_shell = data.get('use_shell', True)
            
            if not command:
                return web.json_response({
                    'success': False,
                    'error': 'Command is required'
                }, status=400)

            # Determine whether the command relies on shell syntax. If it does,
            # the command text is forwarded verbatim to the client and executed
            # through ``/bin/bash -c`` so that operators (&&, ||, |, pipes,
            # redirections, quoting, variable expansion, ...) are preserved.
            SHELL_TOKENS = ("&&", "||", "|", ";", ">", "<", "`", "$")
            use_shell = client_use_shell
            if not use_shell:
                for tok in SHELL_TOKENS:
                    if tok in command:
                        use_shell = True
                        break
                if "'" in command or '"' in command:
                    use_shell = True

            if use_shell:
                # When shell semantics are required, send the whole command
                # line as ``command`` and set shell=True so the client runs it
                # through /bin/bash -c. Any ``args`` supplied by the caller are
                # ignored in this mode.
                executable = command
                remaining_args = []
            else:
                parts = command.split()
                executable = parts[0] if parts else ""
                remaining_args = parts[1:] + (args if args else [])
            
            # Generate execution ID and pass it to the client via environment
            # so both sides use the same ID for pause/resume/terminate
            execution_id = str(uuid.uuid4())
            execution_env = {'__TAURUS_EXECUTION_ID__': execution_id}
            
            # Start execution in background
            task = asyncio.create_task(
                self._run_command(
                    execution_id, executable, remaining_args, timeout, merge_streams,
                    privileged=privileged, su_user=su_user, su_password=su_password,
                    shell=use_shell,
                )
            )
            active_executions[execution_id] = task
            
            return web.json_response({
                'success': True,
                'execution_id': execution_id
            })
            
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def execute_script(self, request: web.Request) -> web.Response:
        """Handle script execution requests."""
        try:
            try:
                data = await self._safe_json(request)
            except ValueError as e:
                return web.json_response({
                    'success': False,
                    'error': str(e)
                }, status=400)
            script_content = data.get('script')
            interpreter = data.get('interpreter', '/bin/bash')
            args = data.get('args', [])
            timeout = data.get('timeout', 60)
            privileged = data.get('privileged', False)
            su_user = data.get('su_user')
            su_password = data.get('su_password')
            
            if not script_content:
                return web.json_response({
                    'success': False,
                    'error': 'Script content is required'
                }, status=400)
            
            # Build command: interpreter -c "script_content" args
            # The script content is passed via stdin to avoid shell escaping issues
            executable = interpreter
            remaining_args = ['-c', script_content] + (args if args else [])
            
            # Generate execution ID
            execution_id = str(uuid.uuid4())
            
            # Start execution in background
            task = asyncio.create_task(
                self._run_command(
                    execution_id, executable, remaining_args, timeout, False,
                    privileged=privileged, su_user=su_user, su_password=su_password,
                    shell=False,
                )
            )
            active_executions[execution_id] = task
            
            return web.json_response({
                'success': True,
                'execution_id': execution_id
            })
            
        except Exception as e:
            logger.error(f"Error executing script: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def terminate_command(self, request: web.Request) -> web.Response:
        """Handle command termination requests."""
        try:
            try:
                data = await self._safe_json(request)
            except ValueError as e:
                return web.json_response({
                    'success': False,
                    'error': str(e)
                }, status=400)
            execution_id = data.get('execution_id')
            
            if not execution_id:
                return web.json_response({
                    'success': False,
                    'error': 'Execution ID is required'
                }, status=400)
            
            task = active_executions.get(execution_id)
            if task and not task.done():
                CommandLogger.terminate_by_user(logger, execution_id)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    CommandLogger.terminate_success(logger, execution_id)
                active_executions.pop(execution_id, None)
                
                return web.json_response({
                    'success': True,
                    'message': 'Command terminated'
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': 'Execution not found or already completed'
                }, status=404)
                
        except Exception as e:
            logger.error(f"Error terminating command: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def pause_command(self, request: web.Request) -> web.Response:
        """Handle command pause requests (SIGSTOP)."""
        try:
            try:
                data = await self._safe_json(request)
            except ValueError as e:
                return web.json_response({
                    'success': False,
                    'error': str(e)
                }, status=400)
            execution_id = data.get('execution_id')

            if not execution_id:
                return web.json_response({
                    'success': False,
                    'error': 'Execution ID is required'
                }, status=400)

            task = active_executions.get(execution_id)
            if not task or task.done():
                return web.json_response({
                    'success': False,
                    'error': 'Execution not found or already completed'
                }, status=404)

            # Ensure client client is connected
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            # Query client for running executions to find the PID
            executions = await self.client_client.list_executions()
            pid = None
            for exec_info in executions:
                if exec_info['execution_id'] == execution_id:
                    pid = exec_info['pid']
                    break

            if not pid or pid <= 0:
                return web.json_response({
                    'success': False,
                    'error': 'Could not find PID for this execution'
                }, status=404)

            # Send SIGSTOP (19) to pause the process group
            result = await self.client_client.send_signal(pid, 19)
            if result['success']:
                return web.json_response({
                    'success': True,
                    'message': f'Command paused (PID: {pid})'
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': result['message']
                }, status=500)

        except Exception as e:
            logger.error(f"Error pausing command: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def resume_command(self, request: web.Request) -> web.Response:
        """Handle command resume requests (SIGCONT)."""
        try:
            try:
                data = await self._safe_json(request)
            except ValueError as e:
                return web.json_response({
                    'success': False,
                    'error': str(e)
                }, status=400)
            execution_id = data.get('execution_id')

            if not execution_id:
                return web.json_response({
                    'success': False,
                    'error': 'Execution ID is required'
                }, status=400)

            task = active_executions.get(execution_id)
            if not task or task.done():
                return web.json_response({
                    'success': False,
                    'error': 'Execution not found or already completed'
                }, status=404)

            # Ensure client client is connected
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            # Query client for running executions to find the PID
            executions = await self.client_client.list_executions()
            pid = None
            for exec_info in executions:
                if exec_info['execution_id'] == execution_id:
                    pid = exec_info['pid']
                    break

            if not pid or pid <= 0:
                return web.json_response({
                    'success': False,
                    'error': 'Could not find PID for this execution'
                }, status=404)

            # Send SIGCONT (18) to resume the process group
            result = await self.client_client.send_signal(pid, 18)
            if result['success']:
                return web.json_response({
                    'success': True,
                    'message': f'Command resumed (PID: {pid})'
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': result['message']
                }, status=500)

        except Exception as e:
            logger.error(f"Error resuming command: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def list_executions(self, request: web.Request) -> web.Response:
        """List all currently executing commands on the client."""
        try:
            # Ensure client client is connected
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            executions = await self.client_client.list_executions()
            return web.json_response({
                'success': True,
                'executions': executions
            })

        except Exception as e:
            logger.error(f"Error listing executions: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def list_dir(self, request: web.Request) -> web.Response:
        """List directory contents on the client."""
        try:
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            path = request.query.get('path', '/')
            entries = await self.client_client.list_directory(path)
            return web.json_response({
                'success': True,
                'path': path,
                'entries': entries
            })

        except Exception as e:
            logger.error(f"Error listing directory: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def upload_file(self, request: web.Request) -> web.Response:
        """Upload a file to the client."""
        try:
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            reader = await request.multipart()
            field = await reader.next()
            if field is None:
                return web.json_response({'success': False, 'error': 'No file uploaded'}, status=400)

            # Read the uploaded file into memory
            file_data = await field.read(decode=False)
            remote_path = request.query.get('path', '')
            if not remote_path:
                # Use the original filename if no path specified
                remote_path = f"/tmp/{field.filename}"

            # Write to a temp file, then upload via gRPC streaming
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.basename(remote_path)) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            try:
                result = await self.client_client.upload_file(tmp_path, remote_path)
                return web.json_response(result)
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def download_file(self, request: web.Request) -> web.Response:
        """Download a file from the client."""
        try:
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            remote_path = request.query.get('path', '')
            if not remote_path:
                return web.json_response({'success': False, 'error': 'No path specified'}, status=400)

            import tempfile
            tmp_fd, tmp_path = tempfile.mkstemp()
            os.close(tmp_fd)  # Close the fd so download_file can open it freely

            try:
                result = await self.client_client.download_file(remote_path, tmp_path)
                
                # Check if download was successful
                if result.get('total_size', 0) == 0:
                    os.unlink(tmp_path)
                    return web.json_response({
                        'success': False,
                        'error': 'File not found or empty'
                    }, status=404)
                
                file_name = os.path.basename(remote_path)

                return web.FileResponse(
                    tmp_path,
                    headers={
                        'Content-Disposition': f'attachment; filename="{file_name}"'
                    }
                )
            except Exception as e:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise e

        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def check_file(self, request: web.Request) -> web.Response:
        """Check if a file exists on the client."""
        try:
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()

            remote_path = request.query.get('path', '')
            if not remote_path:
                return web.json_response({'success': False, 'error': 'No path specified'}, status=400)

            # Use list_directory to check if file exists
            parent_dir = os.path.dirname(remote_path) or '/'
            file_name = os.path.basename(remote_path)
            
            entries = await self.client_client.list_directory(parent_dir)
            file_exists = any(e['name'] == file_name and not e['is_dir'] for e in entries)
            
            if file_exists:
                # Get file size
                file_entry = next((e for e in entries if e['name'] == file_name), None)
                return web.json_response({
                    'success': True,
                    'exists': True,
                    'size': file_entry['size'] if file_entry else 0
                })
            else:
                return web.json_response({
                    'success': True,
                    'exists': False
                })

        except Exception as e:
            logger.error(f"Error checking file: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections for real-time output."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        execution_id = request.query.get('execution_id')
        if not execution_id:
            await ws.close()
            return ws
        
        logger.info(f"WebSocket connection established for execution {execution_id}")
        
        # Store WebSocket connection in the task context
        task = active_executions.get(execution_id)
        if task:
            # Ensure using the same list object
            if not hasattr(task, '_ws_connections'):
                task._ws_connections = []
            task._ws_connections.append(ws)
            logger.info(f"WebSocket registered for execution {execution_id}, total: {len(task._ws_connections)}")
        
        # First send cached historical output (solves race condition, lets client immediately see previous output)
        if execution_id in execution_buffers:
            for cached_response in execution_buffers[execution_id]:
                if not ws.closed:
                    stdout = cached_response.get("stdout", b"").decode("utf-8", errors="replace")
                    stderr = cached_response.get("stderr", b"").decode("utf-8", errors="replace")
                    if stdout or stderr:
                        await ws.send_str(json.dumps({
                            "stdout": stdout,
                            "stderr": stderr
                        }))
            logger.info(f"Sent cached output for execution {execution_id}")
        
        # If command has completed, send completion signal
        if execution_id in execution_finished:
            finished_data = execution_finished[execution_id]
            if "error" in finished_data:
                await ws.send_str(json.dumps({"error": finished_data["error"]}))
            elif "exit_code" in finished_data:
                await ws.send_str(json.dumps({"finished": finished_data["exit_code"]}))
            logger.info(f"Sent finished signal for completed execution {execution_id}")
        elif task:
            # Wait for task to complete (task is still running)
            try:
                await asyncio.wait_for(task, timeout=None)
            except asyncio.CancelledError:
                CommandLogger.websocket_task_cancelled(logger, execution_id)
            except Exception as e:
                CommandLogger.websocket_task_error(logger, execution_id, e)
        
        # Remove WebSocket connection from task context
        if task and hasattr(task, '_ws_connections'):
            if ws in task._ws_connections:
                task._ws_connections.remove(ws)
        logger.info(f"WebSocket connection closed for execution {execution_id}")
        
        return ws

    async def create_session(self, request: web.Request) -> web.Response:
        """Handle session creation requests."""
        try:
            data = await self._safe_json(request)
            username = data.get('username', '')
            password = data.get('password', '')
            working_directory = data.get('working_directory', '')
            timeout = data.get('timeout', 3600)
            
            logger.info(f"Creating session for user: {username or 'current'}")
            
            # Ensure client client is connected
            if not self.client_client:
                self.client_client = TaurusClient(
                    self.client_address,
                    cert_file=self.cert_file,
                    key_file=self.key_file,
                    ca_file=self.ca_file
                )
                await self.client_client.__aenter__()
            
            session = await self.client_client.create_session(
                username=username if username else None,
                password=password if password else None,
                working_directory=working_directory if working_directory else None,
                timeout=timeout,
            )
            
            # Store session
            active_sessions[session.session_id] = session
            session_ws_connections[session.session_id] = []
            
            return web.json_response({
                'success': True,
                'session_id': session.session_id,
                'message': 'Session created successfully'
            })
                
        except Exception as e:
            logger.error(f"Error creating session: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'message': str(e)
            }, status=500)

    async def execute_in_session(self, request: web.Request) -> web.Response:
        """Handle command execution in session."""
        try:
            data = await self._safe_json(request)
            session_id = data.get('session_id')
            command = data.get('command')
            timeout = data.get('timeout', 30)
            
            if not session_id or not command:
                return web.json_response({
                    'success': False,
                    'error': 'session_id and command are required'
                }, status=400)
            
            session = active_sessions.get(session_id)
            if not session:
                return web.json_response({
                    'success': False,
                    'error': 'Session not found'
                }, status=404)
            
            if not session.is_active:
                return web.json_response({
                    'success': False,
                    'error': 'Session is not active'
                }, status=400)
            
            # Collect all output
            output_parts = []
            exit_code = 0
            error_msg = None
            
            async for output in session.execute(command, timeout=timeout):
                logger.debug(f"Session output: {output}")
                if "stdout" in output:
                    output_parts.append(output["stdout"].decode() if isinstance(output["stdout"], bytes) else output["stdout"])
                if "stderr" in output:
                    output_parts.append(output["stderr"].decode() if isinstance(output["stderr"], bytes) else output["stderr"])
                if "exit_code" in output:
                    exit_code = output["exit_code"]
                if "error" in output:
                    error_msg = output["error"]
            
            logger.info(f"Collected output: {repr(''.join(output_parts))}")
            
            if error_msg:
                return web.json_response({
                    'success': False,
                    'output': ''.join(output_parts),
                    'error': error_msg,
                    'exit_code': exit_code
                })
            
            return web.json_response({
                'success': True,
                'output': ''.join(output_parts),
                'exit_code': exit_code
            })
                
        except Exception as e:
            logger.error(f"Error executing in session: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def close_session(self, request: web.Request) -> web.Response:
        """Handle session close requests."""
        try:
            data = await self._safe_json(request)
            session_id = data.get('session_id')
            
            if not session_id:
                return web.json_response({
                    'success': False,
                    'error': 'session_id is required'
                }, status=400)
            
            session = active_sessions.get(session_id)
            if not session:
                return web.json_response({
                    'success': False,
                    'error': 'Session not found'
                }, status=404)
            
            success = await session.close()
            active_sessions.pop(session_id, None)
            session_ws_connections.pop(session_id, None)
            
            return web.json_response({
                'success': success,
                'message': 'Session closed' if success else 'Failed to close session'
            })
                
        except Exception as e:
            logger.error(f"Error closing session: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def list_sessions(self, request: web.Request) -> web.Response:
        """Handle list sessions requests."""
        try:
            async with TaurusClient(
                self.client_address,
                cert_file=self.cert_file,
                key_file=self.key_file,
                ca_file=self.ca_file
            ) as client_client:
                sessions = await client_client.list_sessions()
                
                return web.json_response({
                    'success': True,
                    'sessions': sessions
                })
                
        except Exception as e:
            logger.error(f"Error listing sessions: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def session_websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections for session real-time output."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        session_id = request.query.get('session_id')
        if not session_id:
            await ws.close()
            return ws
        
        logger.info(f"WebSocket connection established for session {session_id}")
        
        # Store WebSocket connection
        if session_id not in session_ws_connections:
            session_ws_connections[session_id] = []
        session_ws_connections[session_id].append(ws)
        
        # Keep connection open
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Could handle commands via WebSocket
                    pass
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error for session {session_id}: {ws.exception()}")
        finally:
            if ws in session_ws_connections.get(session_id, []):
                session_ws_connections[session_id].remove(ws)
        
        logger.info(f"WebSocket connection closed for session {session_id}")
        return ws

    async def _run_command(self, execution_id: str, command: str, args: list, timeout: int, merge_streams: bool = False,
                          privileged: bool = False, su_user: Optional[str] = None, su_password: Optional[str] = None,
                          shell: bool = False):
        """Run the actual command using the client client."""
        client = None
        
        # Initialize output cache and WebSocket connection list
        execution_buffers[execution_id] = []
        execution_finished.pop(execution_id, None)
        
        # Ensure task._ws_connections exists so WebSocket handler will append to the same list
        task = asyncio.current_task()
        if not hasattr(task, '_ws_connections'):
            task._ws_connections = []
        ws_connections = task._ws_connections
        
        # Prepare environment variables
        environment = {}
        if privileged:
            environment["PRIVILEGED_EXECUTION"] = "true"
            if su_user:
                environment["SU_USER"] = su_user
            if su_password:
                environment["SU_PASSWORD"] = su_password
            logger.info(f"Privileged execution enabled for {execution_id}, su_user={su_user}, has_password={bool(su_password)}")
        
        # Pass execution_id to client so both sides use the same ID
        environment["__TAURUS_EXECUTION_ID__"] = execution_id
        
        logger.info(f"Starting command execution {execution_id}: {command} {' '.join(args)}, timeout={timeout}, privileged={privileged}, shell={shell}, ws_count={len(ws_connections)}")
        
        try:
            async with TaurusClient(
                self.client_address, 
                cert_file=self.cert_file,
                key_file=self.key_file,
                ca_file=self.ca_file
            ) as client_client:
                async for response in client_client.execute_command(
                    command=command,
                    args=args,
                    timeout=timeout,
                    merge_streams=merge_streams,
                    environment=environment if environment else None,
                    shell=shell,
                ):
                    # Cache output
                    execution_buffers[execution_id].append(response)
                    
                    # Parse output
                    stdout = response.get("stdout", b"").decode("utf-8", errors="replace") if response.get("stdout") else ""
                    stderr = response.get("stderr", b"").decode("utf-8", errors="replace") if response.get("stderr") else ""
                    
                    # Send output to all WebSocket connections
                    for ws in ws_connections:
                        if not ws.closed:
                            if stdout or stderr:
                                await ws.send_str(json.dumps({
                                    "stdout": stdout,
                                    "stderr": stderr
                                }))
                    
                    # Check if command is finished
                    if response.get("finished", False):
                        exit_code = response.get("exit_code", 0)
                        logger.info(f"Command {execution_id} finished with exit code {exit_code}")
                        execution_finished[execution_id] = {"exit_code": exit_code}
                        # Send final message to all WebSocket connections
                        for ws in ws_connections:
                            if not ws.closed:
                                await ws.send_str(json.dumps({
                                    "finished": exit_code
                                }))
                        break
                        
                    elif response.get("error"):
                        error_msg = response.get("error", "")
                        logger.warning(f"Command {execution_id} error: {error_msg}")
                        execution_finished[execution_id] = {"error": error_msg}
                        # Send error to all WebSocket connections
                        for ws in ws_connections:
                            if not ws.closed:
                                await ws.send_str(json.dumps({
                                    "error": error_msg
                                }))
                        break
                        
        except asyncio.CancelledError:
            # Command was cancelled by user
            CommandLogger.cancelled_by_user(logger, execution_id, command, args)
            execution_finished[execution_id] = {"error": "Command execution cancelled by user"}
            # Send cancellation message to all WebSocket connections
            for ws in ws_connections:
                if not ws.closed:
                    await ws.send_str(json.dumps({
                        "error": "Command execution cancelled by user"
                    }))
            raise
        except Exception as e:
            logger.error(f"Error in command execution {execution_id}: {e}", exc_info=True)
            execution_finished[execution_id] = {"error": str(e)}
            # Send error to all WebSocket connections
            for ws in ws_connections:
                if not ws.closed:
                    await ws.send_str(json.dumps({
                        "error": str(e)
                    }))
        finally:
            # Clean up (safe even if another code path already removed it)
            active_executions.pop(execution_id, None)


def main():
    """Main entry point."""
    import argparse
    import pathlib

    script_dir = pathlib.Path(__file__).resolve().parent
    webui_tls_dir = script_dir / "tls"

    # Certificate detection:
    # Use Web UI TLS certificates (generated by generate_webui_cert.sh)
    # or none (insecure connection)
    if webui_tls_dir.exists() and (webui_tls_dir / "webui-client.crt").exists():
        default_client_cert = str(webui_tls_dir / "webui-client.crt")
        default_client_key = str(webui_tls_dir / "webui-client.key")
        default_client_ca = str(webui_tls_dir / "ca.crt") if (webui_tls_dir / "ca.crt").exists() else None
    else:
        default_client_cert = None
        default_client_key = None
        default_client_ca = None

    parser = argparse.ArgumentParser(description="Web UI for Taurus Executor")
    parser.add_argument(
        "--client-address",
        default="localhost:50051",
        help="Address of the client (default: localhost:50051)"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind to (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8088,
        help="Port to bind to (default: 8088)"
    )

    # Client SSL/TLS parameters (for connecting to backend gRPC service)
    parser.add_argument(
        "--client-cert-file",
        default=default_client_cert,
        help="Path to the client certificate file for gRPC connection (for mTLS) (auto-detected: tls/webui-client.crt or certs/sdk/taurus-sdk.crt)"
    )
    parser.add_argument(
        "--client-key-file",
        default=default_client_key,
        help="Path to the client private key file for gRPC connection (for mTLS) (auto-detected: tls/webui-client.key or certs/sdk/taurus-sdk.key)"
    )
    parser.add_argument(
        "--client-ca-file",
        default=default_client_ca,
        help="Path to the CA certificate file for gRPC connection verification (auto-detected: tls/ca.crt or certs/sdk/ca.crt)"
    )
    
    args = parser.parse_args()
    
    # Create and run the web UI
    web_ui = WebUI(
        client_address=args.client_address,
        cert_file=args.client_cert_file,
        key_file=args.client_key_file,
        ca_file=args.client_ca_file
    )
    
    # Web UI uses HTTP by default (local access), HTTPS can be manually enabled by providing certificates
    ssl_context = None
    
    # Run the application
    web.run_app(web_ui.app, host=args.host, port=args.port, ssl_context=ssl_context)


if __name__ == "__main__":
    main()