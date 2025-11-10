# Collabora Online Setup Guide

This setup provides a Docker-based Collabora Online installation with WOPI protocol support.

## Prerequisites

- Docker and Docker Compose installed
- At least 2GB of RAM available

## Quick Start

1. Navigate to the collabora-setup directory:
```bash
cd collabora-setup
```

2. Start the services:
```bash
docker-compose up -d
```

3. Access Collabora Online at: http://localhost:8080

## Services

- **Collabora Online**: Office suite server running on port 9980
- **Nginx**: Reverse proxy handling WOPI protocol on port 8080

## WOPI Protocol Support

The setup includes basic WOPI (Web Application Open Platform Interface) protocol implementation:

- `GET /wopi/files/{file_id}` - Check file info
- `GET /wopi/files/{file_id}/contents` - Get file contents
- `POST /wopi/files/{file_id}/contents` - Update file contents

## Configuration

### Environment Variables

Edit `docker-compose.yml` to configure:
- Domain settings
- Admin credentials
- Language dictionaries

### Nginx Configuration

The `nginx.conf` file handles:
- WOPI discovery endpoint routing
- Collabora Online endpoint routing
- Health checks

## Testing

1. Check if services are running:
```bash
docker-compose ps
```

2. Test health endpoint:
```bash
curl http://localhost:8080/health
```

3. Test WOPI discovery:
```bash
curl http://localhost:8080/hosting/discovery
```

## File Storage

For production use, replace the in-memory file storage in `wopi_server.py` with:
- Database storage (PostgreSQL, MySQL)
- Cloud storage (S3, Azure Blob Storage)
- Local file system with proper permissions

## Security Considerations

- Change default admin credentials
- Enable SSL/TLS for production
- Implement proper authentication
- Use secure WOPI secret tokens
- Configure proper file access controls

## Troubleshooting

Check logs:
```bash
docker-compose logs collabora
docker-compose logs nginx
```

Restart services:
```bash
docker-compose restart
```