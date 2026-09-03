import pytest
import asyncio
import os
from unittest.mock import Mock, patch, AsyncMock
from executor_core.executors.command_executor import CommandExecutor, StreamEvent, StreamEventType


@pytest.fixture
def executor():
    return CommandExecutor(default_timeout=5)


@pytest.mark.asyncio
async def test_execute_stream_success(executor):
    """Test successful command execution with output."""
    events = []
    async for event in executor.execute_stream("echo", ["test"]):
        events.append(event)
    
    # Should have stdout and exit events
    assert len(events) >= 2
    stdout_events = [e for e in events if e.type == StreamEventType.STDOUT]
    exit_events = [e for e in events if e.type == StreamEventType.EXIT]
    
    assert len(stdout_events) >= 1
    assert len(exit_events) == 1
    assert exit_events[0].exit_code == 0


@pytest.mark.asyncio
async def test_execute_stream_stderr(executor):
    """Test command execution with stderr output."""
    events = []
    # Using sh -c to generate stderr output
    async for event in executor.execute_stream("sh", ["-c", "echo error >&2"]):
        events.append(event)
    
    stderr_events = [e for e in events if e.type == StreamEventType.STDERR]
    assert len(stderr_events) >= 1


@pytest.mark.asyncio
async def test_execute_stream_with_timeout(executor):
    """Test command execution timeout."""
    events = []
    async for event in executor.execute_stream("sleep", ["10"], timeout=1):
        events.append(event)
    
    error_events = [e for e in events if e.type == StreamEventType.ERROR]
    assert len(error_events) >= 1
    assert b"deadline exceeded" in error_events[0].data


@pytest.mark.asyncio
async def test_execute_stream_nonexistent_command(executor):
    """Test execution of nonexistent command."""
    events = []
    async for event in executor.execute_stream("nonexistent_command_xyz", []):
        events.append(event)
    
    error_events = [e for e in events if e.type == StreamEventType.ERROR]
    assert len(error_events) >= 1


@pytest.mark.asyncio
async def test_execute_stream_with_working_directory(executor):
    """Test command execution with custom working directory."""
    events = []
    async for event in executor.execute_stream("pwd", [], cwd="/tmp"):
        events.append(event)
    
    stdout_events = [e for e in events if e.type == StreamEventType.STDOUT]
    assert len(stdout_events) >= 1
    assert b"tmp" in stdout_events[0].data


@pytest.mark.asyncio
async def test_execute_stream_with_environment(executor):
    """Test command execution with custom environment."""
    events = []
    env = {"MY_TEST_VAR": "test_value_123"}
    async for event in executor.execute_stream("sh", ["-c", "echo $MY_TEST_VAR"], env=env):
        events.append(event)
    
    stdout_events = [e for e in events if e.type == StreamEventType.STDOUT]
    assert len(stdout_events) >= 1
    assert b"test_value_123" in stdout_events[0].data


@pytest.mark.asyncio
async def test_execute_stream_empty_command(executor):
    """Test execution with empty command."""
    events = []
    async for event in executor.execute_stream("true", []):
        events.append(event)
    
    exit_events = [e for e in events if e.type == StreamEventType.EXIT]
    assert len(exit_events) == 1
    assert exit_events[0].exit_code == 0


@pytest.mark.asyncio
async def test_execute_stream_large_output(executor):
    """Test command with large output."""
    events = []
    # Generate ~10KB of output
    async for event in executor.execute_stream("sh", ["-c", "head -c 10240 /dev/urandom | base64"]):
        events.append(event)
    
    stdout_events = [e for e in events if e.type == StreamEventType.STDOUT]
    total_bytes = sum(len(e.data) for e in stdout_events)
    assert total_bytes >= 10240


@pytest.mark.asyncio
async def test_execute_stream_exit_code_nonzero(executor):
    """Test command that exits with non-zero code."""
    events = []
    async for event in executor.execute_stream("sh", ["-c", "exit 42"]):
        events.append(event)
    
    exit_events = [e for e in events if e.type == StreamEventType.EXIT]
    assert len(exit_events) == 1
    assert exit_events[0].exit_code == 42