#!/usr/bin/env python3
"""
Taurus Executor CLI tool
"""

import argparse
import asyncio
import os
import sys
from typing import Optional
import getpass
import signal

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from manage.sdk.client import TaurusClient


async def execute_command(
    address: str,
    cmd: str,
    args: list[str],
    timeout: int = 30,
    cert_file: Optional[str] = None,
    key_file: Optional[str] = None,
    ca_file: Optional[str] = None,
    privileged: bool = False,
    sudo_user: Optional[str] = None,
    su_user: Optional[str] = None,
    role: str = "operator",
    merge_streams: bool = False,
):
    """Execute a command on the remote client."""
    try:
        # Handle optional SSL parameters
        client_cert_file = (
            cert_file if cert_file and os.path.exists(cert_file) else None
        )
        client_key_file = key_file if key_file and os.path.exists(key_file) else None
        client_ca_file = ca_file if ca_file and os.path.exists(ca_file) else None

        # Check if certificate files exist
        missing_certs = []
        if cert_file and not client_cert_file:
            missing_certs.append(cert_file)
        if key_file and not client_key_file:
            missing_certs.append(key_file)
        if ca_file and not client_ca_file:
            missing_certs.append(ca_file)

        if missing_certs:
            msg = f"Warning: Missing certificate files: {missing_certs}. Will use insecure connection."
            if RICH_AVAILABLE:
                console = Console()
                console.print(f"[yellow]{msg}[/yellow]")
            else:
                print(msg)

        # Create client instance and keep reference
        client = TaurusClient(
            address, client_cert_file, client_key_file, client_ca_file, role
        )
        
        # Store references for async tasks
        execution_task = None
        execution_finished = False
        
        # Signal handler - for Ctrl+C
        def signal_handler(sig, frame):
            nonlocal execution_finished, execution_task
            if not execution_finished and execution_task:
                print("\nReceived interrupt signal. Cancelling command execution...")
                # Cancel execution task
                execution_task.cancel()
            sys.exit(1)
        
        # Set up signal handling
        signal.signal(signal.SIGINT, signal_handler)

        try:
            await client.__aenter__()  # Equivalent to async with client:
            
            full_cmd = [cmd] + args
            command_str = " ".join(full_cmd)
            
            # Prepare environment variables
            environment = {}
            if privileged:
                environment["PRIVILEGED_EXECUTION"] = "true"
                if sudo_user:
                    environment["SUDO_USER"] = sudo_user
                if su_user:
                    environment["SU_USER"] = su_user
                    
                # Prompt user for password (if needed)
                if su_user:
                    # Use su to switch user
                    if RICH_AVAILABLE:
                        console = Console()
                        user_password = console.input(f"[bold yellow]Enter password for user '{su_user}' (input will be hidden): [/bold yellow]", password=True)
                    else:
                        user_password = getpass.getpass(f"Enter password for user '{su_user}': ")
                    environment["SU_PASSWORD"] = user_password
                elif sudo_user or not sudo_user:
                    # Use sudo to switch user or elevate privileges
                    if RICH_AVAILABLE:
                        console = Console()
                        sudo_password = console.input("[bold yellow]Enter sudo password (input will be hidden): [/bold yellow]", password=True)
                    else:
                        sudo_password = getpass.getpass("Enter sudo password: ")
                    environment["SUDO_PASSWORD"] = sudo_password

            if RICH_AVAILABLE:
                console = Console()
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task(f"Executing: {command_str}", total=None)

                    # Create async task for command execution
                    async def run_execution():
                        try:
                            async for response in client.execute_command(cmd, args, timeout, environment, merge_streams):
                                stdout = response.get("stdout", b"").decode(
                                    "utf-8", errors="replace"
                                )
                                stderr = response.get("stderr", b"").decode(
                                    "utf-8", errors="replace"
                                )

                                if stdout:
                                    console.print(stdout, end="")
                                if stderr:
                                    console.print(f"[red]{stderr}[/red]", end="")

                                if response.get("finished", False):
                                    exit_code = response.get("exit_code", 0)
                                    if exit_code == 0:
                                        progress.update(
                                            task,
                                            description=f"[green]✓ Command completed successfully[/green]",
                                        )
                                    else:
                                        progress.update(
                                            task,
                                            description=f"[red]✗ Command failed with exit code {exit_code}[/red]",
                                        )
                                    return exit_code
                                elif response.get("error"):
                                    error_msg = response.get("error", "")
                                    progress.update(
                                        task,
                                        description=f"[red]✗ Command failed: {error_msg}[/red]",
                                    )
                                    return 1
                        except asyncio.CancelledError:
                            progress.update(
                                task,
                                description="[yellow]Command execution cancelled[/yellow]",
                            )
                            return 130  # Unix standard cancel exit code
                            
                    # Start execution task
                    execution_task = asyncio.create_task(run_execution())
                    
                    # Wait for task to complete
                    try:
                        exit_code = await execution_task
                        execution_finished = True
                        return exit_code
                    except asyncio.CancelledError:
                        execution_finished = True
                        return 130
                        
            else:
                # Simplified version without rich
                async def run_execution_simple():
                    try:
                        async for response in client.execute_command(cmd, args, timeout, environment, merge_streams):
                            stdout = response.get("stdout", b"").decode(
                                "utf-8", errors="replace"
                            )
                            stderr = response.get("stderr", b"").decode(
                                "utf-8", errors="replace"
                            )

                            if stdout:
                                print(stdout, end="", flush=True)
                            if stderr:
                                print(stderr, end="", flush=True)

                            if response.get("finished", False):
                                exit_code = response.get("exit_code", 0)
                                return exit_code
                            elif response.get("error"):
                                print(f"Error: {response.get('error', '')}")
                                return 1
                    except asyncio.CancelledError:
                        print("\nCommand execution cancelled.")
                        return 130
                
                # Start execution task
                execution_task = asyncio.create_task(run_execution_simple())
                
                # Wait for task to complete
                try:
                    exit_code = await execution_task
                    execution_finished = True
                    return exit_code
                except asyncio.CancelledError:
                    execution_finished = True
                    return 130
                    
        finally:
            # Ensure client is properly closed
            try:
                await client.__aexit__(None, None, None)
            except:
                pass

    except Exception as e:
        msg = (
            f"[red]Connection error:[/red] {e}"
            if RICH_AVAILABLE
            else f"Connection error: {e}"
        )
        if 'console' in locals():
            console.print(msg)
        else:
            print(msg)
        return 1


