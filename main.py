import asyncio
import logging
import time
from bot_telegram import init_bot

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def heartbeat():
    """Send periodic heartbeat to keep the bot active"""
    while True:
        logging.info("Bot heartbeat - Running...")
        await asyncio.sleep(300)  # Log every 5 minutes

async def main():
    try:
        # Initialize the bot
        bot = init_bot()
        logging.info("Bot initialized successfully")
        
        # Run the bot
        await bot.run()
        
        # Start heartbeat
        heartbeat_task = asyncio.create_task(heartbeat())
        
        # Keep the bot running with multiple tasks
        tasks = [
            heartbeat_task,
            # Add any other background tasks here
        ]
        
        # Wait for all tasks
        await asyncio.gather(*tasks)
            
    except KeyboardInterrupt:
        logging.info("Bot shutdown requested...")
        await bot.bot_stop()
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        raise
    finally:
        logging.info("Bot shutdown complete")

if __name__ == "__main__":
    # Set up asyncio to use event loop policy that prevents sleep
    if hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Print startup banner
    logging.info("="*50)
    logging.info("Starting RFP Responder Bot")
    logging.info("Press Ctrl+C to stop")
    logging.info("="*50)
    
    # Run the bot
    asyncio.run(main())