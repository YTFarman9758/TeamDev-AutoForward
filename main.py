from fastapi import FastAPI
import os
import threading

app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Bot Running"}

def start_bot():
    os.system("python3 main.py")

threading.Thread(target=start_bot, daemon=True).start()
