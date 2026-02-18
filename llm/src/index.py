from dotenv import load_dotenv
load_dotenv()
from applib.graph.graph_manager import graph_manager
from applib.api.routes import router
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    print("Initializing graph...")
    await graph_manager.initialize_graph()
    print("Graph initialized successfully")
    yield

    # Shutdown cleanup
    await graph_manager.shutdown()
    print("Shutting down")

app = FastAPI(
    title="Patriot Pay Paygent",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware to allow frontend requests
# In production, stage and dev, will be managed by lambda funciton url cors settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """Return 400 { \"detail\": \"Invalid JSON\" } when body is invalid JSON; else 422."""
    errors = getattr(exc, "errors", ()) or []
    if errors and len(errors) > 0 and list(errors[0].get("loc") or [])[:1] == ["body"]:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """Return 500 { \"detail\": \"<error>\" } per spec."""
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get('/')
async def root() -> dict[str, str]:
    """Health check"""
    return {
        'status': 'healthy',
        'service': 'Patriot Pay Paygent',
        'version': '1.0.0'
    }

@app.get("/health")
async def health():
    """Detailed health check"""
    return {
        'status': 'healthy',
        'graph_initialized': graph_manager.graph_initialized(),
        'checkpointer_initialized': graph_manager.checkpointer_initialized()
    }