async def get_status(
    address: str,
    cert_file: Optional[str] = None,
    key_file: Optional[str] = None,
    ca_file: Optional[str] = None,
    role: str = "operator",
):
    """Get the status of the remote client."""
    if RICH_AVAILABLE:
        console = Console()
    else:

        class MockConsole:
            def print(self, *args, **kwargs):
                print(*args, **kwargs)

        console = MockConsole()

    try:
        # Handle optional SSL parameters
        client_cert_file = (
            cert_file if cert_file and os.path.exists(cert_file) else None
        )
        client_key_file = key_file if key_file and os.path.exists(key_file) else None
        client_ca_file = ca_file if ca_file and os.path.exists(ca_file) else None

        async with TaurusClient(
            address, client_cert_file, client_key_file, client_ca_file, role
        ) as client:
            if RICH_AVAILABLE:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task("Fetching status...", total=None)
                    status = await client.get_status()
                    progress.stop()

                    table = Table(title="Taurus Executor Status")
                    table.add_column("Property", style="cyan")
                    table.add_column("Value", style="magenta")

                    table.add_row("Version", status["version"])
                    table.add_row("Uptime", status["uptime"])
                    table.add_row("Hostname", status["hostname"])
                    table.add_row("CPU Usage", f"{status['cpu_usage']:.2f}%")
                    table.add_row("Memory Usage", f"{status['memory_usage']:.2f}%")

                    console.print(table)
                    return 0
            else:
                # Simplified version without rich
                status = await client.get_status()
                print("Taurus Executor Status:")
                print(f"  Version: {status['version']}")
                print(f"  Uptime: {status['uptime']}")
                print(f"  Hostname: {status['hostname']}")
                print(f"  CPU Usage: {status['cpu_usage']:.2f}%")
                print(f"  Memory Usage: {status['memory_usage']:.2f}%")
                return 0

    except Exception as e:
        (
            console.print(f"[red]Connection error:[/red] {e}")
            if RICH_AVAILABLE
            else print(f"Connection error: {e}")
        )
        return 1


def main():
    parser = argparse.ArgumentParser(description="Taurus Executor CLI")
    parser.add_argument(
        "--address",
        "-a",
        default="localhost:50051",
        help="Address of the client (host:port)",
    )

    default_cert_file = os.path.join(os.path.dirname(__file__), "tls", "client.crt")
    default_key_file = os.path.join(os.path.dirname(__file__), "tls", "client.key")
    default_ca_file = os.path.join(os.path.dirname(__file__), "tls", "ca.crt")

    # SSL/TLS parameters
    parser.add_argument(
        "--cert-file",
        default=default_cert_file,
        help="Path to the client certificate file (for mTLS)",
    )
    parser.add_argument(
        "--key-file",
        default=default_key_file,
        help="Path to the client private key file (for mTLS)",
    )
    parser.add_argument(
        "--ca-file",
        default=default_ca_file,
        help="Path to the CA certificate file (for server verification)",
    )
    
    # Role parameters
    parser.add_argument(
        "--role",
        "-r",
        default="operator",
        help="Role for the client (admin, operator, viewer, etc.)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Execute command
    exec_parser = subparsers.add_parser("exec", help="Execute a command")
    exec_parser.add_argument("cmd", help="Command to execute")
    exec_parser.add_argument("args", nargs=argparse.REMAINDER, help="Command arguments")
    exec_parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=30,
        help="Command timeout in seconds",
    )
    # Privileged execution options
    exec_parser.add_argument(
        "--privileged",
        "-p",
        action="store_true",
        help="Execute command with elevated privileges",
    )
    exec_parser.add_argument(
        "--sudo-user",
        "-u",
        help="User to run command as (requires NOPASSWD sudo)",
    )
    exec_parser.add_argument(
        "--su-user",
        "-s",
        help="Switch to user using su (requires password)",
    )
    exec_parser.add_argument(
        "--merge-streams",
        action="store_true",
        help="Merge stderr into stdout to preserve output order",
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Get client status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "exec":
        exit_code = asyncio.run(
            execute_command(
                args.address,
                args.cmd,
                args.args,
                args.timeout,
                args.cert_file,
                args.key_file,
                args.ca_file,
                args.privileged,
                args.sudo_user,
                args.su_user,
                args.role,
                args.merge_streams,
            )
        )
        sys.exit(exit_code)
    elif args.command == "status":
        exit_code = asyncio.run(
            get_status(
                args.address,
                args.cert_file,
                args.key_file,
                args.ca_file,
                args.role,
            )
        )
        sys.exit(exit_code)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    main()