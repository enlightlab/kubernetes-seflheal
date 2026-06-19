from fastapi import FastAPI

app = FastAPI(title="enlight-lab-fastapi")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Enlight Lab FastAPI is running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
