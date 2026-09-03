import pytest
import asyncio
import grpc
from manage.sdk.client import TaurusClient
from executor_core.services.generated.executor.v1 import command_service_pb2


# This fixture starts a real client server for the duration of the tests
@pytest.fixture(scope="function")
async def client_server():
    # Import and run the main server in the background
    from executor_core.services.server import serve

    server_task = asyncio.create_task(serve())
    
    # Wait for server to start and verify it's accepting connections
    max_retries = 10
    retry_count = 0
    while retry_count < max_retries:
        try:
            # Try to establish a connection to verify server is running
            channel = grpc.aio.insecure_channel("localhost:50051")
            await asyncio.wait_for(channel.channel_ready(), timeout=1.0)
            await channel.close()
            break
        except (asyncio.TimeoutError, OSError):
            retry_count += 1
            await asyncio.sleep(0.5)
    
    if retry_count >= max_retries:
        server_task.cancel()
        raise RuntimeError("Failed to start client server within timeout")
    
    yield "localhost:50051"
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


@pytest.fixture
async def client_address(client_server):
    """Extract the address from the client_server fixture."""
    return "localhost:50051" 


@pytest.mark.asyncio
async def test_command_execution_simple(client_address):
    """Test simple command execution."""
    async with TaurusClient(client_address, secure=False) as client:
        outputs = []
        async for event in client.execute_command("echo", ["hello world"]):
            outputs.append(event)

        # Should have stdout and finish events
        assert len(outputs) >= 2

        # Check stdout contains our message
        stdout_found = False
        for event in outputs:
            if "stdout" in event and b"hello world" in event["stdout"]:
                stdout_found = True
                break

        assert stdout_found

        # Check that process finished successfully
        assert any(event.get("finished") and event.get("exit_code") == 0 for event in outputs)


@pytest.mark.asyncio
async def test_command_execution_with_error(client_address):
    """Test command execution that results in an error."""
    async with TaurusClient(client_address, secure=False) as client:
        outputs = []
        async for event in client.execute_command("nonexistent_command_xyz"):
            outputs.append(event)

        # Should have stderr and finish events
        assert len(outputs) >= 1

        # Process should finish with non-zero exit code
        assert any(event.get("finished") and event.get("exit_code") != 0 for event in outputs)


@pytest.mark.asyncio
async def test_long_running_command_cancel(client_address):
    """Test cancelling a long-running command."""
    async with TaurusClient(client_address, secure=False) as client:
        outputs = []
        async for event in client.execute_command("sleep", ["5"]):
            outputs.append(event)
            # Cancel after first event (should be start event)
            break

        # Should have at least one event
        assert len(outputs) >= 1


@pytest.mark.asyncio
async def test_get_status(client_address):
    """Test getting executor status."""
    async with TaurusClient(client_address, secure=False) as client:
        status = await client.get_status()
        
        # Status should contain expected fields
        assert "version" in status
        assert "uptime" in status
        assert "hostname" in status
        assert "cpu_usage" in status
        assert "memory_usage" in status
        
        # Values should be reasonable
        assert isinstance(status["uptime"], float)
        assert status["uptime"] >= 0
        assert isinstance(status["cpu_usage"], float)
        assert 0 <= status["cpu_usage"] <= 100
        assert isinstance(status["memory_usage"], float)
        assert 0 <= status["memory_usage"] <= 100