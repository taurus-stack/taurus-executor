"""
Ticket authentication interceptor
Validates tickets before each command execution
"""
import grpc
import aiohttp
import asyncio
import logging
from typing import Dict, Any

from executor_core.infra.config import settings
from executor_core.infra.logger import add_context_to_log_record

logger = logging.getLogger(__name__)


def _unary_unary_rpc_terminator(code, details):
    """Create unary-unary RPC terminator"""
    def terminate(ignored_request, context):
        context.abort(code, details)
    return grpc.unary_unary_rpc_method_handler(terminate)


def _unary_stream_rpc_terminator(code, details):
    """Create unary-stream RPC terminator"""
    def terminate(ignored_request, context):
        context.abort(code, details)
    return grpc.unary_stream_rpc_method_handler(terminate)


# Methods that do NOT require a ticket (health check / maintenance probes)
_TICKET_WHITELIST_METHODS = frozenset([
    "/GetStatus",
    "/Maintenance",
    "/ListExecutions",
])


def _method_is_streaming(method: str) -> bool:
    """Return True if the gRPC method uses server-streaming response."""
    return method.endswith("/ExecuteCommand") or method.endswith("/ExecuteInSession") \
        or method.endswith("/DownloadFile") or method.endswith("/ListDirectory")


class TicketAuthInterceptor(grpc.aio.ServerInterceptor):
    """
    Ticket authentication interceptor

    Workflow:
    1. Whitelisted probe methods (GetStatus / Maintenance / ListExecutions) pass through
    2. For command-execution methods: extract ticket from gRPC metadata
    3. Call taurus-auth to verify the ticket
    4. Decide whether to allow the request based on verification result
    """

    def __init__(self):
        self.enabled = settings.ticket_auth_enabled
        self.auth_service_url = settings.auth_service_url.rstrip('/')
        self.timeout = settings.auth_verify_timeout
        self.retry_count = settings.auth_verify_retry_count
        self.fallback_policy = settings.auth_fallback_policy
        self.http_client = None

        if self.enabled:
            logger.info(f"Ticket authentication enabled: auth_url={self.auth_service_url}, fallback={self.fallback_policy}")
        else:
            logger.info("Ticket authentication disabled, all requests will be passed through")

    async def _get_http_client(self) -> aiohttp.ClientSession:
        """Get HTTP client (lazy loading)"""
        if self.http_client is None or self.http_client.closed:
            self.http_client = aiohttp.ClientSession()
        return self.http_client

    def _deny_access(self, reason: str, handler_call_details=None):
        """Deny access - return the proper terminator type matching the RPC method."""
        method = handler_call_details.method if handler_call_details else ""
        if _method_is_streaming(method):
            return _unary_stream_rpc_terminator(
                grpc.StatusCode.PERMISSION_DENIED,
                reason,
            )
        return _unary_unary_rpc_terminator(
            grpc.StatusCode.PERMISSION_DENIED,
            reason,
        )

    async def intercept_service(self, continuation, handler_call_details):
        """Intercept gRPC request and verify ticket"""
        # If ticket authentication is not enabled, pass through directly
        if not self.enabled:
            return await continuation(handler_call_details)

        # Whitelisted probe methods: no ticket required (avoids GetStatus health check hang)
        method = handler_call_details.method
        if any(method.endswith(suffix) for suffix in _TICKET_WHITELIST_METHODS):
            return await continuation(handler_call_details)

        # 1. Extract ticket
        metadata = dict(handler_call_details.invocation_metadata)
        ticket = metadata.get('x-command-ticket')

        if not ticket:
            logger.warning("Missing ticket for %s", method)
            return self._deny_access("Missing ticket", handler_call_details)
        
        # 2. Call taurus-auth to verify
        try:
            verify_result = await self._verify_ticket(ticket)
        except Exception as e:
            logger.error(f"Ticket verification service unavailable: {e}")
            # Handle according to fallback policy
            if self.fallback_policy == "allow":
                logger.warning("Ticket verification service unavailable, allowing with permissive policy")
                return await continuation(handler_call_details)
            else:
                return self._deny_access("Ticket verification service unavailable", handler_call_details)

        # 3. Check verification result
        if not verify_result.get('valid'):
            reason = verify_result.get('reason', 'Unknown error')
            logger.warning(f"Ticket verification failed: {reason}")
            return self._deny_access(f"Ticket verification failed: {reason}", handler_call_details)

        # 4. Verification passed, allow request
        host_uuid = verify_result.get('host_uuid')
        action = verify_result.get('action')

        logger.info(f"Ticket verification passed: host={host_uuid}, action={action}")

        # Add context to log
        add_context_to_log_record({
            'host_uuid': host_uuid,
            'action': action,
        })

        return await continuation(handler_call_details)

    async def _verify_ticket(self, ticket: str) -> Dict[str, Any]:
        """
        Call taurus-auth to verify ticket
        
        Args:
            ticket: Ticket string
            
        Returns:
            Verification result dictionary
        """
        url = f"{self.auth_service_url}/api/v1/tickets/verify"
        
        http_client = await self._get_http_client()
        
        for attempt in range(self.retry_count + 1):
            try:
                async with http_client.post(
                    url,
                    json={"ticket": ticket},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    result = await response.json()
                    return result.get('data', {})
            except aiohttp.ClientError as e:
                logger.warning(
                    f"Ticket verification request failed (attempt {attempt + 1}/{self.retry_count + 1}): {e}"
                )
                if attempt == self.retry_count:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
            except asyncio.TimeoutError as e:
                logger.warning(
                    f"Ticket verification timeout (attempt {attempt + 1}/{self.retry_count + 1}): {e}"
                )
                if attempt == self.retry_count:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        
        return {"valid": False, "reason": "Verification service unavailable"}

    async def close(self):
        """Close HTTP client"""
        if self.http_client and not self.http_client.closed:
            await self.http_client.close()