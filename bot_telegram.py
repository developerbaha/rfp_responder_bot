import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import aiohttp
import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import pandas as pd
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Load environment variables
load_dotenv()

# Load environment variables from .env file
BOT_SECRET_PASSWORD = os.getenv("BOT_SECRET_PASSWORD")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
HF_TOKEN = os.getenv("HF_TOKEN")
FILE_RFP_EXCEL_COUNT = int(os.getenv("FILE_RFP_EXCEL_COUNT", "100"))  # Default to 100 if not set
# API_USERNAME = os.getenv("API_USERNAME")
# API_PASSWORD = os.getenv("API_PASSWORD")

# Set the secret password for authentication
SECRET_PASSWORD = BOT_SECRET_PASSWORD

# Dictionary to store authenticated users
AUTHENTICATED_USERS = set()
AWAITING_PASSWORD = set()

logging.info("Bot starting... v0.1.0")  # Add version number

# Add debug logging
logging.info(f"BOT_TOKEN loaded: {'yes' if BOT_TOKEN else 'no'}")
logging.info(f"BASE_URL loaded: {'yes' if BASE_URL else 'no'}")
logging.info(f"HF_TOKEN loaded: {'yes' if HF_TOKEN else 'no'}")
logging.info(f"BOT_SECRET_PASSWORD loaded: {'yes' if BOT_SECRET_PASSWORD else 'no'}")

