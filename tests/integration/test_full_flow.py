import pytest
import asyncio
import grpc
import time
from manage.sdk.client import TaurusClient
from executor_core.services.generated.executor.v1 import command_service_pb2


@pytest.fixture
def aaa():
    
    print("Setup")

# This fixture starts a real client server for the duration of the tests
@pytest.fixture(autouse=True)
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
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        raise RuntimeError("Failed to start client server within timeout")
    
    yield "localhost:50051"
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_command_execution(client_server):
    async with TaurusClient(client_server) as executor:
        outputs = []
        async for event in executor.execute_command("echo", ["hello world"]):
            outputs.append(event)
        
        assert len(outputs) >= 1
        assert b"hello world" in outputs[0]['stdout']
        assert outputs[-1]['finished'] is True
        assert outputs[-1]['exit_code'] == 0


@pytest.mark.asyncio
async def test_status_fetch(client_server):
    async with TaurusClient(client_server) as executor:
        status = await executor.get_status()
        assert "version" in status
        assert "hostname" in status


@pytest.mark.asyncio
async def test_multiple_concurrent_commands(client_server):
    """Test executing multiple commands concurrently."""
    async with TaurusClient(client_server) as executor:
        # Start multiple command executions concurrently
        tasks = [
            asyncio.create_task(collect_command_output(executor, "echo", [f"command-{i}"]))
            for i in range(3)
        ]
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks)
        
        # Verify each command produced expected output
        for i, outputs in enumerate(results):
            stdout_found = False
            for event in outputs:
                if 'stdout' in event and f"command-{i}".encode() in event['stdout']:
                    stdout_found = True
                    break
            assert stdout_found


@pytest.mark.asyncio
async def test_sequential_operations(client_server):
    """Test sequential command executions."""
    async with TaurusClient(client_server) as executor:
        # Execute first command
        outputs1 = []
        async for event in executor.execute_command("echo", ["first"]):
            outputs1.append(event)
        
        # Execute second command
        outputs2 = []
        async for event in executor.execute_command("echo", ["second"]):
            outputs2.append(event)
        
        # Verify both commands completed successfully
        assert any(b"first" in event.get('stdout', b'') for event in outputs1)
        assert any(b"second" in event.get('stdout', b'') for event in outputs2)
        assert outputs1[-1]['finished']
        assert outputs2[-1]['finished']


async def collect_command_output(client, command, args):
    """Helper function to collect command output."""
    outputs = []
    async for event in client.execute_command(command, args):
        outputs.append(event)
    return outputs


@pytest.mark.asyncio
async def test_long_running_command_with_timeout(client_server):
    """Test that we can timeout a long-running command."""
    async with TaurusClient(client_server) as executor:
        request = command_service_pb2.CommandRequest(
            command="sleep",
            args=["5"],  # Sleep for 5 seconds
            timeout_seconds=1  # But timeout after 1 second
        )
        
        outputs = []
        start_time = time.time()
        async for event in executor.execute_command_stream(request):
            outputs.append(event)
        end_time = time.time()
        
        # Should finish quickly due to timeout (less than 2 seconds)
        assert (end_time - start_time) < 2.0
        
        # Should have an error message about timeout
        error_found = False
        for event in outputs:
            if 'error_message' in event and 'timeout' in event['error_message'].lower():
                error_found = True
                break
        
        assert error_found


@pytest.mark.asyncio
async def test_nonexistent_command(client_server):
    """Test executing a nonexistent command."""
    async with TaurusClient(client_server) as executor:
        outputs = []
        async for event in executor.execute_command("nonexistent_command_xyz", []):
            outputs.append(event)
            
        # Should get an error message
        error_found = False
        for event in outputs:
            if 'error' in event and len(event['error']) > 0:
                error_found = True
                break
                
        assert error_found