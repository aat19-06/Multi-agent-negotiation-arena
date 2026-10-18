# Negotiation Arena

> AI-powered multi-agent negotiation platform — Simulation Mode (AI vs AI) and Practice Mode (Human vs AI).

## Live Demo

[Open Negotiation Arena](https://negotiation-arena-17jj.onrender.com)

## Features

- **3 Prebuilt Scenarios**: Vendor Pricing, Job Offer, Project Budget
- **AI vs AI Simulation**: Watch agents negotiate with personality-driven strategies
- **Human vs AI Practice**: Participate as one of the negotiating parties
- **AI Setup Assistant**: Describe any negotiation in natural language
- **Persistent History**: Per-user conversation history with search, rename, and delete
- **Outcome Reports**: Concession timelines, satisfaction scores, and key turning points
- **Real-time Arena**: SSE-powered live negotiation workspace

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + TypeScript + Vite + Tailwind CSS |
| Backend | Python 3.11 + FastAPI + Pydantic v2 |
| AI | LangGraph + LangChain + Groq (llama-3.3-70b) |
| Database | PostgreSQL 15 + SQLAlchemy 2 + Alembic |
| Auth | JWT (access + refresh) + bcrypt |

## Quick Start

### Prerequisites
- Docker Desktop
- Node.js 20+
- Python 3.11+

### 1. Clone and configure

```bash
git clone <repo>
cd negotiation-arena
cp .env.example .env
# Set a unique POSTGRES_PASSWORD and SECRET_KEY in .env.
# GROQ_API_KEY is optional; mock mode works without it.
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

App will be available at:
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

Compose binds the frontend and API to localhost only and does not publish the PostgreSQL port. The Compose file is for development; its backend uses auto-reload. The root production Dockerfile uses a separate configuration and runs without reload.

### 3. Run without Docker (development)

**Backend:**
```bash
cd backend
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

## Environment Variables

See `.env.example` for all required variables.

Key variables:
- `GROQ_API_KEY` — Optional Groq API key (Groq Free has quotas and rate limits; usage can be unavailable)
- `USE_MOCK_AGENTS=true` — Use deterministic mock agents (no API key required)
- `SECRET_KEY` — Unique random string for JWT signing; never use the example value in production

## Mock Agent Mode

Set `USE_MOCK_AGENTS=true` to run without a Groq API key. Mock agents use deterministic concession strategies and are clearly labelled in the UI. With `USE_MOCK_AGENTS=false`, missing keys, provider failures, timeouts, and quota errors are returned explicitly; the app does not retry or silently fall back to mock agents or another provider.

AI prompts treat scenario descriptions, names, messages, and supplied history as untrusted data, while offer validation and negotiation state transitions are enforced by the backend. These controls reduce prompt-injection impact but cannot eliminate prompt injection or guarantee that an AI provider will follow instructions. Never put credentials, secrets, or sensitive private goals in user-controlled scenario text. Negotiation reports use stored offer events for their timeline and label general advice as heuristic; heuristics are not measured performance metrics.

## Resource and security limits

The API caps request bodies at 64 KiB and returns 408 if a body is not received within 15 seconds; assistant messages are limited to 2,000 characters and histories to 20 entries, negotiations to 30 rounds and 100 per account, and detailed transcript/report reads to 300 records. AI requests time out after 25 seconds, use at most 512 output tokens, and are not retried. The default in-process limits are 5 login attempts per IP per hour, 10 registrations per IP per hour, 10 assistant requests per account per minute, 12 practice turns per account per minute, 2 concurrent AI requests and simulations per process, and 20 concurrent SSE streams per process, each bounded to 300 seconds. Each account may have at most 3 active negotiations. Limits return HTTP 429 (or HTTP 409 when the persistent account negotiation cap is reached).

Rate-limit buckets and concurrency gates are in memory and bounded; they are **not shared across workers or service instances** and reset on process restart. The current Docker production command starts one Uvicorn worker. Do not increase worker/replica count expecting these limits to remain global. Without a free, durable shared limiter, keep a public deployment to a single process; process-local limits reduce abuse but cannot guarantee protection from distributed clients or account creation through many IP addresses. For stricter zero-cost use, keep the app local, use mock agents, and avoid public registration.

Per-account negotiation caps do not impose a global database-size or retention limit. Public registration can still create many accounts, and the app does not automatically purge history; monitor the database provider's storage quota or keep registration private/local.

JWTs remain in browser `localStorage`, which is vulnerable to theft if an XSS flaw is introduced. Logout, password changes, and refresh-token rotation revoke prior token versions, but a future browser-auth redesign should move tokens to `HttpOnly; Secure; SameSite` cookies and add CSRF protection and explicit origin checks before enabling cookie authentication.

## Free deployment (Render + Neon)

The root `Dockerfile` builds and serves the frontend and API together. The root `render.yaml` defines a free Render web service; Neon provides the external PostgreSQL database.

1. Create a PostgreSQL project on [Neon's Free plan](https://neon.com/pricing). Choose the pooled connection string in Neon’s connection details.
2. In Render, create a Blueprint from this GitHub repository and select the repository root. Render reads `render.yaml` and builds the Dockerfile.
3. When prompted for `DATABASE_URL`, paste the Neon pooled connection string directly into Render’s secret field. Do not put it in source control or chat.
4. Let Render generate `SECRET_KEY`. The blueprint selects production mode, disables SQLite fallback, enables mock agents, and configures `/health` as the health check.
5. After deployment, open the Render service URL and verify `/health`.

Render and Neon offer free tiers, but their quotas, availability, and terms can change; free-tier failure may make the app unavailable. Groq Free is optional and quota-limited. GitHub Actions runs the configured CI jobs and may consume the account's included Actions minutes. These services are external and may become chargeable depending on account settings, usage, plan changes, and provider terms. **Application code cannot guarantee that use of these services will cost $0.** Do not add a payment method, enable upgrades/add-ons, or assume a free tier has a hard billing ceiling if avoiding charges is essential. Keep the app local with mock agents for the strongest cost control. Never configure automatic fallback to a paid provider.

The Render Blueprint is configured for the Free plan and has automatic deploys disabled. Do not deploy or provision services solely to follow these instructions. Production startup refuses the development secret, SQLite fallback, and non-PostgreSQL URLs; unlike development, it does not create/alter tables at startup. The database schema, including the initial schema, must be manually migrated before the production health check can pass. Back up the database and review the target URL and migration SQL before running Alembic; this command connects to and changes the configured database:

```powershell
cd backend
alembic upgrade head
```

The first migration establishes the current schema and the next adds token-version revocation support. Review the target database and migration SQL before running it; this repository does not run migrations automatically.

## Running Tests

```bash
# Backend
cd backend
pytest tests/ -v

# Frontend checks (there is no frontend test script yet)
cd ../frontend
npm run build
npm run lint
```

## Project Structure

```
negotiation-arena/
├── backend/           # FastAPI application
│   ├── app/
│   │   ├── api/       # Route handlers
│   │   ├── core/      # Config, security
│   │   ├── db/        # Models, session
│   │   ├── schemas/   # Pydantic schemas
│   │   ├── services/  # Business logic
│   │   ├── engine/    # Negotiation state machine
│   │   └── agents/    # LangGraph AI agents
│   └── tests/
├── frontend/          # React application
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── hooks/
│       ├── store/
│       └── api/
├── docker-compose.yml
└── .env.example
```

## License

MIT
