from .interfaces.api.main import app
if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)  # nosec B104 - container ingress
