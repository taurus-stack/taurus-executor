import pytest
import asyncio
import grpc
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from manage.sdk.client import TaurusClient, Session, find_default_certificates
from manage.sdk.exceptions import (
    ConnectionError as TaurusConnectionError,
    CommandExecutionError,
    CommandTimeoutError,
    AuthenticationError,
    ServerError,
    InvalidRequestError,
)


class TestTaurusClientInit:
    def test_init_with_string_address_insecure(self):
        address = "localhost:50051"
        client = TaurusClient(address, secure=False)
        assert client.address == address
        assert client.channel is None
        assert client.stub is None
        assert client.secure is False
        assert client.cert_file is None
        assert client.key_file is None
        assert client.ca_file is None

    def test_init_with_explicit_certs_sets_secure_true(self):
        client = TaurusClient(
            "localhost:50051",
            cert_file="client.crt",
            key_file="client.key",
            ca_file="ca.crt",
            secure=True,
        )
        assert client.secure is True
        assert client.cert_file == "client.crt"
        assert client.key_file == "client.key"
        assert client.ca_file == "ca.crt"

    def test_init_custom_role_and_target_name(self):
        client = TaurusClient(
            "h:50051", role="admin", target_name="custom-target", secure=False
        )
        assert client.role == "admin"
        assert client.target_name == "custom-target"

    def test_init_default_target_name_when_not_provided(self):
        client = TaurusClient("h:1", secure=False)
        assert client.target_name == TaurusClient.GRPC_TARGET_NAME


class TestTaurusClientContextManager:
    @pytest.mark.asyncio
    async def test_context_manager_insecure_creates_channel_and_stub(self):
        with patch("grpc.aio.insecure_channel") as mock_insecure_channel:
            mock_channel_instance = MagicMock()
            mock_insecure_channel.return_value = mock_channel_instance

            client = TaurusClient("localhost:50051", secure=False)
            async with client as c:
                assert c is client
                assert c.channel is mock_channel_instance
                assert c.stub is not None
                assert c.metadata == [("x-client-role", "operator")]
                mock_insecure_channel.assert_called_once()

            # close must have been awaited
            mock_channel_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_secure_loads_certs_and_uses_secure_channel(self):
        cert_bytes = b"cert_data"
        key_bytes = b"key_data"
        ca_bytes = b"ca_data"
        m_open = mock_open()
        m_open.side_effect = [
            mock_open(read_data=cert_bytes).return_value,
            mock_open(read_data=key_bytes).return_value,
            mock_open(read_data=ca_bytes).return_value,
        ]
        with patch("grpc.aio.secure_channel") as mock_secure_channel, patch(
            "builtins.open", m_open
        ), patch("grpc.ssl_channel_credentials") as mock_ssl_creds:
            mock_secure_channel.return_value = MagicMock()
            mock_ssl_creds.return_value = MagicMock()

            client = TaurusClient(
                "localhost:50051",
                cert_file="c.crt",
                key_file="k.key",
                ca_file="ca.crt",
                secure=True,
            )
            async with client as c:
                assert c.channel is not None
                assert c.stub is not None
                mock_ssl_creds.assert_called_once_with(
                    root_certificates=ca_bytes,
                    private_key=key_bytes,
                    certificate_chain=cert_bytes,
                )
                mock_secure_channel.assert_called_once()


def _mock_chunk(**fields):
    m = MagicMock()
    m.stdout_chunk = b""
    m.stderr_chunk = b""
    m.finished = False
    m.exit_code = 0
    m.error_message = ""
    for k, v in fields.items():
        setattr(m, k, v)
    return m


def _asynciter(values):
    async def gen():
        for v in values:
            yield v

    return gen()


