# Manual setup

Docker is the recommended path - see [README.md](README.md). These instructions are for running the services on the host directly.

## Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # add OPENAI_API_KEY
uvicorn main:app --reload
```

API at [http://localhost:8002](http://localhost:8002).

## Frontend

```bash
cd frontend
npm install
npm run dev
```

UI at [http://localhost:5175](http://localhost:5175). The backend's CORS is wired to this exact origin - don't change the Vite port.

## Tests

```bash
cd backend && pytest
```

## Evals

```bash
cd backend && python -m evals.run
```

Requires internet (OpenAI + CourtListener). See [backend/evals/README.md](backend/evals/README.md) for details.
