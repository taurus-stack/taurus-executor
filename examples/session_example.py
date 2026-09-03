"""
Interactive session usage examples

Demonstrates how to use Taurus session persistence:
1. Create a session (optionally specify user and password)
2. Execute multiple commands in the session (state preserved)
3. Close the session
"""
import asyncio
import sys
import os

# Add executor path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'executor'))

from executor_core.executor import TaurusExecutor


async def example_basic_session():
    """Basic session example (using current user)"""
    print("=" * 60)
    print("Example 1: Basic session (current user)")
    print("=" * 60)
    
    async with TaurusExecutor("localhost:50051") as executor:
        # Create session
        session = await executor.create_session(
            working_directory="/tmp",
        )
        print(f"✓ Session created successfully: {session.session_id}")
        
        # Execute command 1: view current directory
        print("\nExecuting: pwd")
        async for output in session.execute("pwd"):
            if "stdout" in output:
                print(f"  Output: {output['stdout'].decode()}")
        
        # Execute command 2: change directory
        print("\nExecuting: cd /var/log")
        async for output in session.execute("cd /var/log"):
            if "stdout" in output:
                print(f"  Output: {output['stdout'].decode()}")
        
        # Execute command 3: verify directory changed
        print("\nExecuting: pwd")
        async for output in session.execute("pwd"):
            if "stdout" in output:
                print(f"  Output: {output['stdout'].decode()}")
        
        # Execute command 4: list files
        print("\nExecuting: ls -la | head -5")
        async for output in session.execute("ls -la | head -5"):
            if "stdout" in output:
                print(f"  Output: {output['stdout'].decode()}")
        
        # Close session
        await session.close()
        print("\n✓ Session closed")


async def example_user_session():
    """User session example (using su to switch to another user)"""
    print("\n" + "=" * 60)
    print("Example 2: User session (su switch to deploy user)")
    print("=" * 60)
    
    async with TaurusExecutor("localhost:50051") as executor:
        # Create session (requires username and password)
        try:
            session = await executor.create_session(
                username="deploy",
                password="your_password_here",  # Replace with actual password
                working_directory="/opt/app",
            )
            print(f"✓ User session created successfully: {session.session_id}")
            
            # Verify user identity
            print("\nExecuting: whoami")
            async for output in session.execute("whoami"):
                if "stdout" in output:
                    print(f"  Output: {output['stdout'].decode()}")
            
            # Execute command
            print("\nExecuting: pwd")
            async for output in session.execute("pwd"):
                if "stdout" in output:
                    print(f"  Output: {output['stdout'].decode()}")
            
            # Close session
            await session.close()
            print("\n✓ Session closed")
            
        except RuntimeError as e:
            print(f"✗ Failed to create user session: {e}")
            print("  Hint: Ensure user exists and password is correct")


async def example_list_sessions():
    """List all active sessions"""
    print("\n" + "=" * 60)
    print("Example 3: List all active sessions")
    print("=" * 60)
    
    async with TaurusExecutor("localhost:50051") as executor:
        sessions = await executor.list_sessions()
        
        if not sessions:
            print("No active sessions")
            return
        
        print(f"Active sessions: {len(sessions)}\n")
        for s in sessions:
            print(f"Session ID: {s['session_id']}")
            print(f"  User: {s['username']}")
            print(f"  Working directory: {s['working_directory']}")
            print(f"  Created at: {s['created_at']}")
            print(f"  Last active: {s['last_active']}")
            print(f"  Status: {'Active' if s['is_alive'] else 'Closed'}")
            print()


async def example_environment_variables():
    """Environment variables example"""
    print("\n" + "=" * 60)
    print("Example 4: Session with environment variables")
    print("=" * 60)
    
    async with TaurusExecutor("localhost:50051") as executor:
        # Create session with environment variables
        session = await executor.create_session(
            environment={
                "MY_APP_ENV": "production",
                "MY_APP_DEBUG": "false",
            }
        )
        print(f"✓ Session created successfully: {session.session_id}")
        
        # Verify environment variables
        print("\nExecuting: echo $MY_APP_ENV")
        async for output in session.execute("echo $MY_APP_ENV"):
            if "stdout" in output:
                print(f"  Output: {output['stdout'].decode()}")
        
        print("\nExecuting: echo $MY_APP_DEBUG")
        async for output in session.execute("echo $MY_APP_DEBUG"):
            if "stdout" in output:
                print(f"  Output: {output['stdout'].decode()}")
        
        # Close session
        await session.close()
        print("\n✓ Session closed")


async def main():
    """Run all examples"""
    try:
        # Run basic session example
        await example_basic_session()
        
        # Run list sessions example
        await example_list_sessions()
        
        # Run environment variables example
        await example_environment_variables()
        
        # Run user session example (requires password modification)
        # await example_user_session()
        
    except Exception as e:
        print(f"\n✗ Example execution failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())