class TelegramBot:
    """A Telegram bot with password-based authentication."""

    def __init__(self, bot_token, base_url, application=None):
        """Initialize the bot with Telegram API token, API credentials, and authentication."""
        self.bot_token = bot_token
        self.base_url = base_url
        # self.username = username
        # self.password = password
        # self.auth_token = None

        # API Endpoints
        self.login_url = f"{self.base_url}/api/v1/auth/login"
        self.ai_url = f"{self.base_url}/api/v1/questions/text"
        self.excel_url = f"{self.base_url}/api/v1/questions/excel"

        # Executors
        self.text_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="text_worker")
        self.excel_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="excel_worker")
        self.excel_semaphore = asyncio.Semaphore(10)
        
        # Track active processes
        self.active_requests = {}
        self.active_excel_files = {}

        # Start Telegram Bot
        if application:
            self.app = application
        else:
            self.app = Application.builder().token(self.bot_token).build()
        self.setup_handlers()

        # Authenticate with API
        logging.info("Authenticating with API...")
        # self.authenticate()

        # Create a ThreadPoolExecutor for handling concurrent requests
        self.executor = ThreadPoolExecutor(max_workers=10)

        # Increase Excel workers for more concurrent processing
        self.excel_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="excel_worker")
        self.excel_semaphore = asyncio.Semaphore(10)  # Allow 10 concurrent Excel processes
        self.active_excel_files = {}

    # def authenticate(self):
    #     """Authenticate with the API and retrieve an access token."""
    #     payload = {"username": self.username, "password": self.password}
    #     headers = {"Content-Type": "application/json", "accept": "application/json"}
    #
    #     try:
    #         response = requests.post(self.login_url, headers=headers, json=payload)
    #
    #         if response.status_code == 200:
    #             self.auth_token = response.json().get("access_token")
    #             logging.info("Successfully authenticated with API")
    #         else:
    #             logging.error(f"Authentication failed: {response.status_code} - {response.text}")
    #
    #     except Exception as e:
    #         logging.error(f"Authentication Error: {e}")

    async def start_command(self, update: Update, context: CallbackContext):
        """Handles the /start command and asks for a password if the user is not authenticated."""
        user_id = update.message.from_user.id

        if user_id in AUTHENTICATED_USERS:
            await update.message.reply_text(
                "✅ You are already authenticated!\n\n"
                "You can :\n"
                "1. Send me any question as text\n"
                "2. Send me an Excel file with questions (must have a 'question' column in 'rfp' sheet)\n\n"
                "Note: Excel files must contain no more than 200 questions.\n\n"
                "type '/status' to check the status of the request"
            )
        else:
            AWAITING_PASSWORD.add(user_id)
            await update.message.reply_text("🔑 Please enter the secret password to access the bot.")

    async def handle_message(self, update: Update, context: CallbackContext):
        """Handles all incoming messages concurrently"""
        user_id = update.message.from_user.id
        user_message = update.message.text.strip()
        message_id = update.message.message_id

        # If user is waiting to enter a password, validate it
        if user_id in AWAITING_PASSWORD:
            await self.check_password(update, context)
            return

        # If user is authenticated, process AI request
        if user_id in AUTHENTICATED_USERS:
            # Create task for processing
            asyncio.create_task(self.chat_with_ai(update, context))
        else:
            await update.message.reply_text("❌ You are not authenticated. Please type /start to authenticate and then enter the password.")


    async def check_password(self, update: Update, context: CallbackContext):
        """Checks if the password is correct and authenticates the user."""
        user_id = update.message.from_user.id
        user_message = update.message.text.strip()

        if user_message == SECRET_PASSWORD:
            AUTHENTICATED_USERS.add(user_id)
            AWAITING_PASSWORD.discard(user_id)
            logging.info(f"User {user_id} authenticated successfully.")
            await update.message.reply_text(
                "✅ Authentication successful!\n\n"
                "You can:\n"
                "1. Send me any question as text\n"
                "2. Send me an Excel file with questions (must have a 'question' column in 'rfp' sheet)\n\n"
                "Note: Excel files must contain no more than 200 questions."
            )
        else:
            await update.message.reply_text("❌ Wrong password. Try again.")

    async def chat_with_ai(self, update: Update, context: CallbackContext):
        """Process text messages asynchronously"""
        message_id = update.message.message_id
        user_message = update.message.text

        try:
            # Send immediate acknowledgment
            processing_msg = await update.message.reply_text(
                f"🤔 Processing your request...\n"
                f"Request ID: #{message_id}"
            )

            # Track this request
            self.active_requests[message_id] = {
                'type': 'text',
                'status': 'processing',
                'start_time': time.time()
            }

            # Process in thread pool
            response = await asyncio.get_event_loop().run_in_executor(
                self.text_executor,
                self._make_api_request,
                user_message
            )

            # Update with response
            await processing_msg.edit_text(
                f"✅ Response for #{message_id}:\n{response}"
            )

        except Exception as e:
            logging.error(f"Error processing text request: {e}")
            await processing_msg.edit_text(
                f"❌ Error processing request #{message_id}: {str(e)}"
            )
        finally:
            if message_id in self.active_requests:
                self.active_requests[message_id]['status'] = 'completed'
                self.active_requests[message_id]['end_time'] = time.time()

    def _make_api_request(self, user_message):
        """Make API request"""
        try:
            headers = {
                "Authorization": f"Bearer {HF_TOKEN}",
                "accept": "application/json"
            }

            json_payload = {"question": user_message}
            form_payload = {"question": user_message}

            response = requests.post(
                self.ai_url, 
                headers={**headers, "Content-Type": "application/json"}, 
                json=json_payload
            )

            if response.status_code == 422:
                response = requests.post(
                    self.ai_url, 
                    headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                    data=form_payload
                )

            if response.status_code == 200:
                return response.json().get("answer", "I didn't understand that.")
            else:
                return f"Error: {response.status_code}"

        except Exception as e:
            return f"Connection error: {e}"

    async def handle_excel(self, update: Update, context: CallbackContext):
        """Handle Excel files concurrently"""
        logging.info("=== Starting handle_excel function ===")
        
        # Add authentication check
        user_id = update.message.from_user.id
        if user_id not in AUTHENTICATED_USERS:
            logging.info(f"Unauthorized access attempt from user {user_id}")
            await update.message.reply_text(
                "❌ You are not authenticated.\n"
                "Please type /start to authenticate and enter the password first."
            )
            return

        logging.info(f"Authenticated user {user_id} uploaded file: {update.message.document.file_name}")
        logging.info(f"Received file: {update.message.document.file_name}")
        
        try:
            document = update.message.document
            message_id = update.message.message_id
            logging.info(f"Processing document with ID: {message_id}")

            # First download the file
            logging.info("Downloading file...")
            file = await context.bot.get_file(document.file_id)
            file_bytes = await file.download_as_bytearray()
            logging.info("File downloaded successfully")
            
            # Excel'i oku ve soru sayısını hesapla
            try:
                logging.info(f"Starting to read file: {document.file_name}")
                
                # Check file extension
                if document.file_name.lower().endswith('.csv'):
                    df = pd.read_csv(io.BytesIO(file_bytes))
                    logging.info("Reading as CSV file")
                else:
                    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name='rfp')
                    logging.info("Reading as Excel file")
                
                logging.info(f"File read successfully. Columns found: {df.columns.tolist()}")
                
                # Add debug logging
                logging.info(f"Excel columns found: {df.columns.tolist()}")
                
                # Check for empty dataframe first
                if df.empty:
                    await update.message.reply_text(
                        "❌ Error: Excel file is empty."
                    )
                    return

                # Check for question column and make it case-insensitive
                columns_lower = [col.lower() for col in df.columns]
                if 'question' not in columns_lower:
                    logging.info(f"'question' column not found in columns: {df.columns.tolist()}")
                    await update.message.reply_text(
                        "❌ Error: Excel file does not have 'question' column.\n"
                        "Please make sure your Excel file has a column named 'question'.\n"
                        f"Found columns: {', '.join(df.columns.tolist())}"
                    )
                    return

            except Exception as e:
                logging.error(f"Error reading Excel: {str(e)}")
                await update.message.reply_text(
                    f"❌ Error reading Excel file: {str(e)}\n"
                    f"Please make sure the file has 'rfp' sheet with 'question' column."
                )
                return
            #print("debug 1")
            #print("FILE_RFP_EXCEL_COUNT: ",FILE_RFP_EXCEL_COUNT)
            #print("type: ",type(FILE_RFP_EXCEL_COUNT))
            # Add debug logging before the check
            question_count = df['question'].count()
            logging.info(f"Number of questions found: {question_count}")
            logging.info(f"Question limit (FILE_RFP_EXCEL_COUNT): {FILE_RFP_EXCEL_COUNT}")

            if question_count > FILE_RFP_EXCEL_COUNT:
                logging.info(f"Exceeded question limit: {question_count} > {FILE_RFP_EXCEL_COUNT}")
                await update.message.reply_text(
                    "❌ Error: Too many questions in Excel file!\n"
                    f"Your file has {question_count} questions.\n"
                    f"Maximum allowed is {FILE_RFP_EXCEL_COUNT} questions.\n"
                    "Please reduce the number of questions and try again."
                )
                logging.info("Sent error message to user about exceeding question limit")
                return

            # If we get here, the question count is okay
            logging.info("Question count is within limits, proceeding with processing")

            num_questions = len(df['question'])
            
            # Tahmini süreyi hesapla
            estimated_seconds = num_questions * 30  # Her soru 30 saniye
            estimated_minutes = estimated_seconds / 60  # Dakikaya çevir

            # Hemen tahmini süreyi göster
            await update.message.reply_text(
                f"📊 Excel file received!\n"
                f"File: {document.file_name}\n"
                f"Number of questions: {num_questions}\n"
                f"Estimated processing time: {estimated_minutes:.1f} minutes\n\n"
                f"Starting processing..."
            )

            # Şimdi asıl işleme başla
            asyncio.create_task(self._process_excel_file(update, context, message_id, file_bytes))

        except Exception as e:
            await update.message.reply_text(
                f"❌ Error reading Excel file: {str(e)}\n"
            )

    async def _process_excel_file(self, update, context, message_id, file_bytes):
        """Process Excel file with progress updates"""
        try:
            document = update.message.document
            
            # Send processing message
            processing_msg = await update.message.reply_text(
                f"⚙️ Processing Excel file...\n"
                f"File: {document.file_name}\n"
                f"Request ID: #{message_id}"
            )

            # Update status to processing
            self.active_excel_files[message_id] = {
                'filename': document.file_name,
                'status': 'processing',
                'start_time': time.time()
            }

            # Process in thread pool
            result = await asyncio.get_event_loop().run_in_executor(
                self.excel_executor,
                self._process_excel_sync,
                file_bytes,
                document.file_name
            )

            if result is None:
                await processing_msg.edit_text(
                    f"❌ Failed to process file\n"
                    f"File: {document.file_name}\n"
                    f"Please check the file format and try again."
                )
                return

            # Send processed file
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=io.BytesIO(result),
                filename=f'processed_{document.file_name}',
                caption=f"✅ Excel processing completed!\nRequest ID: #{message_id}"
            )
            await processing_msg.delete()

        except Exception as e:
            logging.error(f"Error processing file: {e}")
            await processing_msg.edit_text(
                f"❌ Error processing file\n"
                f"File: {document.file_name}\n"
                f"Error: {str(e)}"
            )
        finally:
            if message_id in self.active_excel_files:
                self.active_excel_files[message_id]['status'] = 'completed'
                self.active_excel_files[message_id]['end_time'] = time.time()

    def _process_excel_sync(self, file_bytes, filename):
        """Synchronous function to process Excel file"""
        try:
            headers = {
                "Authorization": f"Bearer {HF_TOKEN}",
                "accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            }

            files = {
                'file': (
                    filename, 
                    file_bytes, 
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            }

            response = requests.post(
                self.excel_url,
                headers=headers,
                files=files
            )

            if response.status_code == 200:
                return response.content
            else:
                logging.error(f"Excel API Error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logging.error(f"Excel processing error: {e}")
            return None

    async def _update_progress(self, message, message_id, filename):
        """Update progress message periodically"""
        try:
            while message_id in self.active_excel_files:
                elapsed_time = time.time() - self.active_excel_files[message_id]['start_time']
                hours = int(elapsed_time // 3600)
                minutes = int((elapsed_time % 3600) // 60)
                
                await message.edit_text(
                    f"⚙️ Processing Excel file...\n"
                    f"File: {filename}\n"
                    f"Request ID: #{message_id}\n"
                    f"Time elapsed: {hours}h {minutes}m\n"
                    f"Status: {self.active_excel_files[message_id]['status']}"
                )
                
                # Update every 5 minutes
                await asyncio.sleep(300)
        except Exception as e:
            logging.error(f"Error updating progress: {e}")

    async def status_command(self, update: Update, context: CallbackContext):
        """Show status of all active processes"""
        status_message = "Current Status:\n\n"

        # Text requests status
        if self.active_requests:
            status_message += "📝 Text Requests:\n"
            for msg_id, info in self.active_requests.items():
                current_time = time.time()
                processing_time = current_time - info['start_time']
                status_message += (
                    f"Request #{msg_id}:\n"
                    f"├─ Status: {info['status']}\n"
                    f"└─ Time: {processing_time:.1f}s\n\n"
                )

        # Excel files status
        if self.active_excel_files:
            status_message += "📊 Excel Files:\n"
            for msg_id, info in self.active_excel_files.items():
                current_time = time.time()
                processing_time = current_time - info['start_time']
                status_message += (
                    f"File #{msg_id}:\n"
                    f"├─ Name: {info['filename']}\n"
                    f"├─ Status: {info['status']}\n"
                    f"└─ Time: {processing_time:.1f}s\n\n"
                )

        if not self.active_requests and not self.active_excel_files:
            status_message += "No active processes"

        await update.message.reply_text(status_message)

    def setup_handlers(self):
        """Set up Telegram command and message handlers."""
        logging.info("Setting up message handlers...")
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Update Excel handler to include CSV files
        self.app.add_handler(MessageHandler(
            filters.Document.FileExtension("xlsx") | 
            filters.Document.FileExtension("xls") |
            filters.Document.FileExtension("csv"),  # Add CSV support
            self.handle_excel
        ))

    async def run(self):
        """Start the bot and listen for messages."""
        logging.info("Starting Telegram bot...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
    
    async def bot_stop(self):
        """Stop the bot."""
        logging.info("Stopping Telegram bot...")
        if self.app.updater:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

def init_bot():
    load_dotenv()
    logging.info("Initializing bot...")
    
    try:
        if os.getenv('SPACE_ID'):  # Check if running on Hugging Face
            logging.info("Running on Hugging Face, using custom settings...")
            application = Application.builder().token(BOT_TOKEN).base_url(
                "https://api.telegram.org/bot"
            ).get_updates_connection_pool_size(100).connection_pool_size(100).connect_timeout(30).read_timeout(30).write_timeout(30).pool_timeout(30).build()
            return TelegramBot(bot_token=BOT_TOKEN, base_url=BASE_URL, application=application)
        else:
            logging.info("Running locally, using default settings...")
            return TelegramBot(bot_token=BOT_TOKEN, base_url=BASE_URL)
    except Exception as e:
        logging.error(f"Error initializing bot: {str(e)}")
        raise