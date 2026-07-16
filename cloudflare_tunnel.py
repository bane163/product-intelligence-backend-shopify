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
_monitor_task: Optional[asyncio.Task[None]] = None


async def _probe_tunnel(url: str) -> bool:
    try:
        import httpx

        timeout = float(os.getenv("COLLABORA_TUNNEL_PROBE_TIMEOUT_SECONDS", "5"))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(f"{url.rstrip('/')}/hosting/discovery")
        return response.status_code == 200
    except Exception:
        return False


async def _wait_until_healthy(url: str, timeout: float) -> bool:
    # Quick-tunnel DNS is announced before the hostname is necessarily
    # resolvable. Avoid poisoning Docker's DNS cache with an immediate NXDOMAIN.
    initial_delay = max(
        0.0,
        float(os.getenv("COLLABORA_TUNNEL_DNS_GRACE_SECONDS", "6")),
    )
    await asyncio.sleep(initial_delay)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _probe_tunnel(url):
            return True
        await asyncio.sleep(1)
    return False


def _ensure_monitor(target_port: int, provider: str) -> None:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor_and_recover(target_port, provider))


async def start_tunnel(
    target_port: int = 9980,
    timeout: float = 30.0,
    provider: Optional[str] = None,
    *,
    start_monitor: bool = True,
) -> Optional[str]:
    """Start a quick tunnel pointing to the target port.
    
    Args:
        target_port: The local port to expose (default: 9980 for Collabora)
        timeout: Maximum seconds to wait for the tunnel URL
        provider: Tunnel provider ("cloudflare"/"cloudflared" or "ngrok")
    
    Returns:
        The public tunnel URL or None on failure
    """
    global _tunnel_process, _tunnel_url, _tunnel_provider, _monitor_task
    
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
                    return match.group(1)
        
        # Wait for URL with timeout
        try:
            tunnel_url = await asyncio.wait_for(read_until_url(), timeout=timeout)
            if tunnel_url:
                from services.source_link_trace import record
                record(
                    component="tunnel",
                    stage="tunnel_candidate_announced",
                    details={
                        "provider": selected_provider,
                        "url_host": tunnel_url.split("//", 1)[-1],
                    },
                )
                if not await _wait_until_healthy(tunnel_url, timeout):
                    logger.error("Tunnel URL was announced but never became healthy")
                    record(
                        component="tunnel",
                        stage="initial_probe_failed",
                        status="error",
                        details={"provider": selected_provider, "url_host": tunnel_url.split("//", 1)[-1]},
                    )
                    stop_tunnel()
                    if start_monitor:
                        _ensure_monitor(target_port, selected_provider)
                    return None
                _tunnel_url = tunnel_url
                logger.info(f"{selected_provider} tunnel established and healthy: {tunnel_url}")
                record(
                    component="tunnel",
                    stage="tunnel_healthy",
                    status="ok",
                    details={"provider": selected_provider, "url_host": tunnel_url.split("//", 1)[-1]},
                )
                # Start background task to consume remaining output
                asyncio.create_task(_consume_output())
                if start_monitor:
                    _ensure_monitor(target_port, selected_provider)
                return tunnel_url
            else:
                logger.error(f"{selected_provider} process exited without providing URL")
                stop_tunnel()
                if start_monitor:
                    _ensure_monitor(target_port, selected_provider)
                return None
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for tunnel URL after {timeout}s")
            stop_tunnel()
            if start_monitor:
                _ensure_monitor(target_port, selected_provider)
            return None
            
    except FileNotFoundError:
        command_name = "cloudflared" if selected_provider == "cloudflare" else "ngrok"
        logger.error(f"{command_name} command not found. Please install {command_name}.")
        if start_monitor:
            _ensure_monitor(target_port, selected_provider)
        return None
    except Exception as e:
        logger.error(f"Failed to start {selected_provider} tunnel: {e}")
        stop_tunnel()
        if start_monitor:
            _ensure_monitor(target_port, selected_provider)
        return None


async def _consume_output():
    """Background task to consume tunnel output and prevent buffer blocking."""
    global _tunnel_process, _tunnel_provider, _tunnel_url
    
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
    finally:
        if process is _tunnel_process and process.returncode is not None:
            _tunnel_url = None


async def _monitor_and_recover(target_port: int, provider: str) -> None:
    """Invalidate stale URLs and restore the quick tunnel with bounded backoff."""
    global _tunnel_process, _tunnel_provider, _tunnel_url
    failures = 0
    backoffs = (1, 2, 5, 10, 30)
    interval = max(2, int(os.getenv("COLLABORA_TUNNEL_PROBE_INTERVAL_SECONDS", "15")))
    while True:
        await asyncio.sleep(interval)
        url = _tunnel_url
        healthy = bool(url) and await _probe_tunnel(str(url))
        failures = 0 if healthy else failures + 1
        if failures < 2:
            continue
        stale_host = str(url or "").split("//", 1)[-1]
        logger.warning("Collabora tunnel is unhealthy; rebuilding it")
        from services.source_link_trace import record
        record(
            component="tunnel",
            stage="tunnel_stale",
            status="error",
            details={"provider": provider, "url_host": stale_host},
        )
        _tunnel_url = None
        process = _tunnel_process
        if process is not None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        _tunnel_process = None
        _tunnel_provider = None
        for delay in backoffs:
            await asyncio.sleep(delay)
            restored = await start_tunnel(
                target_port,
                provider=provider,
                start_monitor=False,
            )
            if restored:
                failures = 0
                break


def stop_tunnel():
    """Stop the running tunnel."""
    global _tunnel_process, _tunnel_url, _tunnel_provider, _monitor_task

    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if _monitor_task is not None and _monitor_task is not current_task:
        _monitor_task.cancel()
    _monitor_task = None
    
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
