from fastapi import FastAPI

from .alertmanager import AlertmanagerMessage

app = FastAPI()


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello from alertagent!"}

@app.post("/")
def alertmanager_ingest(message: AlertmanagerMessage) -> dict[str, str]:

    messages: list[str] = []
    for alert in message.alerts:
        messages.append(alert.labels.get("alertname", "") + " " + alert.status)

    return {
        "discord_message_id": "foobarbaz",
        "alerts": ", ".join(messages),
    }