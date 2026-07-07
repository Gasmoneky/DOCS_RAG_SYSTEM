import json

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

app = FastAPI()

# --- CORS MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

DB_DIR = ""  # enter the location your db
OLLAMA_INTERNAL_URL = os.getenv("OLLAMA_INTERNAL_URL", "http://127.0.0.1:11434")

# Load the Chroma Vector DBs
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)


# --- HANDSHAKE ENDPOINTS ---


@app.get("/")
async def health_check():
    return Response(content="Ollama is running", media_type="text/plain")


@app.get("/api/version")
async def version_check():
    return {"version": "0.3.0"}


@app.post("/api/show")
async def show_model(request: Request):
    try:
        client_data = await request.json()
    except Exception:
        client_data = {}

    try:
        async with httpx.AsyncClient() as client:
            # Pass the exact payload your Flutter app sent straight into Ollama
            ollama_res = await client.post(
                f"{OLLAMA_INTERNAL_URL}/api/show", json=client_data, timeout=5.0
            )

            if ollama_res.status_code == 200:
                return ollama_res.json()

            return JSONResponse(
                status_code=ollama_res.status_code, content=ollama_res.json()
            )

    except httpx.RequestError as e:
        return JSONResponse(
            status_code=500, content={"error": f"Failed to connect to Ollama: {str(e)}"}
        )


@app.get("/api/tags")
@app.get("/v1/models")
async def get_models():
    try:
        async with httpx.AsyncClient() as client:
            # Ask Ollama directly for its true local list of models
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
            status_code=500, content={"error": f"Failed to connect to Ollama: {str(e)}"}
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

    # FIX: Dynamically pull the model selected by your Flutter app
    requested_model = data.get("model", "qwen2.5-coder:3b")

    if not messages:
        return JSONResponse(status_code=400, content={"error": "No messages"})

    user_query = messages[-1].get("content", "")

    # RAG Logic
    docs = vector_store.similarity_search(user_query, k=4)
    context_chunk = "\n\n".join([doc.page_content for doc in docs])

    system_instruction = (
        "You are an expert in the data that u have been given "
        "Answer the question strictly utilizing the official documentation context provided below.\n\n"
        f"Context:\n{context_chunk}"
    )

    updated_messages = [{"role": "system", "content": system_instruction}] + messages

    # The payload model parameter is now entirely driven by your Flutter selection
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
                    json.dumps({"error": f"Stream transport dropped: {str(e)}"}) + "\n"
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

    # Start the fast asynchronous server engine
    uvicorn.run(app, host="0.0.0.0", port=11435)
