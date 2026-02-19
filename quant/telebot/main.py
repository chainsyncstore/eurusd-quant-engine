
import logging
import os
import asyncio
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from quant.telebot.models import Base, User, UserContext
from quant.telebot.auth import CryptoManager
from quant.telebot.manager import BotManager

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
    level=logging.INFO
)
logger = logging.getLogger(__name__)

FOOTER = "\n\nℹ️ Run /help to see command list"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def find_latest_model(root: Path = Path("models/production")) -> Path | None:
    if not root.exists():
        return None
    subdirs = sorted([x for x in root.iterdir() if x.is_dir() and "model_" in x.name], key=lambda x: x.name)
    if subdirs:
        return subdirs[-1]
    return None

# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------
# find model
MODEL_DIR = find_latest_model()
if not MODEL_DIR:
    logger.warning("No production model found in models/production! Bot will fail to start trading.")
else:
    logger.info(f"Using latest model: {MODEL_DIR}")

# Db
DB_PATH = os.path.abspath("quant_bot.db")
ENGINE = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=ENGINE)
Base.metadata.create_all(ENGINE)

# Crypto
# Ensure key exists or fail fast
if not os.getenv("BOT_MASTER_KEY"):
    # Generate one for dev convenience if missing, but warn
    k = CryptoManager.generate_key()
    logger.warning(f"BOT_MASTER_KEY not set! Using temporary key: {k}")
    # In prod, this would be an error. For now, we set it so auth works for this session.
    os.environ["BOT_MASTER_KEY"] = k

CRYPTO = CryptoManager()

# Manager
MANAGER = BotManager(MODEL_DIR) if MODEL_DIR else None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    session = SessionLocal()
    try:
        db_user = session.query(User).filter_by(telegram_id=user.id).first()
        
        if not db_user:
            # Register new user
            new_user = User(
                telegram_id=user.id,
                username=user.username,
                role='user',
                status='pending'
            )
            context_rec = UserContext(telegram_id=user.id)
            new_user.context = context_rec
            session.add(new_user)
            session.commit()
            
            await update.message.reply_text(
                f"👋 Welcome {user.first_name}!\n\n"
                "⏳ **Account Pending**\n"
                "Your request has been sent to the administrator.\n\n"
                "👉 **Next Step:** Wait for approval notification.\n"
                "_(You will be notified here automatically)_" + FOOTER
            )
            
            # Notify Admin
            admin_id = os.getenv("ADMIN_ID")
            if admin_id:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"🔔 **New User:** {user.first_name} (@{user.username}) [ID: {user.id}]\n"
                             f"👉 Run `/approve {user.id}` to grant access."
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin: {e}")
        elif db_user.status == 'pending':
            await update.message.reply_text("⏳ Request still pending. Please wait for admin approval." + FOOTER)
        elif db_user.status == 'banned':
            await update.message.reply_text("🚫 Access denied. Contact admin." + FOOTER)
        else:
            await update.message.reply_text(
                f"✅ **Welcome back, {user.first_name}!**\n\n"
                "System is ready.\n"
                "👉 **Next Step:** Check status or start trading.\n"
                "Run: `/status` or `/start_trading`" + FOOTER
            )
    except Exception as e:
        logger.error(f"Start error: {e}")
        await update.message.reply_text("⚠️ System error. Try again later.")
    finally:
        session.close()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"HELP COMMAND CALLED by {update.effective_user.id}")
    try:
        msg = (
            "COMMAND LIST\n\n"
            "Basics\n"
            "/start - Check account status\n"
            "/help - Show this menu\n\n"
            "Setup\n"
            "/setup <email> <key> <pass> - Connect Capital.com\n\n"
            "Trading\n"
            "/start_demo - Start PAPER trading\n"
            "/start_live - Start REAL trading\n"
            "/stop - Stop execution\n"
            "/status - Check if running\n"
            "/stats - View live performance"
        )
        
        user_id = update.effective_user.id
        admin_id_str = os.getenv("ADMIN_ID", "")
        if str(user_id) == admin_id_str:
            msg += "\n\nAdmin\n/approve <id> - Approve user\n/revoke <id> - Freeze user"
        
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"CRITICAL HELP ERROR: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Error displaying help menu.")

