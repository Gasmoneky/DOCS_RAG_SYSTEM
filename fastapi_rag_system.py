from contextlib import asynccontextmanager
import json
import os
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# --- CONFIGURATION & ENV PATHS ---
DB_DIR = os.getenv("DB_DIR", "/root/chroma_db")
DOCS_DIR = os.getenv("DOCS_DIR", "/app/documentation")
OLLAMA_INTERNAL_URL = os.getenv("OLLAMA_INTERNAL_URL", "http://127.0.0.1:11434")

# Load embeddings engine and vector database store
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)


# --- AUTOMATED INGESTION LOGIC ---
def run_ingestion():
    """Reads markdown/text files from DOCS_DIR, splits into chunks, and saves to ChromaDB."""
    if not os.path.exists(DOCS_DIR):
        print(
            f"[INGEST] Directory {DOCS_DIR} does not exist. Skipping ingestion."
        )
        return

    print(f"[INGEST] Starting document ingestion from: {DOCS_DIR}")

    try:
        # 1. Load all markdown files recursively from your docs path
        loader = DirectoryLoader(
            DOCS_DIR, glob="**/*.md", loader_cls=TextLoader
        )
        raw_documents = loader.load()

        if not raw_documents:
            print("[INGEST] No markdown (.md) documents found to ingest.")
            return

        print(f"[INGEST] Loaded {len(raw_documents)} raw document(s).")

        # 2. Chunk documents into optimized contexts
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200
        )
        docs = text_splitter.split_documents(raw_documents)

        # 3. Embed and insert into ChromaDB vector store
        vector_store.add_documents(docs)
        print(
            f"[INGEST] Successfully indexed {len(docs)} text chunks into Chroma DB at {DB_DIR}!"
        )

    except Exception as e:
        print(f"[INGEST] Failed during ingestion: {str(e)}")


# --- LIFESPAN EVENT (Runs on Server Boot) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # This executes IMMEDIATELY when Uvicorn starts up the app
    run_ingestion()
    yield


app = FastAPI(lifespan=lifespan)

# --- CORS MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


# --- HANDSHAKE & MANUAL INGEST ENDPOINTS ---


@app.get("/")
async def health_check():
    return Response(content="Ollama is running", media_type="text/plain")


@app.get("/api/version")
async def version_check():
    return {"version": "0.3.0"}


@app.post("/api/ingest")
async def manual_ingest_trigger():
    """Manual endpoint to trigger document re-ingestion on demand."""
    run_ingestion()
    return {"status": "success", "message": "Ingestion triggered."}


@app.post("/api/show")
async def show_model(request: Request):
    try:
        client_data = await request.json()
    except Exception:
        client_data = {}

    try:
        async with httpx.AsyncClient() as client:
            ollama_res = await client.post(
                f"{OLLAMA_INTERNAL_URL}/api/show",
                json=client_data,
                timeout=5.0,
            )

            if ollama_res.status_code == 200:
                return ollama_res.json()

            return JSONResponse(
                status_code=ollama_res.status_code, content=ollama_res.json()
            )

    except httpx.RequestError as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to connect to Ollama: {str(e)}"},
        )


@app.get("/api/tags")
@app.get("/v1/models")
async def get_models():
    try:
        async with httpx.AsyncClient() as client:
            ollama_res = await client.get(
                f"{OLLAMA_INTERNAL_URL}/api/tags", timeout=5.0
            )

            if ollama_res.status_code == 200:
                return ollama_res.json()

            return JSONResponse(
                status_code=ollama_res.status_code, content=ollama_res.json()
            )

    except httpx.RequestError as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to connect to Ollama: {str(e)}"},
        )


# --- RAG CHAT ENDPOINT ---


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}

    messages = data.get("messages", [])
    stream = data.get("stream", True)
    requested_model = data.get("model", "qwen2.5-coder:3b")

    if not messages:
        return JSONResponse(status_code=400, content={"error": "No messages"})

    user_query = messages[-1].get("content", "")

    # Retrieve relevant document chunks from ChromaDB dynamically based on user query
    retriever = vector_store.as_retriever(search_kwargs={"k": 3})
    docs = retriever.invoke(user_query)

    context_chunk = "\n\n".join([doc.page_content for doc in docs])

    system_instruction = (
        "You are an expert in the data that u have been given. "
        "Answer the question strictly utilizing the official documentation context provided below.\n\n"
        f"Context:\n{context_chunk}"
    )

    updated_messages = [{"role": "system", "content": system_instruction}] + messages

    payload = {
        "model": requested_model,
        "messages": updated_messages,
        "stream": stream,
    }

    if stream:

        async def generate_stream():
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        f"{OLLAMA_INTERNAL_URL}/api/chat",
                        json=payload,
                        timeout=None,
                    ) as response:
                        async for line in response.aiter_lines():
                            if line:
                                yield line + "\n"
            except httpx.RequestError as e:
                yield (
                    json.dumps({"error": f"Stream transport dropped: {str(e)}"})
                    + "\n"
                )

        return StreamingResponse(
            generate_stream(),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no"},
        )
    else:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{OLLAMA_INTERNAL_URL}/api/chat", json=payload, timeout=None
            )
            return res.json()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=11435)