class TestTaurusClientExecuteCommand:
    @pytest.mark.asyncio
    async def test_execute_command_requires_connection(self):
        client = TaurusClient("h:1", secure=False)
        with pytest.raises(RuntimeError, match="Client not connected"):
            async for _ in client.execute_command("echo", ["hi"]):
                pass

    @pytest.mark.asyncio
    async def test_execute_command_success_decodes_to_strings(self):
        mock_stub = AsyncMock()
        mock_stub.ExecuteCommand.return_value = _asynciter(
            [_mock_chunk(stdout_chunk=b"hello world", finished=True, exit_code=0)]
        )

        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = [("x-client-role", "operator")]

        events = [e async for e in client.execute_command("echo", ["hello world"])]
        assert len(events) == 1
        assert events[0]["stdout"] == "hello world"
        assert events[0]["finished"] is True
        assert events[0]["exit_code"] == 0
        assert "error" not in events[0] or events[0].get("error") == ""

        # verify the request
        mock_stub.ExecuteCommand.assert_called_once()
        request = mock_stub.ExecuteCommand.call_args[0][0]
        assert request.command == "echo"
        assert list(request.args) == ["hello world"]

    @pytest.mark.asyncio
    async def test_execute_command_stderr_and_finish(self):
        mock_stub = AsyncMock()
        mock_stub.ExecuteCommand.return_value = _asynciter(
            [
                _mock_chunk(stderr_chunk=b"boom"),
                _mock_chunk(finished=True, exit_code=1),
            ]
        )
        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = []

        events = [e async for e in client.execute_command("badcmd")]
        assert len(events) == 2
        assert events[0]["stderr"] == "boom"
        assert events[1]["finished"] is True
        assert events[1]["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_execute_command_sets_shell_env_flag(self):
        mock_stub = AsyncMock()
        mock_stub.ExecuteCommand.return_value = _asynciter(
            [_mock_chunk(finished=True, exit_code=0)]
        )
        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = []

        async for _ in client.execute_command("ls /tmp", shell=True, load_profile="login"):
            pass

        request = mock_stub.ExecuteCommand.call_args[0][0]
        assert request.environment["__TAURUS_USE_SHELL__"] == "true"
        assert request.environment["__TAURUS_LOAD_PROFILE__"] == "login"

    @pytest.mark.asyncio
    async def test_execute_command_yields_error_on_grpc_failure(self):
        mock_stub = AsyncMock()
        err = grpc.aio.AioRpcError(
            grpc.StatusCode.UNAVAILABLE, None, details="down", trailing_metadata=None
        )

        async def failing():
            yield _mock_chunk(stdout_chunk=b"partial")
            raise err

        mock_stub.ExecuteCommand.return_value = failing()
        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = []
        events = [e async for e in client.execute_command("x")]
        assert events[0]["stdout"] == "partial"
        assert "gRPC error" in events[-1]["error"]


class TestTaurusClientGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_requires_connection(self):
        client = TaurusClient("h:1", secure=False)
        with pytest.raises(RuntimeError, match="Client not connected"):
            await client.get_status()

    @pytest.mark.asyncio
    async def test_get_status_returns_dict(self):
        mock_stub = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.version = "1.0.0"
        mock_resp.uptime = "100s"
        mock_resp.hostname = "test-host"
        mock_resp.cpu_usage = 25.0
        mock_resp.memory_usage = 50.0
        mock_stub.GetStatus.return_value = mock_resp

        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = []
        status = await client.get_status()

        assert status["version"] == "1.0.0"
        assert status["uptime"] == "100s"
        assert status["hostname"] == "test-host"
        assert status["cpu_usage"] == 25.0
        assert status["memory_usage"] == 50.0
        mock_stub.GetStatus.assert_called_once()


class TestTaurusClientOtherActions:
    @pytest.mark.asyncio
    async def test_send_signal(self):
        mock_stub = AsyncMock()
        mock_stub.SendSignal.return_value = MagicMock(success=True, message="ok")
        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = []
        r = await client.send_signal(1234, 9)
        assert r["success"] is True
        assert r["message"] == "ok"

    @pytest.mark.asyncio
    async def test_list_executions(self):
        mock_stub = AsyncMock()
        e1 = MagicMock(
            execution_id="id1",
            command="ls",
            args=["-la"],
            pid=100,
            status="running",
            started_at="t1",
        )
        mock_stub.ListExecutions.return_value = MagicMock(executions=[e1])

        client = TaurusClient("h:1", secure=False)
        client.stub = mock_stub
        client.metadata = []
        r = await client.list_executions()
        assert len(r) == 1
        assert r[0]["execution_id"] == "id1"
        assert r[0]["command"] == "ls"
        assert r[0]["args"] == ["-la"]
        assert r[0]["pid"] == 100
        assert r[0]["status"] == "running"


class TestSession:
    @pytest.mark.asyncio
    async def test_session_execute_yields_events(self):
        stub = AsyncMock()
        stub.ExecuteInSession.return_value = _asynciter(
            [
                _mock_chunk(stdout_chunk=b"out1"),
                _mock_chunk(stderr_chunk=b"err1"),
                _mock_chunk(finished=True, exit_code=0),
            ]
        )
        s = Session("sess-1", stub, metadata=[("x-client-role", "op")])
        assert s.session_id == "sess-1"
        assert s.is_active is True

        events = [e async for e in s.execute("echo hi")]
        assert events[0]["stdout"] == "out1"
        assert events[1]["stderr"] == "err1"
        assert events[2]["finished"] is True
        assert events[2]["exit_code"] == 0
        assert s.is_active is True

    @pytest.mark.asyncio
    async def test_session_close_deactivates(self):
        stub = AsyncMock()
        stub.CloseSession.return_value = MagicMock(success=True, message="bye")
        s = Session("sess-2", stub, metadata=[])
        ok = await s.close()
        assert ok is True
        assert s.is_active is False
        # Calling close again returns False (no-op)
        ok2 = await s.close()
        assert ok2 is False


class TestExceptions:
    def test_exception_hierarchy(self):
        from manage.sdk.exceptions import TaurusClientError

        assert issubclass(TaurusConnectionError, TaurusClientError)
        assert issubclass(CommandExecutionError, TaurusClientError)
        assert issubclass(CommandTimeoutError, TaurusClientError)
        assert issubclass(AuthenticationError, TaurusClientError)
        assert issubclass(ServerError, TaurusClientError)
        assert issubclass(InvalidRequestError, TaurusClientError)
        e = CommandTimeoutError("timed out")
        assert isinstance(e, TaurusClientError)
        assert str(e) == "timed out"