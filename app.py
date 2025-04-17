from fastapi import FastAPI
import logging

from bot_telegram import init_bot

app = FastAPI()
bot = None

@app.on_event("startup")
async def startup_event():
    """Start the Telegram bot when the FastAPI application starts."""
    global bot
    bot = init_bot()
    await bot.run()

@app.on_event("shutdown")
async def shutdown_event():
    """Stop the Telegram bot when the FastAPI application stops."""
    global bot
    if bot:
        logging.info("Stopping bot...")
        await bot.bot_stop()

@app.get("/")
def greet_json():
    return {"The bot is running": "True"}
