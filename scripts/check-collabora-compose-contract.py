#!/usr/bin/env python3
"""Render Compose and enforce the Collabora invocation contract."""

import json
import os
import subprocess
import sys


PINNED_PREFIX = "collabora/code:25.04.7.1.1@sha256:"


def render(origins: str) -> dict:
    environment = dict(os.environ, COLLABORA_FRAME_ANCESTORS=origins)
    environment.pop("COLLABORA_IMAGE", None)
    output = subprocess.check_output(
        ["docker", "compose", "-f", "docker-compose.stack.yml", "config", "--format", "json"],
        env=environment,
        text=True,
    )
    return json.loads(output)["services"]["collabora"]


def check(origins: str) -> None:
    service = render(origins)
    expected = f"--o:net.content_security_policy=frame-ancestors {origins}"
    assert service["command"] == [expected], service["command"]
    environment = service["environment"]
    extra_params = environment["extra_params"]
    assert extra_params == "--o:ssl.enable=false --o:ssl.termination=true", extra_params
    assert service["image"].startswith(PINNED_PREFIX), service["image"]


if __name__ == "__main__":
    try:
        check("*")
        check("https://app.example.com https://admin.example.com")
    except (AssertionError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Collabora Compose contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Collabora Compose contract passed")
