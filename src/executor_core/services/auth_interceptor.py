import grpc
import logging
from typing import Dict, Any

from executor_core.infra import permissions
from executor_core.infra.logger import add_context_to_log_record


logger = logging.getLogger(__name__)


# RPC methods whose response is server-streaming (must use unary_stream terminator)
_STREAMING_METHOD_SUFFIXES = (
    "/ExecuteCommand",
    "/ExecuteInSession",
    "/DownloadFile",
    "/ListDirectory",
)


def _terminator_for_method(code, details, handler_call_details):
    """Return an RPC terminator whose type matches the method's stream kind."""
    def terminate(ignored_request, context):
        context.abort(code, details)

    method = getattr(handler_call_details, "method", "") or ""
    if any(method.endswith(suf) for suf in _STREAMING_METHOD_SUFFIXES):
        return grpc.unary_stream_rpc_method_handler(terminate)
    return grpc.unary_unary_rpc_method_handler(terminate)


class AuthInterceptor(grpc.aio.ServerInterceptor):
    async def intercept_service(self, continuation, handler_call_details):
        # Extract metadata from the incoming call
        metadata = dict(handler_call_details.invocation_metadata)

        client_role = metadata.get("x-client-role", "viewer")

        # Ensure client_role is a string
        if isinstance(client_role, bytes):
            client_role = client_role.decode("utf-8")
        elif not isinstance(client_role, str):
            client_role = "viewer"  # Default to least privilege

        log_context: Dict[str, Any] = {
            "client_role": client_role,
            "method": handler_call_details.method,
        }
        add_context_to_log_record(log_context)

        required_permission = self._get_required_permission(handler_call_details.method)

        if required_permission:
            user_permissions = permissions.ROLE_PERMISSIONS.get(client_role, set())
            if required_permission not in user_permissions:
                logger.warning(
                    "Permission denied for role '%s' accessing method '%s'",
                    client_role,
                    handler_call_details.method,
                )
                return _terminator_for_method(
                    grpc.StatusCode.PERMISSION_DENIED,
                    f"Permission Denied: Role '{client_role}' lacks '{required_permission.value}'",
                    handler_call_details,
                )

        return await continuation(handler_call_details)

    def _get_required_permission(self, method: str) -> permissions.Permission | None:
        """Map gRPC method to required permission."""
        if method.endswith("/ExecuteCommand") or method.endswith("/ExecuteInSession"):
            return permissions.Permission.EXECUTE_COMMAND
        if method.endswith("/GetStatus"):
            return permissions.Permission.READ_STATUS
        if method.endswith("/Maintenance"):
            return permissions.Permission.TRIGGER_UPDATE
        return None