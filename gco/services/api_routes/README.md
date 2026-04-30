# API Routes

FastAPI route modules for the GCO manifest API. Each file defines a set of related endpoints that are mounted on the main FastAPI app in `manifest_api.py`.

## Table of Contents

- [Files](#files)
- [Route Prefix](#route-prefix)
- [Adding a New Route Module](#adding-a-new-route-module)

## Files

| File | Endpoints | Description |
|------|-----------|-------------|
| `manifests.py` | `POST /api/v1/manifests`, `GET /api/v1/manifests/{ns}/{name}`, `DELETE /api/v1/manifests/{ns}/{name}` | Manifest submission, status, and deletion |
| `jobs.py` | `GET /api/v1/jobs`, `GET /api/v1/jobs/{ns}/{name}`, `GET /api/v1/jobs/{ns}/{name}/logs`, `DELETE /api/v1/jobs/{ns}/{name}` | Job listing, status, logs, events, deletion |
| `queue.py` | `POST /api/v1/queue/submit`, `GET /api/v1/queue/stats` | SQS queue submission and depth stats |
| `templates.py` | `GET /api/v1/templates`, `POST /api/v1/templates`, `GET /api/v1/templates/{name}`, `DELETE /api/v1/templates/{name}` | Reusable job template CRUD |
| `webhooks.py` | `GET /api/v1/webhooks`, `POST /api/v1/webhooks`, `DELETE /api/v1/webhooks/{id}`, `POST /api/v1/webhooks/{id}/test` | Webhook registration and testing |

## Route Prefix

All routes are prefixed with `/api/v1/`. The prefix is set in `manifest_api.py` when mounting the routers.

## Adding a New Route Module

1. Create a new file (e.g. `my_routes.py`) with a `router = APIRouter()` instance
2. Define your endpoints on the router
3. Mount it in `manifest_api.py`: `app.include_router(my_routes.router, prefix="/api/v1")`
4. Add tests in `tests/test_manifest_api_*.py`