async def _start_engine(update: Update, context: ContextTypes.DEFAULT_TYPE, live: bool):
    if not MANAGER: return
    user_id = update.effective_user.id
    
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    if not user or user.status != 'active':
        await update.message.reply_text("⛔ Account not approved." + FOOTER)
        session.close()
        return
        
    ctx = user.context
    if not (ctx.capital_email and ctx.capital_api_key and ctx.capital_password):
        await update.message.reply_text("❌ Credentials missing. Run /setup first." + FOOTER)
        session.close()
        return
        
    # Decrypt
    try:
        creds = {
            'email': ctx.capital_email,
            'api_key': CRYPTO.decrypt(ctx.capital_api_key),
            'password': CRYPTO.decrypt(ctx.capital_password),
            'demo': not live
        }
    except Exception:
        await update.message.reply_text("❌ Decryption failed. Re-run /setup." + FOOTER)
        session.close()
        return
        
    # Update preference
    if ctx.live_mode != live:
        ctx.live_mode = live
        session.commit()
        
    session.close()

    mode_str = "LIVE 🔴" if live else "DEMO 🟢"
    
    if MANAGER.start_session(user_id, creds):
        await update.message.reply_text(
            f"🚀 **{mode_str} Trading STARTED**\n\n"
            "✅ Analysis running...\n"
            "👉 **Next Step:** Monitor performance.\n"
            "Run: `/stats`" + FOOTER
        )
    else:
        await update.message.reply_text("⚠️ Engine already running or failed to start." + FOOTER)

async def start_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_engine(update, context, live=False)

async def start_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_engine(update, context, live=True)

async def stop_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not MANAGER: return
    user_id = update.effective_user.id
    
    if MANAGER.stop_session(user_id):
        await update.message.reply_text(
            "Bzzt. **Engine STOPPED** 🛑\n\n"
            "To resume:\n"
            "`/start_demo` or `/start_live`" + FOOTER
        )
    else:
        await update.message.reply_text("⚠️ Engine not running." + FOOTER)


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin_id = os.getenv("ADMIN_ID")
    
    if str(user_id) != str(admin_id):
        return

    try:
        target_id = int(context.args[0])
        session = SessionLocal()
        user = session.query(User).filter_by(telegram_id=target_id).first()
        if user:
            user.status = 'banned'
            session.commit()
            
            # Stop engine if running
            if MANAGER and MANAGER.is_running(target_id):
                MANAGER.stop_session(target_id)
                await update.message.reply_text(f"🛑 active session for {target_id} stopped.")
                
            await update.message.reply_text(f"🚫 User {target_id} has been **FROZEN**." + FOOTER)

            try:
                await context.bot.send_message(target_id, "⛔ Your access has been revoked by the administrator.")
            except:
                pass 
        else:
            await update.message.reply_text("❌ User not found." + FOOTER)
        session.close()
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /revoke <user_id>" + FOOTER)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        print("Error: TELEGRAM_TOKEN environment variable is missing.")
        return
        
    application = ApplicationBuilder().token(token).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('menu', help_command))
    application.add_handler(CommandHandler('commands', help_command))
    
    application.add_handler(CommandHandler('approve', approve))
    application.add_handler(CommandHandler('revoke', revoke))
    application.add_handler(CommandHandler('setup', setup))
    application.add_handler(CommandHandler('start_demo', start_demo))
    application.add_handler(CommandHandler('start_live', start_live))
    application.add_handler(CommandHandler('stop', stop_trading))
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('stats', stats))
    
    # Debug: Log all updates
    async def debug_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"DEBUG: Received update: {update}")
    
    from telegram.ext import MessageHandler, filters
    application.add_handler(MessageHandler(filters.ALL, debug_log), group=1)
    
    application.add_error_handler(error_handler)
    
    print("Bot is polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
