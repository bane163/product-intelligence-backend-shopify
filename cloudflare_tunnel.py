"""
Tunnel management for exposing local services via quick tunnels.

This module provides functions to start/stop a quick tunnel (Cloudflare or ngrok)
and retrieve the generated public URL for use in development.
"""

import asyncio
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Global state for the tunnel
_tunnel_process: Optional[asyncio.subprocess.Process] = None
_tunnel_url: Optional[str] = None
_tunnel_provider: Optional[str] = None


async def start_tunnel(
    target_port: int = 9980, timeout: float = 30.0, provider: Optional[str] = None
) -> Optional[str]:
    """Start a quick tunnel pointing to the target port.
    
    Args:
        target_port: The local port to expose (default: 9980 for Collabora)
        timeout: Maximum seconds to wait for the tunnel URL
        provider: Tunnel provider ("cloudflare"/"cloudflared" or "ngrok")
    
    Returns:
        The public tunnel URL or None on failure
    """
    global _tunnel_process, _tunnel_url, _tunnel_provider
    
    if _tunnel_process is not None:
        logger.warning("Tunnel already running, stopping existing tunnel first")
        stop_tunnel()
    
    selected_provider = (provider or os.getenv("TUNNEL_PROVIDER", "cloudflare")).strip().lower()
    if selected_provider in ("cloudflare", "cloudflared"):
        selected_provider = "cloudflare"
    elif selected_provider == "ngrok":
        selected_provider = "ngrok"
    else:
        logger.error(
            f"Unsupported tunnel provider '{selected_provider}'. Use 'cloudflare' or 'ngrok'."
        )
        return None

    # Use COLLABORA_HOST env var for Docker environments
    collabora_host = os.getenv("COLLABORA_HOST", "collabora")
    target_url = f"http://{collabora_host}:{target_port}"
    logger.info(f"Starting {selected_provider} tunnel for {target_url}")
    
    try:
        if selected_provider == "cloudflare":
            command = ["cloudflared", "tunnel", "--url", target_url, "--no-autoupdate"]
            url_pattern = re.compile(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)")
        else:
            command = ["ngrok", "http", target_url, "--log", "stdout"]
            url_pattern = re.compile(r"(https://[a-zA-Z0-9.-]+\.ngrok(?:-free)?\.(?:app|io))")

        # Start tunnel process using asyncio subprocess
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _tunnel_process = process
        _tunnel_provider = selected_provider
        
        # Wait for the tunnel URL to appear in output
        async def read_until_url():
            """Read output until we find the tunnel URL."""
            global _tunnel_url
            while True:
                if process.stdout is None:
                    return None
                line = await process.stdout.readline()
                if not line:
                    return None
                line_str = line.decode('utf-8', errors='replace')
                logger.info(f"{selected_provider} output: {line_str.strip()}")
                match = url_pattern.search(line_str)
                if match:
                    _tunnel_url = match.group(1)
                    return _tunnel_url
        
        # Wait for URL with timeout
        try:
            tunnel_url = await asyncio.wait_for(read_until_url(), timeout=timeout)
            if tunnel_url:
                logger.info(f"{selected_provider} tunnel established: {tunnel_url}")
                # Start background task to consume remaining output
                asyncio.create_task(_consume_output())
                return tunnel_url
            else:
                logger.error(f"{selected_provider} process exited without providing URL")
                stop_tunnel()
                return None
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for tunnel URL after {timeout}s")
            stop_tunnel()
            return None
            
    except FileNotFoundError:
        command_name = "cloudflared" if selected_provider == "cloudflare" else "ngrok"
        logger.error(f"{command_name} command not found. Please install {command_name}.")
        return None
    except Exception as e:
        logger.error(f"Failed to start {selected_provider} tunnel: {e}")
        stop_tunnel()
        return None


async def _consume_output():
    """Background task to consume tunnel output and prevent buffer blocking."""
    global _tunnel_process, _tunnel_provider
    
    if _tunnel_process is None:
        return
    
    try:
        process = _tunnel_process
        if hasattr(process, 'stdout') and process.stdout:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                provider = _tunnel_provider or "tunnel"
                logger.debug(f"{provider}: {line.decode('utf-8', errors='replace').strip()}")
    except Exception as e:
        logger.debug(f"Output consumer stopped: {e}")


def stop_tunnel():
    """Stop the running tunnel."""
    global _tunnel_process, _tunnel_url, _tunnel_provider
    
    if _tunnel_process is not None:
        provider = _tunnel_provider or "tunnel"
        logger.info(f"Stopping {provider} tunnel")
        try:
            _tunnel_process.terminate()
        except Exception as e:
            logger.error(f"Error stopping tunnel: {e}")
        finally:
            _tunnel_process = None
    
    _tunnel_url = None
    _tunnel_provider = None


def get_tunnel_url() -> Optional[str]:
    """Get the current tunnel URL if a tunnel is running.
    
    Returns:
        The public tunnel URL or None if no tunnel is active
    """
    return _tunnel_url
