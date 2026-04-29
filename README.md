# Insighta Labs+ — Backend API

Secure, multi-interface profile intelligence platform built with FastAPI.

## Live URL

`https://stage-1-data-persistence-api-design.vercel.app`

## System Architecture

```
┌─────────────┐     GitHub OAuth      ┌──────────────────┐
│  CLI Tool   │ ──────────────────── ▶│                  │
└─────────────┘                       │   FastAPI Backend │
                                      │                  │
┌─────────────┐     GitHub OAuth      │  - Auth routes   │
│ Web Portal  │ ──────────────────── ▶│  - Profile routes│
└─────────────┘                       │  - Admin routes  │
                                      └────────┬─────────┘
                                               │
                                      ┌────────▼─────────┐
                                      │  PostgreSQL DB    │
                                      │  (Supabase)       │
                                      └──────────────────┘
```

## Authentication Flow

### Web Portal Flow
1. User clicks "Continue with GitHub" → `GET /api/v1/auth/github`
2. Backend redirects to GitHub OAuth
3. GitHub redirects to `GET /api/v1/auth/github/callback?code=...`
4. Backend exchanges code for GitHub access token
5. Backend creates/updates user in DB
6. Backend issues access token (3 min) + refresh token (5 min)
7. Tokens are set as **HTTP-only cookies** — never exposed to JavaScript
8. A non-sensitive `has_session` cookie signals to the frontend that a session exists
9. Backend redirects to `/dashboard` (no tokens in URL)

### CLI Flow (PKCE)
1. `insighta login` generates `code_verifier` + `code_challenge` (SHA-256)
2. CLI starts a local HTTP server on port 8765
3. Browser opens GitHub OAuth page with `code_challenge` attached
4. GitHub redirects to `localhost:8765/callback?code=...&state=...`
5. CLI validates `state` (CSRF check), sends `code` + `code_verifier` to backend
6. Backend verifies PKCE, issues tokens, returns JSON
7. CLI stores tokens at `~/.insighta/credentials.json`

## Token Handling

| Token | Expiry | Storage (Web) | Storage (CLI) |
|---|---|---|---|
| Access token | 3 minutes | HTTP-only cookie | `credentials.json` |
| Refresh token | 5 minutes | HTTP-only cookie | `credentials.json` |

- **Web**: Tokens are never accessible via JavaScript. The browser sends them automatically via `credentials: include`.
- **CLI**: On every 401, the CLI automatically calls `POST /auth/refresh` and retries. If refresh fails, the user is prompted to run `insighta login` again.
- Refresh tokens are **single-use** — each refresh issues a new pair and revokes the old one immediately.

## Role Enforcement

| Role | Permissions |
|---|---|
| `analyst` | Read profiles, search, export |
| `admin` | All analyst permissions + create profiles, delete profiles, manage users |

- Default role on signup: `analyst`
- `is_active = false` → `403 Forbidden` on all requests
- Role checks use a structured middleware (`require_role()`) applied at the router level — no scattered checks

## API Reference

### Auth Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/auth/github` | Redirect to GitHub OAuth |
| GET | `/api/v1/auth/github/callback` | Handle OAuth callback |
| POST | `/api/v1/auth/refresh` | Refresh token pair |
| POST | `/api/v1/auth/logout` | Invalidate session |
| GET | `/api/v1/auth/whoami` | Get current user |

### Profile Endpoints

All profile endpoints require `X-API-Version: 1` header and authentication.

| Method | Endpoint | Role | Description |
|---|---|---|---|
| GET | `/api/v1/profiles` | analyst | List profiles (filter, sort, paginate) |
| GET | `/api/v1/profiles/:id` | analyst | Get single profile |
| POST | `/api/v1/profiles` | admin | Create profile |
| DELETE | `/api/v1/profiles/:id` | admin | Delete profile |
| GET | `/api/v1/profiles/search?q=...` | analyst | Natural language search |
| GET | `/api/v1/profiles/export?format=csv` | analyst | Export as CSV |

### Admin Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/admin/users` | List all users |
| PATCH | `/api/v1/admin/users/:id/role` | Change user role |
| PATCH | `/api/v1/admin/users/:id/status` | Activate/deactivate user |

### Pagination Response Format

```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 2026,
  "total_pages": 203,
  "links": {
    "self": "/api/v1/profiles?page=1&limit=10",
    "next": "/api/v1/profiles?page=2&limit=10",
    "prev": null
  },
  "data": []
}
```

## Natural Language Parsing

`GET /api/v1/profiles/search?q=young males from nigeria`

The parser extracts filters from free-text queries:

| Pattern | Example | Extracted filter |
|---|---|---|
| Gender | "males", "females" | `gender=male` |
| Age group | "adult", "teenager", "senior", "child" | `age_group=adult` |
| Young | "young" | `age 16–24` |
| Age range | "above 30", "under 25" | `min_age` / `max_age` |
| Country | "from nigeria", "from kenya" | `country_id=NG` |

## Rate Limiting

| Scope | Limit |
|---|---|
| Auth endpoints (`/auth/*`) | 10 requests/minute |
| All other endpoints | 60 requests/minute |

Returns `429 Too Many Requests` when exceeded.

## Logging

Every request logs: `METHOD PATH STATUS DURATION_MS`

## Local Development

```bash
# 1. Clone and install
git clone <repo>
cd Stage_1
pip install -r requirements.txt

# 2. Create .env
cp .env.example .env  # fill in values

# 3. Run
uvicorn main:app --reload
```

Required `.env` variables:
```
DATABASE_URL=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
JWT_SECRET_KEY=
REDIRECT_URI=http://localhost:8000/api/v1/auth/github/callback
BACKEND_URL=http://localhost:8000
WEB_PORTAL_URL=http://localhost:5173
ENVIRONMENT=development
```
