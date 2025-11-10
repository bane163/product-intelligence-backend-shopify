#!/bin/bash

# Start nginx in background
nginx -g "daemon off;" &

# Wait for nginx to start
sleep 2

# Start the WOPI server (if implemented)
# python wopi_server.py &

# Keep the container running
wait