"""Test to diagnose pylast library serialization issues."""

import asyncio
import threading
import time
from typing import Any
from unittest.mock import patch

import pylast
import pytest


class TestPylastSerialization:
    """Test pylast library for internal serialization mechanisms."""

    @pytest.mark.asyncio
    async def test_pylast_internal_serialization(self):
        """Test if pylast itself serializes requests even with separate instances."""
        
        def create_and_use_pylast_client(call_id: int) -> dict[str, Any]:
            """Create separate pylast client and make request."""
            thread_id = threading.get_ident()
            start_time = time.time()
            
            print(f"   Pylast Call {call_id}: Creating client on thread {thread_id}")
            
            # Create completely separate pylast client
            try:
                client = pylast.LastFMNetwork(
                    api_key="test_key",
                    api_secret=None,
                    username=None
                )
                client_id = id(client)
                
                print(f"   Pylast Call {call_id}: Client {client_id} created on thread {thread_id}")
                
                # Mock the actual network call to isolate pylast behavior
                with patch.object(client, '_request') as mock_request:
                    # Simulate network delay
                    def mock_network_delay(*args, **kwargs):
                        time.sleep(1.0)  # 1 second delay
                        return {'track': {'name': f'TestTrack_{call_id}', 'artist': {'name': 'TestArtist'}}}
                    
                    mock_request.side_effect = mock_network_delay
                    
                    # Make the call
                    print(f"   Pylast Call {call_id}: Starting get_track on client {client_id}")
                    client.get_track("TestArtist", f"TestTrack_{call_id}")
                    
                    end_time = time.time()
                    duration = end_time - start_time
                    
                    print(f"   Pylast Call {call_id}: Completed on thread {thread_id} after {duration:.3f}s")
                    
                    return {
                        "call_id": call_id,
                        "thread_id": thread_id,
                        "client_id": client_id,
                        "duration": duration,
                        "success": True
                    }
                    
            except Exception as e:
                end_time = time.time()
                duration = end_time - start_time
                print(f"   Pylast Call {call_id}: Error {e} after {duration:.3f}s")
                return {
                    "call_id": call_id,
                    "thread_id": thread_id,
                    "duration": duration,
                    "success": False,
                    "error": str(e)
                }
        
        print("\n🎵 Testing pylast library concurrency")
        
        num_calls = 5
        overall_start = time.time()
        
        # Use same pattern as our Last.fm code
        tasks = [
            asyncio.create_task(asyncio.to_thread(create_and_use_pylast_client, i))
            for i in range(num_calls)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        overall_duration = time.time() - overall_start
        
        # Filter valid results
        valid_results = [r for r in results if isinstance(r, dict) and r.get("success")]
        
        print("\n📊 Pylast Library Results:")
        print(f"   Total time: {overall_duration:.3f}s")
        print(f"   Valid results: {len(valid_results)}/{num_calls}")
        print("   Expected concurrent: ~1.0s")
        print("   Expected sequential: ~5.0s")
        
        if valid_results:
            unique_threads = len({r["thread_id"] for r in valid_results})
            unique_clients = len({r["client_id"] for r in valid_results})
            
            print(f"   Unique threads: {unique_threads}")
            print(f"   Unique pylast clients: {unique_clients}")
            
            if overall_duration < 2.0 and unique_threads > 1:
                print("   ✅ PYLAST CONCURRENT: Library allows concurrency")
                pylast_concurrent = True
            else:
                print("   🚨 PYLAST SERIALIZED: Library is serializing requests")
                pylast_concurrent = False
        else:
            print("   ❌ No valid results to analyze")
            pylast_concurrent = False
        
        return {
            "overall_duration": overall_duration,
            "valid_results": len(valid_results),
            "pylast_concurrent": pylast_concurrent
        }

    @pytest.mark.asyncio
    async def test_pylast_shared_resources(self):
        """Test if pylast has shared resources causing serialization."""
        
        def pylast_with_resource_inspection(call_id: int) -> dict[str, Any]:
            """Inspect pylast internal state."""
            import threading
            
            thread_id = threading.get_ident()
            
            # Create pylast client
            client = pylast.LastFMNetwork(api_key="test_key")
            
            # Inspect internal attributes for shared resources
            internal_attrs = {}
            
            # Common attributes that might be shared
            attrs_to_check = [
                '_session',  # HTTP session
                'session',   # HTTP session
                '_cache',    # Response cache
                'cache',     # Response cache
                '_lock',     # Threading lock
                'lock',      # Threading lock
                '_pool',     # Connection pool
                'pool',      # Connection pool
            ]
            
            for attr in attrs_to_check:
                if hasattr(client, attr):
                    value = getattr(client, attr)
                    internal_attrs[attr] = {
                        "type": type(value).__name__,
                        "id": id(value),
                        "repr": repr(value)[:100] if value else None
                    }
            
            # Also check the module level for global state
            module_attrs = {}
            for attr in dir(pylast):
                if not attr.startswith('_'):
                    continue
                try:
                    value = getattr(pylast, attr)
                    if callable(value):
                        continue
                    module_attrs[attr] = {
                        "type": type(value).__name__,
                        "id": id(value)
                    }
                except Exception:  # noqa: S112 # Intentionally broad for debugging pylast attributes
                    continue
            
            return {
                "call_id": call_id,
                "thread_id": thread_id,
                "client_id": id(client),
                "internal_attrs": internal_attrs,
                "module_attrs": module_attrs
            }
        
        print("\n🔍 Inspecting pylast internal resources")
        
        # Create multiple clients and inspect their internal state
        tasks = [
            asyncio.create_task(asyncio.to_thread(pylast_with_resource_inspection, i))
            for i in range(3)
        ]
        
        results = await asyncio.gather(*tasks)
        
        print("\n🔍 Pylast Internal Analysis:")
        
        # Check for shared resources across clients
        shared_resources = {}
        
        for i, result in enumerate(results):
            print(f"   Client {i} (ID: {result['client_id']}):")
            print(f"     Thread: {result['thread_id']}")
            print(f"     Internal attrs: {list(result['internal_attrs'].keys())}")
            
            # Track resource IDs to find shared ones
            for attr_name, attr_info in result['internal_attrs'].items():
                resource_id = attr_info['id']
                if attr_name not in shared_resources:
                    shared_resources[attr_name] = []
                shared_resources[attr_name].append(resource_id)
        
        # Analyze shared resources
        print("\n🔍 Shared Resource Analysis:")
        for attr_name, resource_ids in shared_resources.items():
            unique_ids = set(resource_ids)
            if len(unique_ids) == 1:
                print(f"   {attr_name}: SHARED across all clients (ID: {next(iter(unique_ids))})")
            else:
                print(f"   {attr_name}: SEPARATE per client ({len(unique_ids)} unique)")
        
        return results