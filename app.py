import sys
import json
import os

import re
import shutil
import sqlite3
import pyotp
import qrcode
import io
import base64
import logging
from logging.handlers import RotatingFileHandler


DEBUG_MODE = False    # ⭐ Flip to True only when you want debug output


# =======================================================
# CORE & STANDARD LIBRARIES
# =======================================================
from datetime import datetime, date, timedelta, timezone
import secrets
import subprocess
import inspect
from cryptography.fernet import Fernet

# =======================================================
# THIRD-PARTY UTILITIES & MEDIA
# =======================================================
from dateutil.relativedelta import relativedelta
import pytz
from PIL import Image
import pillow_heif


# =======================================================
# FLASK & WEB EXTENSIONS
# =======================================================
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    session,
    redirect,
    flash,
    url_for,
    abort
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user
)
from flask_mail import Mail, Message as MailMessage
from flask_migrate import Migrate


# =======================================================
# SECURITY & WERKZEUG UTILITIES
# =======================================================
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException

# =======================================================
# DATABASE & ORM MODELS
# =======================================================
from sqlalchemy import func
from database import db
from models import (
    User,
    Company,
    PasswordResetToken,
    LoginCode,
    Message,
    WeeklyDuty,
    DutyBus,
    Settings,
    Takings,
    WeeklyTakings,
    DailyEntries,
    AnnulledTicket,
    Enthusiast,
    EnthusiastPhoto,
    PayRate,
    SpecialDay,
    RoleChangeHistory,
    UserMessages,
    SiteStats
)


# =======================================================
# REPORTLAB (PDF GENERATION)
# =======================================================
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import (
    BaseDocTemplate,
    PageTemplate,
    Frame,
    Table,
    TableStyle,
    Image as RLImage,
    Spacer,
    Paragraph
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# =======================================================
# CREATE FLASK APP & CONFIGURATION
# =======================================================
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

# Handle Render's PostgreSQL URL format for SQLAlchemy
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///transport.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from database import db

db.init_app(app)
migrate = Migrate(app, db)

with app.app_context():
    db.create_all()
    print("Database tables checked/created successfully!")


# =======================================================
# AUTOMATIC SCHEMA PATCH
# =======================================================
with app.app_context():
    db_path = os.path.join(app.root_path, "instance", "drivershub.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(app.root_path, "drivershub.db")
    
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Patch users table (2FA columns)
        cursor.execute("PRAGMA table_info(users);")
        user_columns = [col[1] for col in cursor.fetchall()]
        
        if "totp_secret" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(32);")
        if "is_2fa_enabled" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_2fa_enabled BOOLEAN DEFAULT 0 NOT NULL;")

        # 2. Patch daily_entries table (bus columns)
        cursor.execute("PRAGMA table_info(daily_entries);")
        entry_columns = [col[1] for col in cursor.fetchall()]
        
        for col_name in ["bus1", "bus2", "bus3"]:
            if col_name not in entry_columns:
                cursor.execute(f"ALTER TABLE daily_entries ADD COLUMN {col_name} INTEGER;")

        # 3. Patch site_stats table (lifted_count and deleted_count columns)
        cursor.execute("PRAGMA table_info(site_stats);")
        stats_columns = [col[1] for col in cursor.fetchall()]
        
        if stats_columns:
            if "lifted_count" not in stats_columns:
                cursor.execute("ALTER TABLE site_stats ADD COLUMN lifted_count INTEGER DEFAULT 0 NOT NULL;")
            if "deleted_count" not in stats_columns:
                cursor.execute("ALTER TABLE site_stats ADD COLUMN deleted_count INTEGER DEFAULT 0 NOT NULL;")
            
        conn.commit()
        conn.close()
# =======================================================
# LOGIN MANAGER SETUP
# =======================================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# =======================================================
# ROUTES (Placed AFTER setup and models)
# =======================================================

@app.route('/')
def index():
    # Increment specifically for home page arrivals
    try:
        stats = SiteStats.query.first()
        if stats:
            stats.home_visit_count += 1
            db.session.commit()
    except Exception:
        db.session.rollback()

    return render_template('index.html')

# ---------------------------------------------------------
# GLOBAL HELPER: CREATE PLATFORM MESSAGE (SAFE & ROBUST)
# ---------------------------------------------------------
def create_platform_message(receiver_id=None, subject="System Update", body="", sender_id=1, msg_type="system_upgrade", **kwargs):
    """Helper function to safely create an internal system/platform message for a user."""
    target_user = receiver_id or kwargs.get("user_id")
    
    if target_user:
        # Ensure sender exists, otherwise fallback to the receiver or first available user
        sender = db.session.get(User, sender_id)
        if not sender:
            first_user = User.query.first()
            sender_id = first_user.id if first_user else target_user

        msg = UserMessages(
            sender_id=sender_id,      
            receiver_id=target_user,
            title=subject,            
            body=body,
            type=msg_type,            
            read=False
        )
        db.session.add(msg)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"ERROR creating platform message: {e}")
# =======================================================
# PERSISTENT MAINTENANCE STATE HELPERS (ROBUST)
# =======================================================
STATE_FILE = os.path.join(app.root_path, "maintenance_state.json")

def load_maintenance_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("maintenance_mode", False)
    except Exception as e:
        app.logger.error(f"Error loading maintenance state: {e}")
    return False

def save_maintenance_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"maintenance_mode": state}, f)
    except Exception as e:
        app.logger.error(f"Error saving maintenance state: {e}")

# Load the saved state into app config when the app boots up
app.config['MAINTENANCE_MODE'] = load_maintenance_state() 

# ---------------------------------------------------------
# GLOBAL SECURITY, SUSPENSION, & MAINTENANCE INTERCEPTOR
# ---------------------------------------------------------
@app.before_request
def global_system_checks():
    
    # 0. Global Site Visit Tracker (Skips static assets to avoid inflation)
    if request.endpoint != "static":
        try:
            stats = SiteStats.query.first()
            if not stats:
                stats = SiteStats(visit_count=1, deleted_count=0, lifted_count=0)
                db.session.add(stats)
            else:
                stats.visit_count += 1
            db.session.commit()
        except Exception:
            db.session.rollback()  # Safeguard against unexpected database locks
            
    # 1. Prevent stale cookies/sessions by refreshing the user state from the live database
    if current_user.is_authenticated:
        try:
            db.session.refresh(current_user)
            
            # ⭐ SILENTLY ENFORCE MASTER SUPERADMIN ROLES & LEVEL
            if current_user.email and current_user.email.strip().lower() == 'info@transporthub.uk':
                if not (current_user.role_superadmin and current_user.role_admin and current_user.role_driver and current_user.role_enthusiast and current_user.level == 1):
                    current_user.role_superadmin = True
                    current_user.role_admin = True
                    current_user.role_driver = True
                    current_user.role_enthusiast = True
                    current_user.level = 1
                    db.session.commit()
        except Exception:
            db.session.rollback() # Failsafe if session detached

        # 2. Dynamic Suspension Guard & Auto-Release
        is_currently_suspended = bool(
            current_user.suspended_until and 
            current_user.suspended_until > datetime.now(timezone.utc).replace(tzinfo=None)
        )
        
        if request.endpoint == "suspended" and not is_currently_suspended:
            # If they are sitting on the suspended page, but the DB says they are clean, kick them to their dashboard
            if getattr(current_user, "role_enthusiast", False) and not getattr(current_user, "role_driver", False):
                return redirect(url_for("enthusiast_dashboard"))
            return redirect(url_for("driver_dashboard"))

        if is_currently_suspended:
            # If they are suspended, trap them on the suspension page (except for static assets or logout)
            allowed_suspended_endpoints = ("suspended", "static", "logout")
            if request.endpoint not in allowed_suspended_endpoints:
                return redirect(url_for("suspended"))

    # 3. Maintenance Mode Interceptor
    if load_maintenance_state():
        allowed_maintenance_endpoints = (
            "maintenance", 
            "static", 
            "unified_login", 
            "verify_2fa", 
            "login",
            "logout",
            "suspended"
        )
        
        if request.endpoint in allowed_maintenance_endpoints:
            return

        # Allow your developer account to bypass maintenance
        if current_user.is_authenticated and getattr(current_user, 'email', '').strip().lower() == 'info@transporthub.uk':
            return
            
        return redirect(url_for("maintenance"))
# =======================================================
# TOGGLE MAINTENANCE ROUTE
# =======================================================
@app.route('/admin/toggle-maintenance', methods=['POST'])
@login_required
def toggle_maintenance():
    # Bulletproof Superadmin check
    is_super = session.get('is_superadmin') or (
        current_user.is_authenticated and getattr(current_user, 'role_superadmin', False)
    )
    if not is_super:
        return redirect(url_for("login"))
    
    # Toggle the current state
    current_state = app.config.get('MAINTENANCE_MODE', False)
    new_state = not current_state
    
    app.config['MAINTENANCE_MODE'] = new_state
    save_maintenance_state(new_state)
    
    app.logger.info(f"Maintenance mode toggled to: {new_state} by User {current_user.id}")
    return redirect(url_for('superadmin_tools'))

# =======================================================
# MAINTENANCE PAGE ROUTE
# =======================================================
# Optional helper alias if anything else looks for this name
def get_maintenance_state():
    return load_maintenance_state()

@app.route("/maintenance")
def maintenance():
    # If maintenance mode was turned off, send them back safely
    if not load_maintenance_state():
        if current_user.is_authenticated:
            target = getattr(current_user, 'home_target', 'login')
            return redirect(url_for(target) if target else "/")
        return redirect(url_for("login"))
        
    return render_template("maintenance.html")  

# =======================================================
# SYSTEM LOGGING CONFIGURATION
# =======================================================
log_file_path = os.path.join(app.root_path, "app.log")
file_handler = RotatingFileHandler(log_file_path, maxBytes=1024000, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)

# Attach handler to Flask app logger
if not app.logger.handlers:
    app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

app.logger.info("Transport Hub system starting up...")

# =======================================================
# TIMEZONE
# =======================================================
uk = pytz.timezone("Europe/London")

# Get timezone-aware UTC directly (avoids the deprecation warning)
utc_now = datetime.now(pytz.utc)
uk_now = utc_now.astimezone(uk)

print("UTC:", utc_now.strftime("%Y-%m-%d %H:%M:%S"))
print("UK :", uk_now.strftime("%Y-%m-%d %H:%M:%S"))

# =======================================================
# SUPERADMIN USER ANALYTICS ROUTE (FIXED)
# =======================================================
@app.route('/admin/user-analytics')
@login_required
def user_analytics():
    if not getattr(current_user, 'role_superadmin', False):
        abort(403)

    # Date calculations for current vs previous month strings
    now = datetime.utcnow()
    current_month_start_str = now.replace(day=1).strftime('%Y-%m-%d')
    
    # Calculate previous month start string safely
    prev_month_date = now.replace(day=1) - relativedelta(months=1)
    prev_month_start_str = prev_month_date.strftime('%Y-%m-%d')
    
    def get_trend_data(current_count, prev_count):
        if prev_count == 0:
            percent = 100.0 if current_count > 0 else 0.0
        else:
            percent = round(abs(((current_count - prev_count) / prev_count) * 100), 1)
        
        is_increase = current_count >= prev_count
        return {
            'count': current_count,
            'percent': percent,
            'direction': 'up' if is_increase else 'down',
            'color': 'green' if is_increase else 'red',
            'icon': 'fa-arrow-up' if is_increase else 'fa-arrow-down'
        }

    # --- Database Queries using String Comparisons ---
    drivers_current = User.query.filter(User.role_driver == True, User.joined_date >= current_month_start_str).count()
    drivers_prev = User.query.filter(User.role_driver == True, User.joined_date >= prev_month_start_str, User.joined_date < current_month_start_str).count()
    driver_stats = get_trend_data(drivers_current, drivers_prev)

    enthusiasts_current = User.query.filter(User.role_enthusiast == True, User.joined_date >= current_month_start_str).count()
    enthusiasts_prev = User.query.filter(User.role_enthusiast == True, User.joined_date >= prev_month_start_str, User.joined_date < current_month_start_str).count()
    enthusiast_stats = get_trend_data(enthusiasts_current, enthusiasts_prev)

    # Check boolean flags properly instead of integers (True/False)
    driver_deletions = User.query.filter(User.role_driver == True, User.deleted == True).count()
    enthusiast_deletions = User.query.filter(User.role_enthusiast == True, User.deleted == True).count()

    return render_template(
        'admin_analytics.html', 
        drivers=driver_stats, 
        enthusiasts=enthusiast_stats,
        driver_deletions=driver_deletions,
        enthusiast_deletions=enthusiast_deletions
    )
    
# -------------------------------------------------------
# WEEKLY DUTY TOTAL CALCULATIONS (BREAKS / ISSUES / NDW)
# -------------------------------------------------------

def hhmm_to_minutes(hhmm):
    if hhmm is None:
        return 0

    # If already an integer (minutes)
    if isinstance(hhmm, int):
        return hhmm

    # If empty string
    if hhmm == "":
        return 0

    # If HH:MM format
    if isinstance(hhmm, str) and ":" in hhmm:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    # If numeric string like "15"
    if isinstance(hhmm, str) and hhmm.isdigit():
        return int(hhmm)

    return 0


def minutes_to_hhmm(total):
    return f"{total//60:02d}:{total%60:02d}"


def calculate_break_total(breaks_json):
    if not breaks_json:
        return "00:00"

    # If already a Python list, use it directly
    if isinstance(breaks_json, list):
        items = breaks_json
    else:
        try:
            items = json.loads(breaks_json)
        except:
            return "00:00"

    total = 0
    for b in items:
        start = b.get("start")
        end = b.get("end")
        if start and end:
            s = datetime.strptime(start, "%H:%M")
            f = datetime.strptime(end, "%H:%M")
            total += int((f - s).total_seconds() // 60)

    return minutes_to_hhmm(total)


def calculate_issue_total(issue_json):
    if not issue_json:
        return "00:00"

    if isinstance(issue_json, list):
        items = issue_json
    else:
        try:
            items = json.loads(issue_json)
        except:
            return "00:00"

    total = 0
    for i in items:
        delay = i.get("delay")
        if isinstance(delay, int):
            total += delay
        elif isinstance(delay, str) and delay.isdigit():
            total += int(delay)

    return minutes_to_hhmm(total)


def calculate_ndw_total(ndw_json):
    if not ndw_json:
        return "00:00"

    if isinstance(ndw_json, list):
        items = ndw_json
    else:
        try:
            items = json.loads(ndw_json)
        except:
            return "00:00"

    total = 0
    for n in items:
        duration = n.get("duration")
        if isinstance(duration, int):
            total += duration
        elif isinstance(duration, str) and duration.isdigit():
            total += int(duration)

    return minutes_to_hhmm(total)



# =======================================================
# DEFAULT SETTINGS FALLBACK
# =======================================================
class DefaultSettings:
    theme = "light"
    year_mode = "calendar"

@app.before_request
def store_last_page():
    if request.method == "GET":
        if request.path != "/settings":
            session["last_page"] = request.path


# =======================================================
# SETTINGS INJECTOR
# =======================================================
def inject_settings():
    user_id = session.get("user_id")

    # No logged-in user → return safe defaults
    if not user_id:
        return dict(settings=DefaultSettings())

    # Try to load settings for this user
    settings = Settings.query.filter_by(user_id=user_id).first()

    # If no DB row exists → return safe defaults
    if not settings:
        return dict(settings=DefaultSettings())

    return dict(settings=settings)
    
# ========================================================
# context_processor FOR DELETE ACCOUNTS
# ========================================================
@app.context_processor
def inject_home_target():
    user_id = session.get("user_id")
    if not user_id:
        return dict(
            home_target="login",
            user_roles=[]
        )

    user = db.session.get(User, user_id)
    if not user:
        return dict(
            home_target="login",
            user_roles=[]
        )




    if user.role_driver:
        home = "driver_dashboard"
    elif user.role_enthusiast:
        home = "enthusiast_dashboard"
    else:
        home = "enthusiast_dashboard"

    return dict(
        home_target=home,
        user_roles=get_all_roles(user)
    )

def get_all_roles(user):

    # Prevent crash when user is not logged in
    if not user.is_authenticated:
        return []

    roles = []

    if current_user.role_superadmin:
        roles.append("Superadmin")
    if current_user.role_admin:
        roles.append("Admin")
    if user.role_driver:
        roles.append("Driver")
    if user.role_enthusiast:
        roles.append("Enthusiast")

    return roles
  

   
# -------------------------------------------------
# UK DATE FILTER
# -------------------------------------------------
@app.template_filter("ukdate")
def ukdate(value):
    """Format a date object or ISO string into UK format DD/MM/YYYY with weekday."""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d").date()
        except Exception:
            return value  # fallback: return raw string
    return value.strftime("%A %d/%m/%Y")
    
# -------------------------------------------------
# NO-CACHE HEADERS
# -------------------------------------------------
# @app.after_request
# def add_no_cache_headers(response):
#     response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
#     response.headers["Pragma"] = "no-cache"
#     response.headers["Expires"] = "0"
#     return response

# Make json available inside Jinja templates
app.jinja_env.globals["json"] = json

# ---------------------------------------------------------
# UPLOAD FOLDERS (CORRECTED)
# ---------------------------------------------------------

# Waybill upload folder (private)
WAYBILL_UPLOAD_FOLDER = "uploads"
os.makedirs(WAYBILL_UPLOAD_FOLDER, exist_ok=True)

# Profile photo upload folder (public)
PROFILE_UPLOAD_FOLDER = "static/profile_photos"
os.makedirs(PROFILE_UPLOAD_FOLDER, exist_ok=True)

# ---------------------------------------------------------
# JINJA FILTER: UK DATE FORMAT
# ---------------------------------------------------------
@app.template_filter("ukdate")
def ukdate(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")
    except:
        return value

app.secret_key = "supersecretkey123"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///drivershub.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Profile photo upload config
app.config["UPLOAD_FOLDER"] = PROFILE_UPLOAD_FOLDER

# Max upload size: 3 MB
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024


@app.errorhandler(413)
def too_large(e):
    return "File too large. Max size is 3 MB.", 413

with app.app_context():
    db.create_all()

# ---------------------------------------------------------
# EMAIL CONFIG
# ---------------------------------------------------------
app.config["MAIL_SERVER"] = "smtp.ionos.co.uk"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "info@transporthub.uk"
app.config["MAIL_PASSWORD"] = "Drivingtothelimits2019"   # or your real Outlook password
app.config["MAIL_DEFAULT_SENDER"] = "info@transporthub.uk"

mail = Mail(app)

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def _get_or_create_company(name: str):
    """Always ensure a Company row exists for a given name, return its id."""
    if not name:
        return None

    name = name.strip()
    if not name:
        return None

    existing = Company.query.filter_by(name=name).first()
    if existing:
        return existing.id

    new_company = Company(name=name)
    db.session.add(new_company)
    db.session.commit()
    return new_company.id

def _compute_company_id_for_user(user: User):
    """
    Compute company_id for a user based on their stored company name.
    Always returns a Company.id, creating the company if needed.
    """
    company_name = getattr(user, "company", None)
    if not company_name:
        return None
    return _get_or_create_company(company_name)

def _current_user():
    """Return the currently logged-in user object or None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, int(user_id))

# ---------------------------------------------------------
# API ROUTES
# ---------------------------------------------------------
@app.get("/api/companies")
def get_companies():
    companies = Company.query.order_by(Company.name.asc()).all()
    return jsonify([{"id": c.id, "name": c.name} for c in companies])

# ---------------------------------------------------------
# AUTO-ENSURE SUPERADMIN HOOK
# ---------------------------------------------------------
@app.before_request
def auto_ensure_superadmin():
    """Silently ensures the master account always has Superadmin and all role flags active."""
    target_email = "info@transporthub.uk"
    
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if user and user.email == target_email:
            if not user.role_superadmin or not user.role_admin or not user.role_driver or not user.role_enthusiast or user.level != 1:
                user.role_superadmin = True
                user.role_admin = True
                user.role_driver = True
                user.role_enthusiast = True
                user.level = 1
                db.session.commit()
                
# ======================================================
# ROLE HELPERS
# ======================================================
def is_superadmin(user):
    return bool(user.role_superadmin) if user and user.is_authenticated else False

def is_admin(user):
    return bool(user.role_admin) if user and user.is_authenticated else False

# ======================================================
# SUPERADMIN / ADMIN DASHBOARD ROUTE
# ======================================================  
@app.route("/admin_dashboard")
@login_required
def admin_dashboard():

    # Allow Admins and Superadmins using the actual user attributes
    if not (getattr(current_user, 'role_admin', False) or getattr(current_user, 'role_superadmin', False)):
        return redirect(url_for("user_accounts")) # or redirect back to login/home

    # Birthday popup logic
    show_birthday = False

    if current_user.date_of_birth:
        today = datetime.today().date()

        # Handles date stored as string YYYY-MM-DD or date object
        if isinstance(current_user.date_of_birth, str):
            try:
                dob = datetime.strptime(current_user.date_of_birth, "%Y-%m-%d").date()
            except ValueError:
                dob = None
        else:
            dob = current_user.date_of_birth

        if dob and dob.day == today.day and dob.month == today.month:
            show_birthday = True

    return render_template(
        "admin_dashboard.html",
        user=current_user,
        show_birthday=show_birthday
    )

# =========================================================
# SUPERADMIN ROUTE
# =========================================================
@app.route("/superadmin-tools")
@login_required
def superadmin_tools():
    if not is_superadmin(current_user):
        return redirect(url_for("login"))
    return render_template("superadmin_dashboard.html", user=current_user)

# =========================================================
# USER ACCOUNTS (ADMIN) ROUTE
# =========================================================
@app.route('/user_accounts')
@login_required
def user_accounts():
    users = User.query.filter(User.id != 1).all()

    # Build readable role for each user using LEVEL system
    for u in users:
        if u.role_superadmin:
            u.role = "Superadmin"
        elif u.role_admin:
            u.role = "Admin"
        elif u.role_driver:
            u.role = "Driver"
        elif u.role_enthusiast:
            u.role = "Enthusiast"
        else:
            u.role = "Unknown"

    # ============================
    # USER COUNTS FOR STATS GRID
    # ============================

    total_users = User.query.count()

    enthusiast_count = User.query.filter_by(role_enthusiast=True).count()
    driver_count = User.query.filter_by(role_driver=True).count()
    admin_count = User.query.filter_by(role_admin=True).count()
    superadmin_count = User.query.filter_by(role_superadmin=True).count()

    upgraded_count = RoleChangeHistory.query.filter_by(change_type="upgrade").count()
    downgraded_count = RoleChangeHistory.query.filter_by(change_type="downgrade").count()

    suspended_count = User.query.filter(User.suspended_until.isnot(None)).count()

    lifted_count = User.query.filter(
        (User.suspended_until == None) &
        (User.suspension_reason != None)
    ).count()

    deleted_count = User.query.filter_by(deleted=True).count()

    # ============================
    # SITE STATS (GLOBAL TRAFFIC)
    # ============================
    stats = SiteStats.query.first()
    total_page_views = stats.visit_count if stats else 0
    home_visit_count = stats.home_visit_count if stats else 0

    return render_template(
        'user_accounts.html',
        users=users,
        companies=[],
        user=current_user,

        # Pass counts to template
        total_users=total_users,
        total_page_views=total_page_views,
        home_visit_count=home_visit_count,
        enthusiast_count=enthusiast_count,
        driver_count=driver_count,
        admin_count=admin_count,
        superadmin_count=superadmin_count,
        upgraded_count=upgraded_count,
        downgraded_count=downgraded_count,
        suspended_count=suspended_count,
        lifted_count=lifted_count,
        deleted_count=deleted_count
    )


# =======================================================
# SYSTEM LOGGING CONFIGURATION
# =======================================================
class FlushableRotatingFileHandler(RotatingFileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

log_file_path = os.path.join(app.root_path, "app.log")
file_handler = FlushableRotatingFileHandler(log_file_path, maxBytes=1024000, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)

# Attach to BOTH root logger and app.logger
logging.getLogger().addHandler(file_handler)
logging.getLogger().setLevel(logging.INFO)

if not app.logger.handlers:
    app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

app.logger.info("Transport Hub system starting up...")




# =======================================================
# AUTOMATIC ERROR LOGGING
# =======================================================
@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through standard HTTP errors (like 404 Not Found or 405 Method Not Allowed)
    if isinstance(e, HTTPException):
        return e

    # Log full exception with stack trace to app.log
    app.logger.error(f"Unhandled Exception: {str(e)}", exc_info=True)
    
    # Return 500 template if present, else plain text fallback
    template_exists = os.path.exists(os.path.join(app.root_path, app.template_folder or 'templates', '500.html'))
    if template_exists:
        return render_template("500.html"), 500
    return "Internal Server Error", 500




# =======================================================
# SYSTEM LOGS VIEW ROUTE
# =======================================================
@app.route("/system_logs")
@login_required
def system_logs():
    # 1. Authorization check
    is_authorized = False
    if hasattr(current_user, 'level') and current_user.level in [1, 2]:
        is_authorized = True
    elif callable(globals().get('is_superadmin')) and callable(globals().get('is_admin')):
        is_authorized = is_superadmin(current_user) or is_admin(current_user)

    if not is_authorized:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("login"))

    # 2. Read and format logs safely
    logs = []

    if os.path.exists(log_file_path):
        try:
            with open(log_file_path, "r", encoding="utf-8", errors="replace") as file:
                # Fetch last 300 log entries
                raw_lines = file.readlines()[-300:]
                
                # Strip ANSI color codes and whitespace
                logs = [
                    re.sub(r'\x1b\[[0-9;]*m', '', line).strip() 
                    for line in raw_lines 
                    if line.strip()
                ]
                logs.reverse()
        except Exception as err:
            logs = [f"Error reading log file: {str(err)}"]
    
    if not logs:
        logs = ["No log entries recorded yet in app.log."]

    return render_template("system_logs.html", logs=logs, user=current_user)



# Helper to check SuperAdmin status
def is_superadmin_user(user):
    if hasattr(user, 'level'):
        return user.level == 1
    elif callable(globals().get('is_superadmin')):
        return is_superadmin(user)
    return False


# =======================================================
# DATABASE BACKUP ENCRYPTION HELPERS
# =======================================================
KEY_FILE = os.path.join(app.root_path, "secret.key")

def get_or_create_encryption_key():
    """Loads the secret key or generates one automatically if it doesn't exist yet."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        app.logger.info("New backup encryption key generated and saved securely.")
        return key

def encrypt_file(source_path, dest_path):
    """Encrypts a file (like your SQLite database) into a secure .enc file."""
    key = get_or_create_encryption_key()
    fernet = Fernet(key)
    
    with open(source_path, "rb") as file:
        file_data = file.read()
        
    encrypted_data = fernet.encrypt(file_data)
    
    with open(dest_path, "wb") as file:
        file.write(encrypted_data)

def decrypt_file(source_path, dest_path):
    """Decrypts a secure .enc backup back into a usable database file for restoration."""
    key = get_or_create_encryption_key()
    fernet = Fernet(key)
    
    with open(source_path, "rb") as file:
        encrypted_data = file.read()
        
    decrypted_data = fernet.decrypt(encrypted_data)
    
    with open(dest_path, "wb") as file:
        file.write(decrypted_data)
# =======================================================
# BACKUP & RESTORE ROUTES (ENCRYPTED)
# =======================================================
MAX_BACKUPS_TO_KEEP = 5  # Keeps only the 5 most recent backups automatically


@app.route("/backup_tools")
@login_required
def backup_tools():
    if not is_superadmin_user(current_user):
        flash("Unauthorized access. SuperAdmin privileges required.", "danger")
        return redirect(url_for("index"))

    # Create backup directory if it doesn't exist
    backup_dir = os.path.join(app.root_path, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    # List existing encrypted backups (newest first)
    backup_files = []
    if os.path.exists(backup_dir):
        files = os.listdir(backup_dir)
        files = [f for f in files if f.endswith('.enc')]
        files.sort(reverse=True)
        for f in files:
            file_path = os.path.join(backup_dir, f)
            size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M:%S')
            backup_files.append({"filename": f, "size": f"{size_mb} MB", "created": mtime})

    return render_template("backup_tools.html", backups=backup_files, user=current_user)


@app.route("/create_backup", methods=["POST"])
@login_required
def create_backup():
    if not is_superadmin_user(current_user):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("index"))

    db_path = os.path.join(app.root_path, "instance", "drivershub.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(app.root_path, "drivershub.db")

    backup_dir = os.path.join(app.root_path, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_db_path = os.path.join(backup_dir, f"temp_{timestamp}.db")
    backup_filename = f"backup_drivershub_{timestamp}.enc"
    backup_path = os.path.join(backup_dir, backup_filename)

    try:
        # 1. Live SQLite safe backup to temporary raw file
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(temp_db_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        # 2. Encrypt the temporary database file into a secure .enc backup
        encrypt_file(temp_db_path, backup_path)

        # 3. Remove the unencrypted temporary raw file
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)

        # 4. Auto-rotate: keep only the latest MAX_BACKUPS_TO_KEEP .enc files
        files = [f for f in os.listdir(backup_dir) if f.startswith('backup_drivershub_') and f.endswith('.enc')]
        files.sort(reverse=True)  # Newest first

        if len(files) > MAX_BACKUPS_TO_KEEP:
            for old_file in files[MAX_BACKUPS_TO_KEEP:]:
                try:
                    os.remove(os.path.join(backup_dir, old_file))
                except Exception:
                    pass

        app.logger.info(f"Encrypted database backup created by User {current_user.id}: {backup_filename}")
        flash(f"Encrypted backup created successfully: {backup_filename}", "success")
    except Exception as e:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
        app.logger.error(f"Failed to create encrypted backup: {str(e)}")
        flash(f"Backup failed: {str(e)}", "danger")

    return redirect(url_for("backup_tools"))


@app.route("/download_backup/<filename>")
@login_required
def download_backup(filename):
    if not is_superadmin_user(current_user):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("index"))

    backup_dir = os.path.join(app.root_path, "backups")
    safe_filename = secure_filename(filename)
    file_path = os.path.join(backup_dir, safe_filename)

    if os.path.exists(file_path) and file_path.endswith('.enc'):
        from flask import send_file
        return send_file(file_path, as_attachment=True)

    flash("Backup file not found or invalid format.", "danger")
    return redirect(url_for("backup_tools"))


@app.route("/delete_backup/<filename>", methods=["POST"])
@login_required
def delete_backup(filename):
    if not is_superadmin_user(current_user):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("index"))

    backup_dir = os.path.join(app.root_path, "backups")
    safe_filename = secure_filename(filename)
    file_path = os.path.join(backup_dir, safe_filename)

    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            flash(f"Deleted backup: {safe_filename}", "info")
        except Exception as e:
            flash(f"Failed to delete backup: {str(e)}", "danger")
    else:
        flash("Backup file not found.", "warning")

    return redirect(url_for("backup_tools"))


@app.route("/restore_backup", methods=["POST"])
@login_required
def restore_backup():
    if not is_superadmin_user(current_user):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("index"))

    if 'backup_file' not in request.files:
        flash("No file selected for restore.", "warning")
        return redirect(url_for("backup_tools"))

    file = request.files['backup_file']
    if file.filename == '':
        flash("No file selected.", "warning")
        return redirect(url_for("backup_tools"))

    if not file.filename.endswith('.enc'):
        flash("Invalid file format. Please upload a secure .enc backup file.", "danger")
        return redirect(url_for("backup_tools"))

    db_path = os.path.join(app.root_path, "instance", "drivershub.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(app.root_path, "drivershub.db")

    backup_dir = os.path.join(app.root_path, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    temp_upload_path = os.path.join(backup_dir, f"temp_upload_{timestamp}.enc")

    try:
        # Save the uploaded encrypted backup temporarily
        file.save(temp_upload_path)

        # Create a safety pre-restore backup of the current live database first (also encrypted!)
        temp_pre_db = os.path.join(backup_dir, f"temp_pre_{timestamp}.db")
        pre_restore_path = os.path.join(backup_dir, f"pre_restore_{timestamp}.enc")
        
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(temp_pre_db)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        
        encrypt_file(temp_pre_db, pre_restore_path)
        if os.path.exists(temp_pre_db):
            os.remove(temp_pre_db)

        # Close active database sessions
        db.session.remove()

        # Decrypt the uploaded backup directly onto the live database path
        decrypt_file(temp_upload_path, db_path)

        # Clean up temporary upload file
        if os.path.exists(temp_upload_path):
            os.remove(temp_upload_path)

        app.logger.warning(f"Database RESTORED by User {current_user.id} using encrypted file: {file.filename}")
        flash("Database restored successfully from encrypted backup!", "success")
    except Exception as e:
        if os.path.exists(temp_upload_path):
            os.remove(temp_upload_path)
        app.logger.error(f"Failed to restore database: {str(e)}")
        flash(f"Restore failed: {str(e)}", "danger")

    return redirect(url_for("backup_tools"))
    
# ==========================================
# PLATFORM MESSAGES (Unified, with masking)
# ==========================================
@app.route("/platform_messages")
@login_required
def platform_messages():
    # Logged-in user
    user = current_user

    # All users (for admin/superadmin dropdown)
    users = User.query.all()

    # All messages sent to OR from the logged-in user
    messages = UserMessages.query.order_by(UserMessages.created_at.desc()).all()

    # Unread count for logged-in user only (using is_read or read matching your model)
    unread_count = UserMessages.query.filter(
        UserMessages.receiver_id == user.id,
        UserMessages.is_read == False
    ).count()

    return render_template(
        "platform_messages.html",
        user=user,                # ⭐ REQUIRED
        current_user=current_user,
        users=users,
        messages=messages,
        unread_count=unread_count
    )


@app.route("/mark_read/<int:msg_id>", methods=["POST"])
@login_required
def mark_read(msg_id):
    msg = UserMessages.query.get(msg_id)

    if not msg:
        return jsonify({"success": False, "error": "Message not found"}), 404

    # Only allow the receiver to mark it read
    if msg.receiver_id != current_user.id:
        return jsonify({"success": False, "error": "Not allowed"}), 403

    msg.is_read = True
    db.session.commit()

    return jsonify({"success": True})

# =========================================
# SET ROLE
# =========================================
@app.route('/set_role', methods=['POST'])
@login_required
def set_role():
    data = request.get_json()
 

    user_id = data.get('user_id')
    change_type = data.get('change_type')
    new_role = data.get('role')
    reason = data.get('reason')
    suspend_length = data.get('suspend_length')

    user = db.session.get(User, int(user_id))
    if not user:
        return jsonify({"error": "User not found"}), 404

    old_role = get_role_string(user)

    # ============================================================
    # DELETE USER
    # ============================================================
    if change_type == "delete":
        log_role_change(
            user_id=user.id,
            admin_id=current_user.id,
            change_type="delete",
            old_role=old_role,
            new_role="None",
            reason=reason,
            extra_info=None
        )
        db.session.delete(user)
        db.session.commit()

        create_platform_message(
            sender_id=current_user.id,
            receiver_id=user.id,
            title="Account Deleted",
            body=f"Your account has been deleted. Reason: {reason}",
            msg_type="system_delete",
            popup=True
        )

        return jsonify({"status": "deleted"})

    # ============================================================
    # SUSPEND USER
    # ============================================================
    if change_type == "suspend":
        days = int(suspend_length)
        user.suspended_until = datetime.utcnow() + timedelta(days=days)
        user.suspension_reason = reason
        db.session.commit()

        log_role_change(
            user_id=user.id,
            admin_id=current_user.id,
            change_type="suspend",
            old_role=old_role,
            new_role=old_role,
            reason=reason,
            extra_info=f"{days} days"
        )
        db.session.commit()

        create_platform_message(
            sender_id=current_user.id,
            receiver_id=user.id,
            title="Account Suspended",
            body=f"Your account has been suspended for {days} days. Reason: {reason}",
            msg_type="system_suspend",
            popup=True
        )

        return jsonify({"status": "suspended"})

    # ============================================================
    # LIFT SUSPENSION
    # ============================================================
    if change_type == "lift":
        user.suspended_until = None
        user.suspension_reason = None
        db.session.commit()

        log_role_change(
            user_id=user.id,
            admin_id=current_user.id,
            change_type="lift",
            old_role=old_role,
            new_role=old_role,
            reason="Suspension lifted",
            extra_info=""
        )
        db.session.commit()

        create_platform_message(
            sender_id=current_user.id,
            receiver_id=user.id,
            title="Suspension Lifted",
            body="Your suspension has been lifted. You may now access your dashboard again.",
            msg_type="system_lift",
            popup=True
        )

        return jsonify({"status": "lifted"})

    # ============================================================
    # UPGRADE / DOWNGRADE
    # ============================================================
    if change_type in ["upgrade", "downgrade"]:

        # Clear suspension
        user.suspended_until = None
        user.suspension_reason = None

        # Apply selected role level
        # 1 = SuperAdmin
        # 2 = Admin
        # 3 = Driver
        # 4 = Enthusiast (Basic)

        if new_role == "Driver":
            user.level = 3

        elif new_role == "Enthusiast":
            user.level = 4

        elif new_role == "Admin":
            user.level = 2

        elif new_role == "Superadmin":
            user.level = 1


        # Set correct message + reason
        if change_type == "downgrade":
            user.role_change_message = f"Sorry, you have been downgraded to {new_role}."
            user.downgrade_reason = reason

            create_platform_message(
                sender_id=current_user.id,
                receiver_id=user.id,
                title="Account Downgraded",
                body=f"Your account has been downgraded to {new_role}. Reason: {reason}",
                msg_type="system_downgrade",
                popup=True
            )

        elif change_type == "upgrade":
            user.role_change_message = f"You have been upgraded to {new_role}."
            user.downgrade_reason = None

            create_platform_message(
                sender_id=current_user.id,
                receiver_id=user.id,
                title="Account Upgraded",
                body=f"Your account has been upgraded to {new_role}.",
                msg_type="system_upgrade",
                popup=True
            )

        db.session.commit()

        log_role_change(
            user_id=user.id,
            admin_id=current_user.id,
            change_type=change_type,
            old_role=old_role,
            new_role=new_role,
            reason=reason,
            extra_info=None
        )
        db.session.commit()

        return jsonify({"status": "role_changed"})

    return jsonify({"error": "Invalid change type"})


@app.route('/create_user_by_email', methods=['POST'])
@login_required
def create_user_by_email():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({"error": "Email required"}), 400

    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({"error": "User already exists"}), 400

    new_user = User(
        email=email,
        level=4   # Enthusiast (Basic)
    )


    db.session.add(new_user)
    db.session.commit()

    return jsonify({"success": True})

# =========================================================
#  LOG ROLE CHANGE (HELPER FUNCTIONS)
# =========================================================
def get_role_string(user):
    if user.level == 1: 
        return "Superadmin"
    if user.level == 2: 
        return "Admin"
    if user.level == 3: 
        return "Driver"
    if user.level == 4: 
        return "Enthusiast"
    return "None"

def log_role_change(user_id, admin_id, change_type, old_role, new_role, reason, extra_info=None):
    entry = RoleChangeHistory(
        user_id=user_id,
        admin_id=admin_id,
        change_type=change_type,
        old_role=old_role,
        new_role=new_role,
        reason=reason,
        extra_info=extra_info
    )
    db.session.add(entry)
    db.session.commit()

# =========================================================
# ROUTES
# =========================================================
@app.route('/apply_role_change', methods=['POST'])
def apply_role_change():
    user_id = request.form.get('user_id')
    change_type = request.form.get('change_type')
    selected_role = request.form.get('selected_role')
    reason = request.form.get('reason')
    extra_info = request.form.get('extra_info')

    user = db.session.get(User, int(user_id))

    old_role = get_role_string(user)

    # Apply selected role level
    # 1 = SuperAdmin
    # 2 = Admin
    # 3 = Driver
    # 4 = Enthusiast (Basic)

    if selected_role == "Superadmin":
        user.level = 1
    elif selected_role == "Admin":
        user.level = 2
    elif selected_role == "Driver":
        user.level = 3
    elif selected_role == "Enthusiast":
        user.level = 4

    db.session.commit()

    # Log the change
    log_role_change(
        user_id=user.id,
        admin_id=current_user.id,
        change_type=change_type,
        old_role=old_role,
        new_role=selected_role,
        reason=reason,
        extra_info=extra_info
    )

    return jsonify({"status": "success"})
    
# =========================================================
# ROLE CHANGE LOGS
# =========================================================
@app.route("/role/change/history")
@login_required
def role_change_history():
    logs = RoleChangeHistory.query.order_by(RoleChangeHistory.timestamp.desc()).all()

    return render_template(
        "role_change_history.html",
        logs=logs
    )

# ============================================================
# 1. SYSTEM SETTINGS (Admin Global Settings)
# ============================================================
@app.route("/settings/system")
@login_required
def settings_system():
    if not current_user.role_superadmin:
        return redirect("/dashboard")
        
    settings_row = SystemSettings.query.first()
    if not settings_row:
        settings_row = SystemSettings()
        db.session.add(settings_row)
        db.session.commit()
        
    return render_template("system_settings.html", settings=settings_row)


@app.route("/settings/system/save", methods=["POST"])
@login_required
def settings_system_save():
    if not current_user.role_superadmin:
        return redirect("/dashboard")

    settings_row = SystemSettings.query.first()
    if not settings_row:
        settings_row = SystemSettings()
        db.session.add(settings_row)

    session_timeout_val = request.form.get("session_timeout")
    settings_row.session_timeout = int(session_timeout_val) if session_timeout_val else 30

    suspension_val = request.form.get("default_suspension_length")
    settings_row.default_suspension_length = int(suspension_val) if suspension_val else 7

    retention_val = request.form.get("log_retention")
    settings_row.log_retention = int(retention_val) if retention_val else 90

    settings_row.superadmin_protection = request.form.get("superadmin_protection") is not None
    settings_row.module_takings = request.form.get("module_takings") is not None
    settings_row.module_incidents = request.form.get("module_incidents") is not None
    settings_row.module_logs = request.form.get("module_logs") is not None
    settings_row.module_user_management = request.form.get("module_user_management") is not None
    settings_row.module_company_management = request.form.get("module_company_management") is not None
    settings_row.module_outstations = request.form.get("module_outstations") is not None
    settings_row.module_regions = request.form.get("module_regions") is not None

    settings_row.notify_email = request.form.get("notify_email") is not None
    settings_row.notify_suspension = request.form.get("notify_suspension") is not None
    settings_row.notify_incident = request.form.get("notify_incident") is not None
    settings_row.notify_admin_action = request.form.get("notify_admin_action") is not None

    settings_row.default_company = request.form.get("default_company")
    settings_row.default_region = request.form.get("default_region")
    settings_row.default_depot = request.form.get("default_depot")
    settings_row.theme = request.form.get("theme")

    db.session.commit()
    return redirect("/settings/system")

# ============================================================
# 2. USER SETTINGS (Individual Profile & Preferences)
# ============================================================
@app.route("/settings", methods=["GET", "POST"])
def settings():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    user = User.query.get(user_id)
    if not user:
        return redirect("/login")

    user_settings = Settings.query.filter_by(user_id=user.id).first()
    if not user_settings:
        user_settings = Settings(
            user_id=user.id, 
            username=getattr(user, "username", None) or "User",
            week_start_day="Monday"
        )
        db.session.add(user_settings)
        db.session.commit()

    if request.method == "POST":
        user_settings.year_mode = request.form.get("year_mode", user_settings.year_mode)
        user_settings.tax_year_preset = request.form.get("tax_year_preset", user_settings.tax_year_preset)
        user_settings.custom_tax_year_start = request.form.get("custom_tax_year_start", user_settings.custom_tax_year_start)
        user_settings.week_start_day = request.form.get("week_start_day", user_settings.week_start_day)
        
        db.session.commit()
        flash("Settings updated successfully!", "success")
        return redirect(url_for("settings"))

    # Safe queries for pay rates and special days
    pay_rates = []
    if 'PayRate' in globals():
        if hasattr(PayRate, 'user_id'):
            pay_rates = PayRate.query.filter_by(user_id=user.id).all()
        else:
            pay_rates = PayRate.query.all()
    
    special_days = []
    if 'SpecialDay' in globals():
        if hasattr(SpecialDay, 'user_id'):
            special_days = SpecialDay.query.filter_by(user_id=user.id).all()
        else:
            special_days = SpecialDay.query.all()
        
    return_to = request.referrer or url_for("home")
    
    return render_template(
        "settings.html",
        user=user,
        settings=user_settings,
        pay_rates=pay_rates,
        special_days=special_days,
        return_to=return_to
    )

# ============================================================
# SUPERADMIN CHAT SYSTEM (FIXED & SAFE)
# ============================================================
@app.route("/superadmin/messages/<target>")
@login_required
def superadmin_messages(target):

    # Only SuperAdmin allowed
    if current_user.level != 1:
        return "Unauthorized", 403

    # Identify Admin + Superadmin safely
    admin = User.query.filter_by(level=2).first()
    superadmin = User.query.filter_by(level=1).first()

    # Fallback if admin or superadmin row is missing to prevent 500 errors
    if not superadmin:
        superadmin = current_user

    # ============================
    # CATEGORY MODE — MERGED INBOX
    # ============================
    if target in ["user", "enthusiast", "driver", "admin", "all"]:

        # Build user list based on category (Using Level 3 for Drivers)
        if target == "user":
            users = User.query.filter(User.level != 1).all()

        elif target == "enthusiast":
            users = User.query.filter_by(level=4).all()

        elif target == "driver":
            users = User.query.filter_by(level=3).all()

        elif target == "admin":
            users = User.query.filter_by(level=2).all()

        elif target == "all":
            users = User.query.filter(User.level != 1).all()
        else:
            users = []

        # Load messages safely if admin exists
        if admin:
            messages = UserMessages.query.filter(
                (
                    (UserMessages.sender_id == admin.id) &
                    (UserMessages.receiver_id == superadmin.id)
                )
                |
                (
                    (UserMessages.sender_id == superadmin.id) &
                    (UserMessages.receiver_id == admin.id)
                )
            ).order_by(UserMessages.created_at.asc()).all()

            # Mark messages as read for both
            for m in messages:
                if m.receiver_id in [admin.id, superadmin.id] and not m.read:
                    m.read = True

            db.session.commit()
        else:
            messages = []

        return render_template(
            "superadmin_messages.html",
            users=users,
            messages=messages,
            user=None,
            target=target,            
            admin=admin,
            superadmin=superadmin
        )
    
    return "Invalid target category", 404


    # ============================
    # PRIVATE CHAT MODE
    # ============================
    try:
        user_id = int(target)
    except ValueError:
        return "Invalid target", 400

    if user_id == current_user.id:
        return "Cannot chat to yourself", 400

    user = User.query.get(user_id)
    if not user:
        return "User not found", 404

    if user.level == 1:
        return "Unauthorized", 403

    # ⭐ Load ALL messages between THIS USER and Admin+Superadmin
    messages = UserMessages.query.filter(
        (
            # User → Admin or User → Superadmin
            (UserMessages.sender_id == user.id) &
            (UserMessages.receiver_id.in_([admin.id, superadmin.id]))
        )
        |
        (
            # Admin → User or Superadmin → User
            (UserMessages.sender_id.in_([admin.id, superadmin.id])) &
            (UserMessages.receiver_id == user.id)
        )
    ).order_by(UserMessages.created_at.asc()).all()

    # ⭐ Mark messages as read for BOTH Admin + Superadmin
    for m in messages:
        if m.receiver_id in [admin.id, superadmin.id] and not m.read:
            m.read = True

    db.session.commit()

    # Dropdown list
    users = User.query.filter(
        User.id != current_user.id,
        User.level != 1
    ).order_by(User.first_name.asc()).all()

    return render_template(
        "superadmin_messages.html",
        users=users,
        messages=messages,
        user=user,
        target=target,
        admin=admin,
        superadmin=superadmin     
    )



# ============================================================
# ADMIN CHAT SYSTEM (OLD)
# ============================================================

@app.route("/admin/chat/<int:user_id>")
@login_required
def admin_chat(user_id):

    # Admin OR Superadmin allowed
    if current_user.level not in [1, 2]:
        return "Unauthorized", 403

    # Prevent chatting with yourself
    if user_id == current_user.id:
        return redirect("/admin/messages/users")

    # Load the target user
    user = User.query.get(user_id)
    if not user:
        return "User not found", 404

    # Admin cannot chat with other Admins
    if current_user.level == 2 and user.level == 2:
        return redirect("/admin/messages/users")

    # Identify Admin + Superadmin
    admin = User.query.filter_by(level=2).first()
    superadmin = User.query.filter_by(level=1).first()

    # Build dropdown list
    if current_user.level == 1:  # Superadmin
        users = User.query.filter(
            User.id != current_user.id,
            User.level.in_([2, 3, 4])   # Admin + Drivers + Enthusiasts
        ).order_by(User.first_name.asc()).all()

    elif current_user.level == 2:  # Admin
        users = User.query.filter(
            User.id != current_user.id,
            User.level.in_([1, 3, 4])   # Superadmin + Drivers + Enthusiasts
        ).order_by(User.first_name.asc()).all()

    # ⭐ Load ALL messages between THIS USER and Admin+Superadmin
    messages = UserMessages.query.filter(
        (
            # User → Admin or User → Superadmin
            (UserMessages.sender_id == user.id) &
            (UserMessages.receiver_id.in_([admin.id, superadmin.id]))
        )
        |
        (
            # Admin → User or Superadmin → User
            (UserMessages.sender_id.in_([admin.id, superadmin.id])) &
            (UserMessages.receiver_id == user.id)
        )
    ).order_by(UserMessages.created_at.asc()).all()

    # ⭐ Mark messages as read for BOTH Admin + Superadmin
    for m in messages:
        if m.receiver_id in [admin.id, superadmin.id] and not m.read:
            m.read = True

    db.session.commit()

    return render_template(
        "admin_chat.html",
        users=users,
        messages=messages,
        user=user
    )



# ============================================================
# ADMIN CHAT SYSTEM (NEW)
# ============================================================
@app.route("/admin/messages/<target>")
@login_required
def admin_messages(target):

    # Admin OR Superadmin allowed
    if current_user.level not in [1, 2]:
        return "Unauthorized", 403

    # Identify Admin + Superadmin
    admin = User.query.filter_by(level=2).first()
    superadmin = User.query.filter_by(level=1).first()

    # ============================
    # USERS MODE — SHOW DRIVER + ENTHUSIAST
    # ============================
    if target == "users":
        list_to_show = User.query.filter(
            User.id != current_user.id,
            User.level.in_([3, 4])  # Drivers + Enthusiasts
        ).order_by(User.first_name.asc()).all()

        user = None
        messages = []

    # ============================
    # SUPERADMIN MODE — MERGED INBOX
    # ============================
    elif target == "superadmin":

        # Superadmin user object
        user = superadmin

        # Show only Superadmin in the list
        list_to_show = [superadmin]

        # ⭐ Load ALL messages addressed TO or FROM Admin OR Superadmin
        messages = UserMessages.query.filter(
            (
                # Messages sent TO Admin or Superadmin
                UserMessages.receiver_id.in_([admin.id, superadmin.id])
            ) |
            (
                # Messages sent FROM Admin or Superadmin
                UserMessages.sender_id.in_([admin.id, superadmin.id])
            )
        ).order_by(UserMessages.created_at.asc()).all()

        # ⭐ Mark messages as read for BOTH Admin + Superadmin
        for m in messages:
            if m.receiver_id in [admin.id, superadmin.id] and not m.read:
                m.read = True

        db.session.commit()


    # ============================
    # USER ID MODE — PRIVATE CHAT
    # ============================
    else:
        try:
            user_id = int(target)
        except ValueError:
            return "Invalid target", 400

        user = User.query.get(user_id)
        if not user:
            return "User not found", 404

        # build dropdown list (drivers + enthusiasts)
        list_to_show = User.query.filter(
            User.id != current_user.id,
            User.level.in_([3, 4])
        ).order_by(User.first_name.asc()).all()

        # ⭐ Load ALL messages between THIS USER and Admin+Superadmin
        messages = UserMessages.query.filter(
            (
                # User → Admin or User → Superadmin
                (UserMessages.sender_id == user.id) &
                (UserMessages.receiver_id.in_([admin.id, superadmin.id]))
            )
            |
            (
                # Admin → User or Superadmin → User
                (UserMessages.sender_id.in_([admin.id, superadmin.id])) &
                (UserMessages.receiver_id == user.id)
            )
        ).order_by(UserMessages.created_at.asc()).all()

        # ⭐ Mark messages as read for BOTH Admin + Superadmin
        for m in messages:
            if m.receiver_id in [admin.id, superadmin.id] and not m.read:
                m.read = True
        db.session.commit()

    return render_template(
        "admin_messages.html",
        users=list_to_show,
        user=user,
        messages=messages,
        target=target,
        admin=admin,
        superadmin=superadmin
    )



# ==============================================
# ADMIN CHAT
# ==============================================
@app.route("/admin/chat")
@login_required
def admin_chat_default():

    if current_user.level not in [1, 2]:
        return "Unauthorized", 403

    # Admin default view = go to selection page
    return redirect("/admin/messages/users")

# ===============================================
# SEND CHAT MESSAGES POST ROUTE
# ===============================================

@app.route("/send_chat_message", methods=["POST"])
@login_required
def send_chat_message():
    data = request.get_json()
    body = data.get("body")
    title = data.get("title", "Chat Message")

    # ------------------------------
    # DETERMINE RECEIVER
    # ------------------------------
    if current_user.level in [3, 4]:  
        # USER sending a message → ALWAYS send to Admin
        admin_user = User.query.filter_by(level=2).first()
        receiver_id = admin_user.id

    else:
        # ADMIN or SUPERADMIN → use provided receiver_id
        receiver_id = data.get("receiver_id")

    # ------------------------------
    # CREATE MESSAGE
    # ------------------------------
    msg = UserMessages(
        sender_id=current_user.id,
        receiver_id=receiver_id,
        title=title,
        body=body,
        type="reply"
    )

    db.session.add(msg)
    db.session.commit()

    return {"success": True}


# ==========================================
# MESSAGES ROUTE
# ==========================================

@app.route("/messages")
@login_required
def messages():

    if current_user.level == 1:
        return redirect("/superadmin/messages/user")

    if current_user.level == 2:
        return redirect("/admin/chat")

    return redirect("/platform/messages")

# ============================================================
# PLATFORM MESSAGES (for drivers & enthusiasts)
# ============================================================

@app.route("/platform/messages")
@login_required
def platform_messages_view():
    # Only non-admin, non-superadmin users allowed
    if current_user.level in [1, 2]:
        return "Unauthorized", 403

    # Load all messages for this user
    messages = UserMessages.query.filter(
        (UserMessages.sender_id == current_user.id) |
        (UserMessages.receiver_id == current_user.id)
    ).order_by(UserMessages.created_at.asc()).all()

    # Count unread messages
    unread_count = UserMessages.query.filter_by(
        receiver_id=current_user.id,
        read=False
    ).count()

    return render_template(
        "platform_messages.html",
        messages=messages,
        unread_count=unread_count,
        user=current_user,
        users=[],
        target="platform"
    )

# ---------------------------------------------------------
# REGISTER (UNIFIED DRIVER + ENTHUSIAST)
# ---------------------------------------------------------
@app.post("/create_profile")
def create_profile():
    # BASIC DETAILS
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    date_of_birth = request.form.get("date_of_birth", "")

    # MAIN ROLE LEVEL (Driver = 3, Enthusiast = 4)
    level_value = request.form.get("level")

    # EXTRA ACCESS ROLES (checkboxes)
    role_driver = request.form.get("role_driver") == "1"
    role_enthusiast = request.form.get("role_enthusiast") == "1"

    # AGE CHECK
    try:
        dob_date = datetime.strptime(date_of_birth, "%Y-%m-%d")
    except ValueError:
        return "Invalid date format", 400

    today = datetime.today()
    age = today.year - dob_date.year - (
        (today.month, today.day) < (dob_date.month, dob_date.day)
    )

    # MAIN ROLE AGE CHECK
    if level_value == "3" and age < 18:
        return "You must be 18+ to register as a driver", 400
    if level_value == "4" and age < 16:
        return "You must be 16+ to register as an enthusiast", 400

    # EXTRA ACCESS AGE CHECKS
    if role_driver and age < 18:
        return "You must be 18+ to have driver access", 400
    if role_enthusiast and age < 16:
        return "You must be 16+ to have enthusiast access", 400

    # CREATE USER
    hashed_password = generate_password_hash(password)

    user = User(
        first_name=first_name,
        last_name=last_name,
        username=username if username else None,
        email=email,
        password=hashed_password,
        date_of_birth=date_of_birth,
        level=int(level_value) if level_value else 4,
        role_driver=role_driver,
        role_enthusiast=role_enthusiast,
        joined_date=datetime.today().strftime("%Y-%m-%d")
    )

    db.session.add(user)
    db.session.commit()

    return redirect("/profile")


# =====================================================================
# CREATE PROFILE GET ROUTE
# =====================================================================
@app.get("/create_account")
def show_create_account():
    return render_template("create_account.html")

# =====================================================================
# DELETE  OPTIONS
# =====================================================================
@app.route("/delete-account-options")
def delete_account_options():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    user = db.session.get(User, int(user_id))
    enthusiast = Enthusiast.query.filter_by(username=user.username).first()

    return render_template(
        "delete_options.html",
        current_user=user,
        enthusiast=enthusiast
    )

# ---------------------------------------------------------
# SUSPENDED PAGE ROUTE
# ---------------------------------------------------------
@app.route("/suspended")
@login_required
def suspended():
    return render_template("suspended.html")

# =====================================================================
# DELETE ACCOUNTS
# =====================================================================
@app.route("/confirm-delete", methods=["POST"])
def confirm_delete():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    user = db.session.get(User, int(user_id))
    if not user:
        return redirect("/login")

    delete_driver = "delete_driver" in request.form
    delete_enthusiast = "delete_enthusiast" in request.form
    delete_both = "delete_both" in request.form  # Matches name="delete_both" in your template

    # ⭐ FULL ACCOUNT DELETE (Matches your HTML checkbox)
    if delete_both:
        db.session.delete(user)
        db.session.commit()
        session.clear()
        return redirect("/goodbye")

    # ⭐ DELETE DRIVER ROLE ONLY
    if delete_driver:
        user.level = 4  # downgrade to enthusiast
        db.session.commit()
        return redirect("/profile")

    # ⭐ DELETE ENTHUSIAST ROLE ONLY
    if delete_enthusiast:
        user.level = 3  # downgrade to driver
        db.session.commit()
        return redirect("/profile")

    # If all roles are now zero → delete whole account
    if getattr(user, 'role_driver', 0) == 0 and getattr(user, 'role_enthusiast', 0) == 0:
        db.session.delete(user)
        db.session.commit()
        session.clear()
        return redirect("/goodbye")

    return redirect(url_for("home"))

@app.route("/goodbye")
def goodbye():
    return render_template(
        "goodbye.html",
        home_target="home",
        is_drivers_hub=False,
        page_title="Account Deleted"
    )
   
# ---------------------------------------------------------
# UNIFIED LOGIN (DRIVER + ENTHUSIAST + MANDATORY 2FA)
# ---------------------------------------------------------
@app.post("/api/login")
def unified_login():
    # Support both JSON requests and standard form submissions
    if request.is_json:
        data = request.get_json() or {}
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
    else:
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

    user = User.query.filter_by(email=email).first()

    if not user:
        return jsonify({"message": "Account not found"}), 400

    if not check_password_hash(user.password, password):
        return jsonify({"message": "Incorrect password"}), 400
    
    # 🛡️ MANDATORY 2FA ENFORCEMENT CHECK
    if not user.is_2fa_enabled or not user.totp_secret:
        session["pre_2fa_user_id"] = user.id
        login_user(user)  # Log them in briefly so @login_required allows them to view /setup_2fa
        return jsonify({
            "message": "Please set up Two-Factor Authentication",
            "redirect": "/setup_2fa"
        }), 200

    # If 2FA is active, hold them here and send them to the code entry screen
    session["pre_2fa_user_id"] = user.id
    return jsonify({
        "message": "2FA verification required",
        "redirect": "/verify_2fa"
    }), 200
    
# ---------------------------------------------------------
# SETUP 2FA LOGIN 
# ---------------------------------------------------------
@app.route("/setup_2fa", methods=["GET", "POST"])
@login_required
def setup_2fa():
    # ⚠️ REMOVED: if not is_superadmin_user(current_user): 
    # Now ALL users can access setup_2fa so regular users can enable their Authenticator apps!

    if request.method == "POST":
        token = request.form.get("token")
        
        # Verify token using the stored secret with time window tolerance
        totp = pyotp.TOTP(current_user.totp_secret)
        if totp.verify(token, valid_window=1):
            current_user.is_2fa_enabled = True
            db.session.commit()
            flash("Two-Factor Authentication enabled successfully!", "success")
            
            # Safe dashboard redirect based on role
            if current_user.role_driver:
                return redirect(url_for("driver_dashboard"))
            else:
                return redirect(url_for("enthusiast_dashboard"))
        else:
            flash("Invalid 2FA code. Please try again.", "danger")

    # Generate a new secret if the user doesn't have one yet
    if not current_user.totp_secret:
        current_user.totp_secret = pyotp.random_base32()
        db.session.commit()

    # Create the provisioning URI for the authenticator app
    totp = pyotp.TOTP(current_user.totp_secret)
    provisioning_uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="TransportHub.uk"
    )

    # Generate QR code image in-memory
    img = qrcode.make(provisioning_uri)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    qr_code_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

    return render_template("setup_2fa.html", qr_code=qr_code_base64, secret=current_user.totp_secret)
    
# ---------------------------------------------------------
# VERIFY 2FA LOGIN 
# --------------------------------------------------------- 
@app.route("/verify_2fa", methods=["GET", "POST"])
def verify_2fa():
    user_id = session.get("pre_2fa_user_id")
    if not user_id:
        # Only flash if they submitted a form or came from login, 
        # or remove the flash line completely to stop the message from appearing
        if request.method == "POST":
            flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))
        
    user = db.session.get(User, user_id)
    
    if request.method == "POST":
        token = request.form.get("token")
        totp = pyotp.TOTP(user.totp_secret)
        
        if totp.verify(token, valid_window=1):
            session.pop("pre_2fa_user_id", None)
            
            # --- FIX: Set the session key your dashboards look for! ---
            session["user_id"] = user.id
            
            login_user(user)
            flash("Logged in successfully!", "success")
            
            # Safe dashboard redirect based on role
            if user.role_driver:
                return redirect(url_for("driver_dashboard"))
            else:
                return redirect(url_for("enthusiast_dashboard"))
        else:
            flash("Invalid 2FA code. Please try again.", "danger")
            
    return render_template("verify_2fa.html")
    
# ---------------------------------------------------------
# PASSWORD RESET (EMAIL + TOKEN)
# ---------------------------------------------------------
@app.post("/api/password_reset_request")
def password_reset_request():
    data = request.json or {}
    email = data.get("email", "").strip().lower()

    # Check if email exists (safe generic response)
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"message": "If this email exists, a reset link will be sent."})

    # Generate secure token + expiry
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(minutes=30)

    # Store token entry
    reset_entry = PasswordResetToken(
        email=email,
        token=token,
        expires_at=expires
    )
    db.session.add(reset_entry)
    db.session.commit()

    # Your REAL domain reset link
    reset_link = f"https://transporthub.uk/reset_password/{token}"

    # Send email from info@transporthub.uk
    msg = MailMessage(
        subject="Password Reset Request",
        sender="info@transporthub.uk",
        recipients=[email]
    )

    msg.html = render_template(
        "emails/password_reset_email.html",
        reset_link=reset_link,
        expires_minutes=30
    )

    mail.send(msg)

    return jsonify({"message": "If this email exists, a reset link has been sent."})



# ==========================================
# PASSWORD RESET TOKEN
# ==========================================
@app.post("/api/password_reset/<token>")
def password_reset(token):
    data = request.json or {}
    new_password = data.get("password", "").strip()

    # Require a password
    if not new_password:
        return jsonify({"message": "Password is required."}), 400

    # Look up token
    reset_entry = PasswordResetToken.query.filter_by(token=token).first()
    if not reset_entry:
        return jsonify({"message": "Invalid or expired reset link."}), 400

    # Check expiry
    if reset_entry.expires_at < datetime.utcnow():
        db.session.delete(reset_entry)
        db.session.commit()
        return jsonify({"message": "Reset link has expired."}), 400

    # Hash new password
    hashed_password = generate_password_hash(new_password)

    # Update all accounts with this email
    users = User.query.filter_by(email=reset_entry.email).all()
    for user in users:
        user.password = hashed_password

    # Remove token after use
    db.session.delete(reset_entry)
    db.session.commit()

    return jsonify({"message": "Password updated successfully."})



# ---------------------------------------------------------
# LOGIN CODE (EMAIL BACKUP LOGIN)
# ---------------------------------------------------------

@app.post("/api/request_login_code")
def request_login_code():
    data = request.json or {}
    email = data.get("email", "").strip().lower()

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"message": "If this email exists, a code will be sent."})

    code = f"{secrets.randbelow(10000):04d}"
    expires = datetime.utcnow() + timedelta(minutes=5)

    login_code = LoginCode(email=email, code=code, expires_at=expires)
    db.session.add(login_code)
    db.session.commit()

    msg = MailMessage("Your 4‑Digit Login Code", recipients=[email])
    msg.html = render_template(
        "emails/login_code_email.html",
        code=code,
        expires_minutes=5
    )
    mail.send(msg)

    return jsonify({"message": "A login code has been sent to your email."})

# ==============================================
# VERIFY LOGIN CODE
# ==============================================

@app.post("/api/verify_login_code")
def verify_login_code():
    data = request.json or {}
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()

    entry = LoginCode.query.filter_by(email=email, code=code).first()
    if not entry:
        return jsonify({"message": "Invalid code"}), 400

    if entry.expires_at < datetime.utcnow():
        db.session.delete(entry)
        db.session.commit()
        return jsonify({"message": "Code expired"}), 400

    db.session.delete(entry)
    db.session.commit()

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"message": "Account not found"}), 400

    session["email"] = user.email
    session["user_id"] = user.id
    session["company_id"] = _compute_company_id_for_user(user)

    return jsonify({
        "message": "Login success",
        "driver": (user.level == 3),
        "enthusiast": (user.level == 4)
    }), 200



# ---------------------------------------------------------
# HTML PAGES (LOGIN + CREATE ACCOUNT)
# ---------------------------------------------------------

@app.route("/create_account")
def create_account_page():
    return render_template("create_account.html")


@app.route("/login")
def login():
    return render_template("login.html")


# ---------------------------------------------------------
# CHOOSE WEEK START PAGE (ADD THIS HERE)
# ---------------------------------------------------------

@app.route("/choose-week-start")
def choose_week_start():
    return render_template("choose_week_start.html")

    
# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")

# =========================================================
# SET WEEK START
# =========================================================
@app.post("/set-week-start")
def set_week_start():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    user = User.query.get(user_id)
    if not user or user.level not in [3, 4]:
        return redirect("/")

    selected_day = request.form.get("week_start")
    if not selected_day:
        return "Invalid week start", 400

    # Get company record
    company = Company.query.filter_by(name=user.company).first()

    # CASE 1 — Company has no week start yet → first driver sets it
    if company and company.week_start_day is None:
        company.week_start_day = selected_day
        db.session.commit()

        session["week_start"] = selected_day
        return redirect("/driver_dashboard")

    # CASE 2 — Company already has a week start → driver override
    user.week_start_override = selected_day
    db.session.commit()

    session["week_start"] = selected_day
    return redirect("/driver_dashboard")



    # CASE 2 — Company already has a week start → driver override
    user.week_start_override = selected_day
    db.session.commit()

    # Update session
    session["week_start"] = selected_day

    return redirect("/driver_dashboard")

# ---------------------------------------------------------
# CURRENT USER INFO (USED BY DASHBOARDS)
# ---------------------------------------------------------
@app.get("/api/me")
def api_me():
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"logged_in": False}), 401

    user = db.session.get(User, int(user_id))
    if not user:
        return jsonify({"logged_in": False}), 401

    return jsonify({
        "logged_in": True,
        "first_name": user.first_name,
        "username": user.username
    })

# =======================================================
# BIRTHDAY HELPER
# =======================================================
def check_birthday(dob_field):
    if not dob_field:
        return False
    today = datetime.today().date()
    
    if isinstance(dob_field, str):
        try:
            dob = datetime.strptime(dob_field, "%Y-%m-%d").date()
        except ValueError:
            return False
    else:
        dob = dob_field
        
    return dob and dob.day == today.day and dob.month == today.month

# ---------------------------------------------------------
# DRIVER DASHBOARD ROUTE
# ---------------------------------------------------------
@app.route("/driver_dashboard")
@login_required
def driver_dashboard():
    user = current_user

    # Suspension check
    if user.suspended_until and user.suspended_until > datetime.utcnow():
        return redirect(url_for("suspended"))  # Use url_for for safety

    # Role check - Allow drivers, superadmins, and admins
    if not (getattr(user, "role_driver", False) or getattr(user, "role_superadmin", False) or getattr(user, "role_admin", False)):
        if getattr(user, "role_enthusiast", False):
            return redirect(url_for("enthusiast_dashboard"))
        return redirect(url_for("login"))

    # Admin welcome (upgrade)
    if user.role_admin and getattr(user, "show_admin_welcome", False):
        user.show_admin_welcome = False
        db.session.commit()
        return render_template("admin_welcome.html", user=user)

    # Admin removed (downgrade)
    if getattr(user, "show_admin_removed", False):
        user.show_admin_removed = False
        db.session.commit()
        return render_template("admin_removed.html", user=user)

    # Settings + session message
    settings = Settings.query.filter_by(user_id=user.id).first()
    msg = session.pop("msg", None)

    # Unread messages for THIS user only
    unread_count = UserMessages.query.filter(
        UserMessages.receiver_id == user.id,
        UserMessages.read == False
    ).count()
    
    # Birthday popup logic
    show_birthday = check_birthday(user.date_of_birth)
    
    # Seasonal logic
    # Allow manual override via URL parameter: /driver/dashboard?season=christmas
    forced_season = request.args.get('season')
    if forced_season in ['halloween', 'christmas', 'easter']:
        active_season = forced_season
    else:
        # Fall back to your automatic date checking function
        active_season = get_current_season()

    return render_template(
        "driver_dashboard.html",
        settings=settings,
        home_target="driver_dashboard",
        is_drivers_hub=True,
        username=user.username,
        user=user,
        msg=msg,
        role_superadmin=getattr(user, "role_superadmin", False),
        unread_count=unread_count,
        show_birthday=show_birthday,
        active_season=active_season
    )


# ---------------------------------------------------------
# ENTHUSIAST DASHBOARD ROUTE
# ---------------------------------------------------------
@app.route("/enthusiast_dashboard")
@login_required
def enthusiast_dashboard():
    user = current_user

    # Suspension check
    if user.suspended_until and user.suspended_until > datetime.utcnow():
        return redirect(url_for("suspended"))

    # Only redirect to driver dashboard if they are a driver AND DO NOT have the enthusiast role
    if getattr(user, "level", None) == 3 and not getattr(user, "role_enthusiast", False):
        return redirect(url_for("driver_dashboard"))

    # Show admin welcome (upgrade)
    if getattr(user, "level", None) == 2 and getattr(user, "show_admin_welcome", False):
        user.show_admin_welcome = False
        db.session.commit()
        return render_template("admin_welcome.html", user=user)

    # Show admin removed (downgrade)
    if getattr(user, "show_admin_removed", False):
        user.show_admin_removed = False
        db.session.commit()
        return render_template("admin_removed.html", user=user)

    msg = session.pop("msg", None)
    
    unread_count = UserMessages.query.filter(
        UserMessages.receiver_id == user.id,
        UserMessages.read == False
    ).count()

    # Birthday popup logic
    show_birthday = check_birthday(user.date_of_birth)
    
    # Seasonal logic
    # Allow manual override via URL parameter: /driver/dashboard?season=christmas
    forced_season = request.args.get('season')
    if forced_season in ['halloween', 'christmas', 'easter']:
        active_season = forced_season
    else:
        # Fall back to your automatic date checking function
        active_season = get_current_season()

    return render_template(
        "enthusiast_dashboard.html",
        home_target="enthusiast_dashboard",
        is_transport_hub=True,
        page_title="Transport Hub",
        user=user,
        msg=msg,
        unread_count=unread_count,
        show_birthday=show_birthday,
        active_season=active_season        
    )

# ---------------------------------------------------------
# SEASONAL GALLERY
# ---------------------------------------------------------
@app.route('/seasonal-gallery')
def seasonal_gallery():
    active_season = get_current_season()
    return render_template('seasonal_gallery.html', active_season=active_season)

def get_easter_date(year):
    """Calculates Easter Sunday for any given year using Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)

def get_current_season():
    today = date.today()
    year = today.year
    
    # Halloween: October 25th to October 31st
    if date(year, 10, 25) <= today <= date(year, 10, 31):
        return "halloween"

    # Christmas: December 20th to December 31st
    if date(year, 12, 20) <= today <= date(year, 12, 31):
        return "christmas"

    # Easter: Dynamic 2-week window (1 week before Easter Sunday up to Easter Monday/7 days after)
    easter_sunday = get_easter_date(year)
    easter_start = date.fromordinal(easter_sunday.toordinal() - 7)  
    easter_end = date.fromordinal(easter_sunday.toordinal() + 7)
    
    if easter_start <= today <= easter_end:
        return "easter"

    return None
# ---------------------------------------------------------
# WEBSITE ROUTES (THEME ENABLED)
# ---------------------------------------------------------

@app.route("/")
def home():
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template(
        "index.html",
        settings=settings,
        home_target="home",
        is_drivers_hub=False
    )

@app.route("/drivers-hub")
def drivers_hub():
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template(
        "bus_coach_drivers_hub.html",
        settings=settings,
        home_target="driver_dashboard",
        is_drivers_hub=True
    )
    
@app.route("/hub")
def hub():
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()

    if settings and settings.account_type == "enthusiast":
        return redirect("/enthusiast-hub")
    else:
        return redirect("/drivers-hub")
    

@app.route("/enthusiast/create")
def enthusiast_create():
    return render_template("enthusiast_create.html")
    
@app.route("/enthusiast/login")
def enthusiast_login():
    return render_template("enthusiast_login.html")

   
@app.route("/enthusiast-hub")
def enthusiast_hub():
    enthusiasts = Enthusiast.query.order_by(Enthusiast.created_at.desc()).all()
    return render_template(
        "enthusiast_hub.html",
        enthusiasts=enthusiasts,
        home_target="enthusiast_dashboard",         # PUBLIC → always home
        is_drivers_hub=False                       # PUBLIC → never driver hub
    )



@app.route("/forgot_password")
def forgot_password():
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template(
        "forgot_password.html",
        settings=settings,
        home_target="home",
        is_drivers_hub=False
    )

@app.route("/reset_password/<token>")
def reset_password_page(token):
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template(
        "reset_password.html",
        token=token,
        settings=settings,
        home_target="home",
        is_drivers_hub=False
    )

@app.route("/enter_code/<email>")
def enter_code(email):
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template(
        "enter_code.html",
        email=email,
        settings=settings,
        home_target="home",
        is_drivers_hub=False
    )
# ====================================================
#       ENTHUSIAST ADD ENTRY
# ====================================================
@app.route("/enthusiast/add", methods=["GET", "POST"])
def add_enthusiast_record():
    if request.method == "POST":
        user_id = session.get("user_id")
        user = db.session.get(User, int(user_id))

        # -----------------------------
        # FIX: CONVERT SPOTTED_AT
        # -----------------------------
        spotted_at_raw = request.form.get("spotted_at")

        if spotted_at_raw and spotted_at_raw.strip():
            spotted_at = datetime.fromisoformat(spotted_at_raw)
        else:
            spotted_at = None

        # -----------------------------
        # CREATE ENTRY
        # -----------------------------
        new_entry = Enthusiast(
            username=user.username,
            operator=request.form["operator"],
            vehicle_number=request.form["vehicle_number"],
            registration=request.form["registration"],
            make_model=request.form["make_model"],
            depot=request.form["depot"],
            route_number=request.form["route_number"],
            route_destination=request.form["route_destination"],
            notes=request.form.get("notes"),
            spotted_at=spotted_at,
            photos_uploaded=0   # temporary
        )
        db.session.add(new_entry)
        db.session.commit()     # new_entry now exists and has an ID


        # -----------------------------
        # SAFE PHOTO UPLOAD HANDLING
        # -----------------------------       
        photos = request.files.getlist("photos")

        if photos and photos[0].filename != "":
            for photo in photos:
                if photo.filename:
                    filename = secure_filename(photo.filename)
                    upload_path = os.path.join("static/enthusiast_photos", filename)
                    photo.save(upload_path)

                    # Save photo record to DB
                    photo_record = EnthusiastPhoto(
                        filename=filename,
                        enthusiast_id=new_entry.id
                    )
                    db.session.add(photo_record)

        db.session.commit()     # commit photo records


        # 3️⃣ NOW you can safely update the count
        new_entry.photos_uploaded = len(new_entry.photos)
        db.session.commit()


        return redirect(url_for("enthusiast_hub"))

    return render_template("add_enthusiast_record.html", home_target="enthusiast_dashboard")

# ---------------------------------------------------------
# ENTHUSIAST EDIT PAGE
# ---------------------------------------------------------
@app.route("/enthusiast/edit/<int:enthusiast_id>", methods=["GET", "POST"])
def edit_enthusiast_record(enthusiast_id):
    user_id = session.get("user_id")
    user = db.session.get(User, int(user_id))

    entry = Enthusiast.query.get_or_404(enthusiast_id)

    # -------------------------------------------------
    # SAFETY CHECK — only allow editing your own entry
    # -------------------------------------------------
    if entry.username != user.username:
        return redirect(url_for("enthusiast_hub"))

    if request.method == "POST":
        entry.operator = request.form["operator"]
        entry.vehicle_number = request.form["vehicle_number"]
        entry.registration = request.form["registration"]
        entry.make_model = request.form["make_model"]
        entry.depot = request.form["depot"]
        entry.route_number = request.form["route_number"]
        entry.route_destination = request.form["route_destination"]
        entry.notes = request.form.get("notes")

        spotted_at_raw = request.form.get("spotted_at")
        entry.spotted_at = datetime.fromisoformat(spotted_at_raw) if spotted_at_raw else None

        db.session.commit()

        # Redirect back to the photo detail page
        first_photo = entry.photos[0]
        return redirect(url_for("enthusiast_photo_details", photo_id=first_photo.id))

    return render_template("enthusiast_edit.html", entry=entry)


# ---------------------------------------------------------
# ENTHUSIAST DELETE
# ---------------------------------------------------------
@app.route("/enthusiast/delete/<int:enthusiast_id>")
def delete_enthusiast_record(enthusiast_id):
    user_id = session.get("user_id")
    user = db.session.get(User, int(user_id))

    entry = Enthusiast.query.get_or_404(enthusiast_id)

    # SAFETY CHECK — only delete your own entries
    if entry.username != user.username:
        return redirect(url_for("enthusiast_gallery"))
    
    # Delete photos
    for photo in entry.photos:
        try:
            os.remove(os.path.join("static/enthusiast_photos", photo.filename))
        except:
            pass
        db.session.delete(photo)

    db.session.delete(entry)
    db.session.commit()

    return redirect(url_for("enthusiast_gallery"))


# ---------------------------------------------------------
# ENTHUSIAST CLEANUP (DELETE ENTRIES WITH NO PHOTOS)
# ---------------------------------------------------------
@app.route("/enthusiast/cleanup")
def enthusiast_cleanup():
    user_id = session.get("user_id")
    user = db.session.get(User, int(user_id))

    # Get ONLY your own entries with no photos
    empty_entries = Enthusiast.query.filter_by(
        username=user.username,
        photos_uploaded=0
    ).all()

    for entry in empty_entries:
        db.session.delete(entry)

    db.session.commit()

    return redirect(url_for("enthusiast_hub"))



# ---------------------------------------------------------
# ENTHUSIAST GALLERY PAGE
# ---------------------------------------------------------
@app.route("/enthusiast/gallery")
def enthusiast_gallery():
    user_id = session.get("user_id")
    user = db.session.get(User, int(user_id))

    # -----------------------------
    # GET filter values
    # -----------------------------
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)
    selected_user = request.args.get("user", "me")
    date_type = request.args.get("date_type", "created")
    sort = request.args.get("sort", "newest")

    operator = request.args.get("operator")
    depot = request.args.get("depot")
    route = request.args.get("route")

    # -----------------------------
    # Base query: ALL photos
    # -----------------------------
    photos_query = EnthusiastPhoto.query.join(Enthusiast)

    # -----------------------------
    # USER FILTER
    # -----------------------------
    if selected_user == "me":
        photos_query = photos_query.filter(Enthusiast.username == user.username)
    elif selected_user == "all":
        pass
    else:
        photos_query = photos_query.filter(Enthusiast.username == selected_user)

    # -----------------------------
    # DATE FIELD SELECTION
    # -----------------------------
    if date_type == "created":
        date_field = Enthusiast.created_at
    else:
        date_field = Enthusiast.spotted_at

    # -----------------------------
    # OPERATOR FILTER
    # -----------------------------
    if operator:
        photos_query = photos_query.filter(Enthusiast.operator == operator)

    # -----------------------------
    # DEPOT FILTER
    # -----------------------------
    if depot:
        photos_query = photos_query.filter(Enthusiast.depot == depot)

    # -----------------------------
    # ROUTE FILTER
    # -----------------------------
    if route:
        photos_query = photos_query.filter(Enthusiast.route_number == route)

    # -----------------------------
    # MONTH FILTER
    # -----------------------------
    if month:
        photos_query = photos_query.filter(
            db.extract('month', date_field) == month
        )

    # -----------------------------
    # YEAR FILTER
    # -----------------------------
    if year:
        photos_query = photos_query.filter(
            db.extract('year', date_field) == year
        )

    # -----------------------------
    # SORT FILTER
    # -----------------------------
    if sort == "oldest":
        photos = photos_query.order_by(date_field.asc()).all()
    else:
        photos = photos_query.order_by(date_field.desc()).all()

    # -----------------------------
    # Build gallery items
    # -----------------------------
    gallery_items = []
    for p in photos:
        enthusiast = Enthusiast.query.get(p.enthusiast_id)
        gallery_items.append({
            "photo": p,
            "enthusiast": enthusiast
        })

    # -----------------------------
    # GLOBAL year list
    # -----------------------------
    year_rows = db.session.query(
        db.extract('year', EnthusiastPhoto.uploaded_at)
    ).distinct().order_by(
        db.extract('year', EnthusiastPhoto.uploaded_at)
    ).all()
    year_list = [y[0] for y in year_rows]

    # -----------------------------
    # Month names
    # -----------------------------
    month_list = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December")
    ]

    # -----------------------------
    # All users
    # -----------------------------
    user_list = User.query.order_by(User.username).all()

    # -----------------------------
    # Operator / Depot / Route lists
    # -----------------------------
    operator_list = sorted(set(
        o[0].strip() for o in db.session.query(Enthusiast.operator).distinct().all()
        if o[0]
    ))
    
    depot_list = sorted(set(
        d[0].strip() for d in db.session.query(Enthusiast.depot).distinct().all()
        if d[0]
    ))

    route_list = sorted(set(
        r[0].strip() for r in db.session.query(Enthusiast.route_number).distinct().all()
        if r[0]
    ))



    return render_template(
        "enthusiast_gallery.html",
        gallery_items=gallery_items,
        month_list=month_list,
        year_list=year_list,
        user_list=user_list,
        operator_list=operator_list,
        depot_list=depot_list,
        route_list=route_list
    )
   
# ---------------------------------------------------------
# ENTHUSIAST PHOTO DETAILS PAGE
# ---------------------------------------------------------
@app.route("/enthusiast/photo/<int:photo_id>")
def enthusiast_photo_details(photo_id):
    photo = EnthusiastPhoto.query.get_or_404(photo_id)
    enthusiast = Enthusiast.query.get(photo.enthusiast_id)

    # Get previous and next photos
    prev_photo = EnthusiastPhoto.query.filter(EnthusiastPhoto.id < photo_id).order_by(EnthusiastPhoto.id.desc()).first()
    next_photo = EnthusiastPhoto.query.filter(EnthusiastPhoto.id > photo_id).order_by(EnthusiastPhoto.id.asc()).first()

    return render_template(
        "photo_detail.html",
        photo=photo,
        enthusiast=enthusiast,
        prev_photo=prev_photo,
        next_photo=next_photo
    )

# ---------------------------------------------------------
# ENTHUSIAST ROUTES PAGE
# ---------------------------------------------------------

@app.route("/enthusiast/routes")
def enthusiast_routes():
    routes = Enthusiast.query.order_by(Enthusiast.route_number.asc()).all()
    return render_template("routes.html", routes=routes)

# ---------------------------------------------------------
# API ROUTES (NO THEME NEEDED)
# ---------------------------------------------------------

@app.route("/api/check_enthusiast_status")
def check_enthusiast_status():
    email = session.get("email")
    if not email:
        return jsonify({"has_enthusiast": False})

    enthusiast = User.query.filter_by(email=email, level=4).first()
    return jsonify({"has_enthusiast": enthusiast is not None})



# ---------------------------------------------------------
# CHAT ROUTE (THEME ENABLED)
# ---------------------------------------------------------

@app.route("/chat")
def chat():
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template("chat.html", settings=settings)


# ---------------------------------------------------------
# DEBUG ROUTE (NO THEME REQUIRED)
# ---------------------------------------------------------

@app.route("/debug_templates")
def debug_templates():
    import os
    return str(os.listdir(app.template_folder))

# ================================================
# SETTINGS
# ================================================    
@app.route("/settings", methods=["GET", "POST"])
def user_settings():
    user_id = session.get("user_id")

    if not user_id:
        flask_user = getattr(current_user, "id", None)
        if flask_user:
            user_id = flask_user

    if not user_id:
        return redirect("/login")

    try:
        user_id = int(user_id)
    except:
        session.clear()
        return redirect("/login")

    session["last_page"] = request.referrer or session.get("last_page")

    user = db.session.get(User, user_id)
    if not user:
        return redirect("/login")

    settings = Settings.query.filter_by(user_id=user_id).first()
    if settings is None:
        settings = Settings(
            user_id=user_id,
            username=user.username or "User"  # ⭐ Safe fallback
        )
        db.session.add(settings)
        db.session.commit()

    if request.method == "POST":
        if "year_mode" in request.form:
            settings.year_mode = request.form.get("year_mode")

        if "tax_year_preset" in request.form:
            settings.tax_year_preset = request.form.get("tax_year_preset")

        if "custom_tax_year_start" in request.form:
            settings.custom_tax_year_start = request.form.get("custom_tax_year_start")

        if "week_start_day" in request.form:
            new_week_start = request.form.get("week_start_day")
            settings.week_start_day = new_week_start

            if user.level == 3:
                company = Company.query.filter_by(name=user.company).first()
                if company:
                    company.week_start_day = new_week_start
            else:
                user.week_start_override = new_week_start

        db.session.commit()
        return redirect(url_for("user_settings"))

    # ⭐ Correctly un-indented so it runs on GET requests
    pay_rates = PayRate.query.order_by(PayRate.effective_from.asc().nulls_last()).all()
    special_days = SpecialDay.query.order_by(SpecialDay.date.asc()).all()

    return_to = session.get("last_page", url_for("home"))

    return render_template(
        "settings.html",
        settings=settings,
        user=user,
        return_to=return_to,
        pay_rates=pay_rates,
        special_days=special_days
    )
  

# ============================
# PAY RATE SETTINGS
# ============================
@app.route("/add_pay_rate", methods=["POST"])
def add_pay_rate():

    def clean_rate(value):
        if not value or value.strip() == "" or value.strip().upper() == "N/A":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    effective_from = datetime.strptime(request.form.get("effective_from"), "%Y-%m-%d").date()

    # Base rates
    mon_fri = clean_rate(request.form.get("mon_fri_rate"))
    sat = clean_rate(request.form.get("sat_rate"))
    sun = clean_rate(request.form.get("sun_rate"))
    bank_hol = clean_rate(request.form.get("bank_hol_rate"))

    # Premiums
    late_week = clean_rate(request.form.get("early_rate"))
    late_sat = clean_rate(request.form.get("late_rate"))
    late_sun = clean_rate(request.form.get("midlate_rate"))
    night = clean_rate(request.form.get("night_rate"))

    # Special days
    christmas = clean_rate(request.form.get("christmas_rate"))
    boxingday = clean_rate(request.form.get("boxingday_rate"))
    newyear = clean_rate(request.form.get("newyear_rate"))
    goodfriday = clean_rate(request.form.get("goodfriday_rate"))

    # Must have at least one rate
    all_rates = [
        mon_fri, sat, sun, bank_hol,
        late_week, late_sat, late_sun, night,
        christmas, boxingday, newyear, goodfriday
    ]

    if not any(all_rates):
        flash("You must enter at least one rate or N/A.")
        return redirect("/settings")

    new_rate = PayRate(
        effective_from = effective_from,

        mon_fri_rate = mon_fri,
        sat_rate = sat,
        sun_rate = sun,
        bank_hol_rate = bank_hol,

        late_week_rate = late_week,
        late_sat_rate = late_sat,
        late_sun_rate = late_sun,
        night_rate = night,

        christmas_rate = christmas,
        boxingday_rate = boxingday,
        newyear_rate = newyear,
        goodfriday_rate = goodfriday
    )

    db.session.add(new_rate)
    db.session.commit()

    flash("New pay rate band added.")
    return redirect("/settings")
# ----------------------------------------------
# DELETE EXISTING PAY RATE ROW BUTTON
# ----------------------------------------------
@app.route("/delete_pay_rate", methods=["POST"])
def delete_pay_rate():
    rate_id = request.form.get("rate_id")
    rate = PayRate.query.get(rate_id)

    if rate:
        db.session.delete(rate)
        db.session.commit()
        flash("Pay rate band deleted.")

    return redirect("/settings")


# ===============================================
# SPECIAL DAYS
# ===============================================
@app.route("/add_special_day", methods=["POST"])
def add_special_day():
    date = datetime.strptime(request.form.get("special_date"), "%Y-%m-%d").date()
    name = request.form.get("special_name")
    rate = float(request.form.get("special_rate"))

    new_day = SpecialDay(date=date, name=name, rate=rate)
    db.session.add(new_day)
    db.session.commit()

    return redirect("/settings")
# ----------------------------------------------
# DELETE SPECIAL DAY RATE ROW BUTTON
# ----------------------------------------------    
@app.route("/delete_special_day", methods=["POST"])
def delete_special_day():
    day_id = request.form.get("day_id")
    day = SpecialDay.query.get(day_id)

    if day:
        db.session.delete(day)
        db.session.commit()
        flash("Special day deleted.")

    return redirect("/settings")

# ===================================================
# LEGAL SECTION SETTINGS
# ===================================================    
@app.route("/legal")
def legal():
    return render_template("legal.html", return_to="/settings")

@app.route("/terms")
def terms():
    return render_template("terms.html", return_to=request.referrer or "/settings")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html", return_to=request.referrer or "/settings")

@app.route("/about")
def about():
    return render_template("about.html", return_to=request.referrer or "/settings")

# ============================
# CHANGE PASSWORD — GET + POST
# ============================

@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session.get("user_id")

    # Ensure settings row exists (CRITICAL FIX)
    settings = Settings.query.filter_by(user_id=user_id).first()
    if settings is None:
        settings = Settings(user_id=user_id)
        db.session.add(settings)
        db.session.commit()

    if request.method == "GET":
        return render_template("change_password.html", settings=settings)

    # POST: user submitted the form
    current_pw = request.form.get("current_password")
    new_pw = request.form.get("new_password")
    confirm_pw = request.form.get("confirm_password")

    if new_pw != confirm_pw:
        flash("New passwords do not match.", "error")
        return redirect("/change_password")

    user = db.session.get(User, int(user_id))

    if not user or not check_password_hash(user.password, current_pw):
        flash("Current password is incorrect.", "error")
        return redirect("/change_password")

    user.password = generate_password_hash(new_pw)
    db.session.commit()

    flash("Password updated successfully!", "success")
    return redirect("/settings")

# -------------------------------------------------
# DUTY HOURS CHECKER
# -------------------------------------------------

@app.route("/duty-hours-checker", methods=["GET", "POST"])
def duty_hours_checker_page():
    # Load theme settings for this page
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()

    def to_minutes(t):
        if not t:
            return 0
        h, m = map(int, t.split(":"))
        return h * 60 + m

    def to_hhmm(minutes):
        h = minutes // 60
        m = minutes % 60
        return f"{h:02d}:{m:02d}"

    # -------------------------------------------------
    # 1. GET REQUEST → show empty form
    # -------------------------------------------------
    if request.method == "GET":
        return render_template("duty_hours_checker.html", results=None, settings=settings)

    # -------------------------------------------------
    # 2. POST REQUEST → repopulation
    # -------------------------------------------------
    if request.method == "POST":
        if request.form.get("repopulate"):
            results = json.loads(request.form.get("results_json"))
            
            # ⭐ FIX: Convert formatted date back to ISO for the date input
            if "raw_date" in results:
                try:
                    d = datetime.strptime(results["raw_date"], "%A %d %B %Y")
                    results["raw_date"] = d.strftime("%Y-%m-%d")
                except:
                    pass
                    
            # ⭐ NEW FIX: ensure the date picker gets ISO
            results["duty_date"] = results["raw_date"]

            # ⭐ NEW: regenerate JSON so Save Duty receives ISO
            results_json_fixed = json.dumps(results)

            # ⭐ Restore outstation allowance
            results["outstation_allowance"] = request.form.get("outstation_allowance")

            # ⭐ Restore duty number
            results["duty_number"] = request.form.get("duty_number")

            # ⭐ NEW FIX: Restore incident delay checkbox correctly
            incident_mins = int(results.get("incident_delay_minutes", 0) or 0)
            if incident_mins > 0:
                results["incident_delay"] = "Yes"
            else:
                results["incident_delay"] = "No"

            return render_template(
                "duty_hours_checker.html",
                results=results,
                results_json=results_json_fixed,
                settings=settings
            )



        # -------------------------------------------------
        # 3. NORMAL POST → process form submission
        # -------------------------------------------------

        # BASIC FIELDS
        duty_number = request.form.get("duty_number")
        duty_date_raw = request.form.get("duty_date")
        vehicle_type = request.form.get("vehicle_type")

        traffic_delay = "Yes" if request.form.get("traffic_delay") else "No"
        incident_delay = "Yes" if request.form.get("incident_delay") else "No"
        traffic_delay_minutes = int(request.form.get("traffic_delay_minutes") or 0)
        incident_delay_minutes = int(request.form.get("incident_delay_minutes") or 0)
        
        # ⭐ Multi-Bus Breakdown
        bus_nos = request.form.getlist("bus_no[]")
        reg_plates = request.form.getlist("reg_plate[]")
        time_froms = request.form.getlist("time_from[]")
        time_tos = request.form.getlist("time_to[]")

        buses = []
        for i in range(len(reg_plates)):
            # Only add if there's at least a registration plate entered
            if reg_plates[i].strip():
                buses.append({
                    "bus_no": bus_nos[i] if i < len(bus_nos) else "",
                    "reg_plate": reg_plates[i],
                    "time_from": time_froms[i] if i < len(time_froms) else "",
                    "time_to": time_tos[i] if i < len(time_tos) else ""
                })

        # ⭐ Passenger Issues
        passenger_issues = []
        total_issue_delay = 0
        i = 1
        while True:
            dropdown_type = request.form.get(f"issue{i}_type")
            manual_type = request.form.get(f"issue{i}_type_manual")
            issue_delay = request.form.get(f"issue{i}_delay")
            issue_notes = request.form.get(f"issue{i}_notes")

            if not (dropdown_type or manual_type or issue_delay or issue_notes):
                break

            issue_type = manual_type if dropdown_type == "Other" else dropdown_type
            delay_val = int(issue_delay) if issue_delay else 0
            total_issue_delay += delay_val

            passenger_issues.append({
                "type": issue_type,
                "delay": delay_val,
                "notes": issue_notes
            })
            i += 1

        # ⭐ Outstation Allowance
        outstation_allowance = int(request.form.get("outstation_allowance") or 0)

        # ⭐ FORMAT DATE
        try:
            duty_date_obj = datetime.strptime(duty_date_raw, "%Y-%m-%d")
            duty_date_full = duty_date_obj.strftime("%A %d %B %Y")
        except:
            duty_date_full = duty_date_raw

        # ⭐ TIMES
        start_time = request.form.get("start_time")
        finish_time = request.form.get("finish_time")
        actual_finish_time = request.form.get("actual_finish_time")

        start_minutes = to_minutes(start_time)
        scheduled_finish_minutes = to_minutes(finish_time)

        if scheduled_finish_minutes < start_minutes:
            scheduled_finish_minutes += 24 * 60

        # ⭐ ACTUAL FINISH LOGIC
        if actual_finish_time:
            actual_finish_minutes = to_minutes(actual_finish_time)

            if actual_finish_minutes < start_minutes:
                actual_finish_minutes += 24 * 60

            finish_difference_minutes = actual_finish_minutes - scheduled_finish_minutes
            finish_difference = to_hhmm(abs(finish_difference_minutes))

            final_finish_minutes = actual_finish_minutes
        else:
            actual_finish_time = ""
            finish_difference = "N/A"
            final_finish_minutes = scheduled_finish_minutes

        # ⭐ BREAKS
        total_break_minutes = 0
        total_lost_minutes = 0
        breaks = []

        i = 1
        while True:
            b_start = request.form.get(f"break{i}_start")
            b_end = request.form.get(f"break{i}_end")
            b_lost = request.form.get(f"break{i}_lost")

            if not (b_start or b_end or b_lost):
                break

            if b_start and b_end:
                b_start_m = to_minutes(b_start)
                b_end_m = to_minutes(b_end)

                if b_end_m < b_start_m:
                    b_end_m += 24 * 60

                total_break_minutes += (b_end_m - b_start_m)

            if b_lost:
                total_lost_minutes += int(b_lost)

            breaks.append({
                "start": b_start,
                "end": b_end,
                "lost": b_lost
            })

            i += 1

        # ⭐ NDW (Non‑Driving Work)
        ndw_entries = []
        total_ndw_minutes = 0
        i = 1
        while True:
            ndw_task = request.form.get(f"ndw{i}_task")
            ndw_start = request.form.get(f"ndw{i}_start")
            ndw_end = request.form.get(f"ndw{i}_end")

            if not (ndw_task or ndw_start or ndw_end):
                break

            if ndw_start and ndw_end:
                ndw_start_m = to_minutes(ndw_start)
                ndw_end_m = to_minutes(ndw_end)

                if ndw_end_m < ndw_start_m:
                    ndw_end_m += 24 * 60

                duration = ndw_end_m - ndw_start_m
                total_ndw_minutes += duration
            else:
                duration = 0

            ndw_entries.append({
                "task": ndw_task,
                "start": ndw_start,
                "end": ndw_end,
                "duration": duration
            })

            i += 1

        # ⭐ CALCULATIONS
        total_duty_minutes = final_finish_minutes - start_minutes

        total_lost_minutes += traffic_delay_minutes
        total_lost_minutes += incident_delay_minutes

        # ⭐ Calculate driving time
        driving_minutes = (
            total_duty_minutes
            - total_break_minutes
            - total_lost_minutes
            - total_issue_delay
            - total_ndw_minutes
        )

        # ⭐ Driving time must never be negative
        driving_minutes = max(0, driving_minutes)


        # ⭐ RULE ENGINE
        duty_status = "Green"
        driving_status = "Green"

        duty_mins = total_duty_minutes
        drive_mins = driving_minutes
        break_mins = total_break_minutes

        delay_extra_drive = traffic_delay_minutes + incident_delay_minutes
        delay_extra_duty = traffic_delay_minutes + incident_delay_minutes
        outstation_extra_drive = outstation_allowance

        # BUS RULES
        if vehicle_type == "bus":
            if duty_mins > (16 * 60) + delay_extra_duty:
                duty_status = "Red"
            elif duty_mins > (15 * 60):
                duty_status = "Amber"

            if drive_mins > (10 * 60) + delay_extra_drive + outstation_extra_drive:
                driving_status = "Red"
            elif drive_mins > (9 * 60) + outstation_extra_drive:
                driving_status = "Amber"

            if duty_mins > (8.5 * 60) and break_mins < 45:
                driving_status = "Red"
            elif duty_mins > (5.5 * 60) and break_mins < 30:
                driving_status = "Amber"

        # COACH RULES
        if vehicle_type == "coach":
            if drive_mins > (10 * 60) + delay_extra_drive + outstation_extra_drive:
                driving_status = "Red"
            elif drive_mins > (9 * 60) + outstation_extra_drive:
                driving_status = "Amber"

            if drive_mins > (4.5 * 60) and break_mins < 45:
                driving_status = "Red"

        # OVERALL STATUS
        if duty_status == "Red" or driving_status == "Red":
            overall_status = "Red"
        elif duty_status == "Amber" or driving_status == "Amber":
            overall_status = "Amber"
        else:
            overall_status = "Green"

        # THEORY REPORT
        theory_report = f"""
Your total duty time was {to_hhmm(duty_mins)}, which results in a {duty_status.upper()} status.
Your driving time was {to_hhmm(drive_mins)}, which results in a {driving_status.upper()} status.
Breaks totalled {to_hhmm(break_mins)}.
Passenger issue delays totalled {to_hhmm(total_issue_delay)}.
NDW totalled {to_hhmm(total_ndw_minutes)}.

Delay allowances applied: {delay_extra_drive} minutes driving, {delay_extra_duty} minutes duty.
Outstation allowance applied: {outstation_extra_drive} minutes.

Overall, this duty is rated as {overall_status.upper()}.
""".strip()

        # ⭐ RESULTS DICTIONARY
        results = {
            "duty_number": duty_number,

            # RAW ISO DATE (YYYY-MM-DD)
            "raw_date": duty_date_raw,

            # FORMATTED DATE (Wednesday 08 July 2026)
            "duty_date": duty_date_full,

            "vehicle_type": vehicle_type,
            
            # Multi-Bus Entries Added Here
            "buses": buses,            
    
            "traffic_delay": traffic_delay,
            "traffic_delay_minutes": traffic_delay_minutes,

            "incident_delay": incident_delay,
            "incident_delay_minutes": incident_delay_minutes,

            "outstation_allowance": outstation_allowance,

            "start_time": start_time,
            "finish_time": finish_time,
            "actual_finish_time": actual_finish_time,
            "finish_difference": finish_difference,

            "total_duty": to_hhmm(total_duty_minutes),
            "total_breaks": to_hhmm(total_break_minutes),
            "total_lost": to_hhmm(total_lost_minutes),
            "total_issue_delay": to_hhmm(total_issue_delay),
            "total_ndw": to_hhmm(total_ndw_minutes),

            "driving_time": to_hhmm(driving_minutes),

            "breaks": breaks,
            "passenger_issue": passenger_issues,
            "ndw_entries": ndw_entries,

            "duty_status": duty_status,
            "driving_status": driving_status,
            "overall_status": overall_status,

            "theory_report": theory_report
        
        }    


        return render_template("analyse_waybill.html", results=results, settings=settings)

 
# =======================================================
# ANALYSE SUMMARY
# =======================================================

@app.route("/analyse-summary/<int:id>")
def analyse_summary(id):
    # Load theme + user settings
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()

    # Load duty record
    duty = WeeklyDuty.query.get_or_404(id)

    # ⭐ FIX: Reformat date safely
    try:
        d = datetime.strptime(duty.duty_date, "%Y-%m-%d")
        duty_date_full = d.strftime("%A %d %B %Y")
    except:
        duty_date_full = duty.duty_date  # fallback

    # ⭐ Load linked multi-bus records from the database
    buses_list = []
    if hasattr(duty, 'buses') and duty.buses:
        for b in duty.buses:
            buses_list.append({
                "bus_no": b.bus_no,
                "reg_plate": b.reg_plate,
                "time_from": b.time_from,
                "time_to": b.time_to
            })

    # Convert DB entry back into the results dict analyse_waybill expects
    results = {
        "duty_number": duty.duty_number,

        # ⭐ FIX: raw_date must ALWAYS be YYYY-MM-DD
        "raw_date": duty.duty_date,
        "duty_date": duty_date_full,

        "vehicle_type": duty.vehicle_type,

        # ⭐ Multi-Bus Entries for saved summary view
        "buses": buses_list,

        # Delays
        "traffic_delay": "Yes" if duty.traffic_delay == "Yes" else "No",
        "traffic_delay_minutes": duty.traffic_delay_minutes,

        "incident_delay": "Yes" if duty.incident_delay else "No",   # ⭐ FIXED
        "incident_delay_minutes": duty.incident_delay_minutes,

        # Outstation (not stored in DB yet)
        "outstation_allowance": duty.outstation_allowance,

        # Times
        "start_time": duty.start_time,
        "finish_time": duty.finish_time,
        "actual_finish_time": duty.actual_finish_time,
        "finish_difference": "N/A",

        # Totals
        "total_duty": duty.total_duty,
        "driving_time": duty.driving_time,

        # Breaks
        "breaks": json.loads(duty.breaks) if duty.breaks else [],

        # Passenger Issues
        "passenger_issue": json.loads(duty.passenger_issue) if duty.passenger_issue else [],

        # ⭐ NDW (Non‑Driving Work)
        "ndw_entries": json.loads(duty.ndw_entries) if duty.ndw_entries else [],
        "total_ndw": duty.total_ndw if hasattr(duty, "total_ndw") else "00:00",

        # Statuses
        "duty_status": duty.duty_status,
        "driving_status": duty.driving_status,
        "overall_status": duty.overall_status,

        # Report
        "theory_report": "Summary loaded from saved duty."
    }
    
    results["total_breaks"] = duty.total_breaks
    results["total_passenger_issue"] = duty.total_passenger_issue
    results["total_lost_time"] = duty.total_lost_time

    return_to = request.args.get("return_to", "weekly")

    return render_template(
        "analyse_waybill.html",
        results=results,
        settings=settings,
        return_to=return_to
    )
    


        
# ============================================
# SAVE DUTY
# ============================================

@app.route("/save-duty", methods=["POST"])
@login_required
def save_duty():

    # Load JSON payload safely
    results_json = request.form.get("results_json")

    results = json.loads(results_json) if results_json else {}
    
    # ⭐ ADD THIS DEBUG LINE HERE:
    print(">>> BUSES FOUND IN RESULTS:", results.get("buses"))

    # ---------------------------------------------------------
    # 1️⃣ GET DUTY DATE (from hidden field or JSON fallback)
    # ---------------------------------------------------------
    duty_date = request.form.get("duty_date") or results.get("raw_date")
    duty_number = results.get("duty_number")



    # ---------------------------------------------------------
    # 2️⃣ FIX DUTY DATE FORMAT (THIS STOPS THE CRASH)
    # ---------------------------------------------------------
    # Try ISO first
    try:
        duty_date_obj = datetime.strptime(duty_date, "%Y-%m-%d")
    except ValueError:
        # Try formatted (Wednesday 08 July 2026)
        try:
            d = datetime.strptime(duty_date, "%A %d %B %Y")
            duty_date = d.strftime("%Y-%m-%d")
            duty_date_obj = d
        except ValueError:
            print("ERROR: Could not parse duty_date:", duty_date)
            raise

    # Compute start of week
    start_of_week = duty_date_obj - timedelta(days=duty_date_obj.weekday())

    # ---------------------------------------------------------
    # 3️⃣ FIND EXISTING DUTY
    # ---------------------------------------------------------
    existing = WeeklyDuty.query.filter_by(
        user_id=current_user.id,
        duty_date=duty_date,
        duty_number=duty_number
    ).first()


    # Ignore corrupted entries
    if existing and not existing.duty_date.startswith("202"):
        existing = None

    entry = existing if existing else WeeklyDuty(user_id=current_user.id)


    # ---------------------------------------------------------
    # 4️⃣ UPDATE FIELDS
    # ---------------------------------------------------------
    entry.duty_date = duty_date
    entry.start_of_week = start_of_week.strftime("%Y-%m-%d")

    entry.duty_number = duty_number
    entry.vehicle_type = results.get("vehicle_type")

    entry.start_time = results.get("start_time")
    entry.finish_time = results.get("finish_time")
    entry.actual_finish_time = request.form.get("actual_finish_time")

    entry.total_duty = results.get("total_duty")
    entry.driving_time = results.get("driving_time")
    
    # ---------------------------------------------------------
    # 4B️⃣ CALCULATE TOTAL BREAKS (INCLUDING LOST MINUTES)
    # ---------------------------------------------------------
    breaks_list = results.get("breaks", [])
    total_break_minutes = 0

    for b in breaks_list:
        start = b.get("start")
        end = b.get("end")
        lost_raw = b.get("lost", 0)
        lost = int(lost_raw) if str(lost_raw).strip().isdigit() else 0


        if start and end:
            # Convert HH:MM → datetime
            s = datetime.strptime(start, "%H:%M")
            e = datetime.strptime(end, "%H:%M")

            # Raw break duration
            diff = int((e - s).total_seconds() / 60)

            # Subtract lost minutes
            actual = max(diff - lost, 0)

            total_break_minutes += actual

    # Convert minutes → HH:MM
    hours = total_break_minutes // 60
    mins = total_break_minutes % 60
    entry.total_breaks = f"{hours:02d}:{mins:02d}"


    entry.breaks = json.dumps(results.get("breaks", []))
    
    # ---------------------------------------------------------
    # 4C️⃣ CALCULATE TOTAL PASSENGER ISSUE MINUTES
    # ---------------------------------------------------------
    issues_list = results.get("passenger_issue", [])
    total_issue_minutes = 0

    for issue in issues_list:
        delay = issue.get("delay", 0)
        try:
            total_issue_minutes += int(delay)
        except:
            pass

    # Convert minutes → HH:MM
    hours_i = total_issue_minutes // 60
    mins_i = total_issue_minutes % 60
    entry.total_passenger_issue = f"{hours_i:02d}:{mins_i:02d}"

    entry.passenger_issue = json.dumps(results.get("passenger_issue", []))
    
    # ---------------------------------------------------------
    # 4D️⃣ CALCULATE TOTAL LOST TIME
    # ---------------------------------------------------------
    lost_from_breaks = 0
    for b in breaks_list:
        try:
            lost_from_breaks += int(b.get("lost", 0))
        except:
            pass

    lost_from_issues = total_issue_minutes

    lost_from_incident = int(results.get("incident_delay_minutes", 0))
    lost_from_traffic = int(results.get("traffic_delay_minutes", 0))

    total_lost = lost_from_breaks + lost_from_issues + lost_from_incident + lost_from_traffic

    # Convert minutes → HH:MM
    hours_l = total_lost // 60
    mins_l = total_lost % 60
    entry.total_lost_time = f"{hours_l:02d}:{mins_l:02d}"


    entry.delay_type = results.get("delay_type")
    entry.traffic_delay = results.get("traffic_delay")
    entry.traffic_delay_minutes = results.get("traffic_delay_minutes", 0)

    entry.incident_delay = 1 if results.get("incident_delay") == "Yes" else 0
    entry.incident_delay_minutes = results.get("incident_delay_minutes", 0)

    entry.ndw_entries = json.dumps(results.get("ndw_entries", []))
    entry.total_ndw = results.get("total_ndw", "00:00")

    entry.duty_status = results.get("duty_status")
    entry.driving_status = results.get("driving_status")
    entry.overall_status = results.get("overall_status")

    entry.outstation_allowance = results.get("outstation_allowance")
    
    # ---------------------------------------------------------
    # 4E️⃣ SAVE MULTI-BUS ENTRIES
    # ---------------------------------------------------------
    # If the entry already existed, clear old linked buses first to avoid duplicates
    if existing:
        DutyBus.query.filter_by(duty_id=entry.id).delete()

    # Flush session so entry.id is available if it's a new record
    db.session.add(entry)
    db.session.flush()

    # Process and save new bus list from results JSON
    buses_list = results.get("buses", [])
    for b in buses_list:
        if b.get("reg_plate"):
            new_bus = DutyBus(
                duty_id=entry.id,
                bus_no=b.get("bus_no"),
                reg_plate=b.get("reg_plate"),
                time_from=b.get("time_from"),
                time_to=b.get("time_to")
            )
            db.session.add(new_bus)

    # ---------------------------------------------------------
    # 5️⃣ SAVE
    # ---------------------------------------------------------
    db.session.add(entry)
    db.session.commit()
    
    flash("Duty saved successfully.", "success")

    return_to = request.args.get("return_to", "weekly")

    if return_to == "monthly":
        return redirect(url_for(
            "monthly_view",
            month=duty_date_obj.month,
            year=duty_date_obj.year
        ))

    elif return_to == "weekly":
        return redirect(url_for("weekly_view"))

    elif return_to == "checker":
        return redirect(url_for("weekly_view"))

    # Fallback
    return redirect(url_for("weekly_view"))

# =======================================
# WEEKLY VIEW
# =======================================
@app.route("/weekly_view")
@login_required
def weekly_view():
    # 1. Absolute safety check for authentication
    if not current_user.is_authenticated:
        return redirect(url_for("login"))

    # 2. Flexible role authorization check
    is_authorized = (
        getattr(current_user, 'role_driver', False) or 
        getattr(current_user, 'role_enthusiast', False) or 
        getattr(current_user, 'role_superadmin', False) or
        getattr(current_user, 'level', 0) in [1, 2, 3, 4]
    )
    
    if not is_authorized:
        flash("You do not have permission to view this page.", "danger")
        return redirect(url_for("enthusiast_dashboard"))

    # 3. Fetch settings safely using current_user.id
    settings = Settings.query.filter_by(user_id=current_user.id).first()
    week_start = settings.week_start_day.lower() if settings and settings.week_start_day else "saturday"

    # Read selected date from URL (default = today)
    selected_date_str = request.args.get("date")
    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        except:
            selected_date = date.today()
    else:
        selected_date = date.today()

    WEEKDAY_MAP = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
    }

    start_index = WEEKDAY_MAP.get(week_start, 5) # Defaults to Saturday (5) if not found

    current_index = selected_date.weekday()
    offset = (current_index - start_index) % 7
    week_start_date = selected_date - timedelta(days=offset)
    prev_week_start = week_start_date - timedelta(days=7)
    next_week_start = week_start_date + timedelta(days=7)

    prev_week_display = prev_week_start.strftime("%d/%m/%Y")
    next_week_display = next_week_start.strftime("%d/%m/%Y")
    current_week_display = week_start_date.strftime("%d/%m/%Y")

    week_end_date = week_start_date + timedelta(days=6)

    # Build list of week dates
    week_dates = [week_start_date + timedelta(days=i) for i in range(7)]
    week_date_strings = [d.strftime("%Y-%m-%d") for d in week_dates]

    # Fetch duties
    all_duties = WeeklyDuty.query.filter_by(user_id=current_user.id).all()
    duties = []

    def add_minutes_to_time(time_str, minutes):
        if not time_str or ":" not in time_str:
            return time_str
        h, m = map(int, time_str.split(":"))
        total = h * 60 + m + minutes
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"

    # Decode + enrich
    for d in all_duties:
        # -----------------------------
        # PARSE DATE (Weekly View)
        # -----------------------------
        raw = str(d.duty_date).strip()
        parsed = None

        # YYYY-MM-DD
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d").date()
        except:
            pass

        # DD/MM/YYYY
        if parsed is None:
            try:
                parsed = datetime.strptime(raw, "%d/%m/%Y").date()
            except:
                pass

        # Thursday 09 July 2026
        if parsed is None:
            try:
                parsed = datetime.strptime(raw, "%A %d %B %Y").date()
            except:
                pass

        # If still not parsed → skip
        if parsed is None:
            continue

        d_date = parsed
        d.duty_date = d_date

        # -----------------------------
        # WEEKLY FILTER
        # -----------------------------
        if not (week_start_date <= d_date <= week_end_date):
            continue

        d.date = d_date
        d.date_uk = d.date.strftime("%d/%m/%Y")

        # -----------------------------
        # DECODE LISTS
        # -----------------------------
        d.breaks_list = json.loads(d.breaks) if d.breaks else []
        d.passenger_list = json.loads(d.passenger_issue) if d.passenger_issue else []
        d.ndw_list = json.loads(d.ndw_entries) if d.ndw_entries else []

        # -----------------------------
        # BREAK TOTAL
        # -----------------------------
        total_break_minutes = 0
        for b in d.breaks_list:
            try:
                start = datetime.strptime(b["start"], "%H:%M")
                end = datetime.strptime(b["end"], "%H:%M")
                diff = (end - start).seconds // 60
                total_break_minutes += diff
            except:
                pass

        d.total_breaks = (
            f"{total_break_minutes // 60:02d}:{total_break_minutes % 60:02d}"
            if total_break_minutes > 0 else "None"
        )

        # -----------------------------
        # PASSENGER ISSUE TOTAL
        # -----------------------------
        issue_total_mins = sum(int(p.get("delay", 0)) for p in d.passenger_list)
        d.total_passenger_issue = (
            f"{issue_total_mins//60:02d}:{issue_total_mins%60:02d}"
            if issue_total_mins > 0 else "None"
        )

        # -----------------------------
        # NDW TOTAL
        # -----------------------------
        ndw_total_mins = sum(int(n.get("duration", 0)) for n in d.ndw_list)
        d.total_ndw = (
            f"{ndw_total_mins//60:02d}:{ndw_total_mins%60:02d}"
            if ndw_total_mins > 0 else "None"
        )

        # -----------------------------
        # DELAY TYPE
        # -----------------------------
        traffic = d.traffic_delay_minutes or 0
        incident = d.incident_delay_minutes or 0

        if traffic > 0 and incident > 0:
            d.delay_type = (
                f"Traffic ({traffic} mins)<br/>"
                f"Incident ({incident} mins)"
            )
        elif traffic > 0:
            d.delay_type = f"Traffic ({traffic} mins)"
        elif incident > 0:
            d.delay_type = f"Incident ({incident} mins)"
        elif issue_total_mins > 0:
            d.delay_type = f"Passenger ({issue_total_mins} mins)"
        else:
            d.delay_type = "None"

        # -----------------------------
        # ACTUAL FINISH
        # -----------------------------
        if getattr(d, "actual_finish_time") not in [None, "", "None"]:
            pass
        else:
            d.actual_finish_time = d.finish_time

        duties.append(d)

    # -----------------------------
    # SORT WEEKLY DUTIES
    # -----------------------------
    duties.sort(key=lambda x: x.date, reverse=True)

    # -------------------------------------------------------
    # GROUP DUTIES BY DATE
    # -------------------------------------------------------
    duties_by_date = {d: [] for d in week_dates}

    for d in duties:
        duties_by_date[d.duty_date].append(d)

    # -------------------------------------------------------
    # WEEKLY TOTALS
    # -------------------------------------------------------
    weekly_totals = {
        "duty": "None",
        "driving": "None",
        "breaks": "None",
        "issue": "None",
        "ndw": "None",
        "delay": "None"
    }

    duty_mins = 0
    driving_mins = 0
    break_mins = 0
    issue_mins = 0
    ndw_mins = 0
    delay_mins = 0

    for d in duties:
        if d.total_duty and ":" in d.total_duty:
            h, m = d.total_duty.split(":")
            duty_mins += int(h)*60 + int(m)

        if d.driving_time and ":" in d.driving_time:
            h, m = d.driving_time.split(":")
            driving_mins += int(h)*60 + int(m)

        if d.total_breaks != "None":
            h, m = d.total_breaks.split(":")
            break_mins += int(h)*60 + int(m)

        if d.total_passenger_issue != "None":
            h, m = d.total_passenger_issue.split(":")
            issue_mins += int(h)*60 + int(m)

        if d.total_ndw != "None":
            h, m = d.total_ndw.split(":")
            ndw_mins += int(h)*60 + int(m)

        delay_mins += (d.traffic_delay_minutes or 0)
        delay_mins += (d.incident_delay_minutes or 0)

    def mins_to_hhmm(total):
        return f"{total//60:02d}:{total%60:02d}" if total > 0 else "None"

    weekly_totals["duty"] = mins_to_hhmm(duty_mins)
    weekly_totals["driving"] = mins_to_hhmm(driving_mins)
    weekly_totals["breaks"] = mins_to_hhmm(break_mins)
    weekly_totals["issue"] = mins_to_hhmm(issue_mins)
    weekly_totals["ndw"] = mins_to_hhmm(ndw_mins)
    weekly_totals["delay"] = f"{delay_mins} mins" if delay_mins > 0 else "None"

    # -------------------------------------------------------
    # EXPORT FOR PRINT WEEKLY
    # -------------------------------------------------------
    export = []
    for d in duties:
        # Collect linked multi-bus entries for export
        bus_export_list = []
        if hasattr(d, 'buses') and d.buses:
            for b in d.buses:
                bus_export_list.append({
                    "bus_no": b.bus_no,
                    "reg_plate": b.reg_plate,
                    "time_from": b.time_from,
                    "time_to": b.time_to
                })

        export.append({
            "date": d.duty_date.strftime("%Y-%m-%d"),
            "duty_number": d.duty_number,
            "osa": d.outstation_allowance or 0,
            "vehicle_type": d.vehicle_type,
            "buses": bus_export_list,  # ⭐ Added multi-bus array here
            "start_time": d.start_time,
            "finish_time": d.finish_time,
            "actual_finish": d.actual_finish_time,
            "total_duty": d.total_duty,
            "driving_time": d.driving_time,
            "breaks": d.total_breaks,
            "passenger_issues": d.total_passenger_issue,
            "ndw": d.total_ndw,
            "delay_type": d.delay_type,
            "duty_status": d.duty_status,
            "driving_status": d.driving_status,
            "overall_status": d.overall_status
        })
        
    with open("weekly_export.json", "w") as f:
        json.dump(export, f, indent=4)

    return render_template(
        "weekly_view.html",
        settings=settings,
        week_start=week_start,
        week_dates=week_dates,
        duties=duties,
        duties_by_date=duties_by_date,
        weekly_totals=weekly_totals,
        current_week_display=current_week_display,
        prev_week_display=prev_week_display,
        next_week_display=next_week_display,
        prev_week_start=prev_week_start,
        next_week_start=next_week_start
    )

# =====================================
# DELETE DUTY
# =====================================

@app.route("/delete-duty/<int:id>", methods=["POST"])
def delete_duty(id):
    duty = WeeklyDuty.query.get_or_404(id)
    db.session.delete(duty)
    db.session.commit()
    return redirect("/weekly_view")

# =====================================
# WEEKLY PRINT HEADER WAVES
# =====================================

def draw_header(canvas, doc, title, *args, **kwargs):
    canvas.saveState()


    x = doc.leftMargin
    w = doc.width

    # HEIGHTS
    bar_h  = 100
    wave_h = 15

    # BLUE BAR POSITION
    bar_y = doc.height + doc.topMargin - bar_h + 20

    # DRAW BLUE BAR
    canvas.setFillColorRGB(0.10, 0.23, 0.60)
    canvas.rect(x, bar_y, w, bar_h, fill=1, stroke=0)

    # TITLE
    canvas.setFillColorRGB(1, 1, 1)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawCentredString(
        x + (w / 2),
        bar_y + bar_h - 40,
        title
    )

    # LOGO
    canvas.drawImage(
        "static/assets/logo.png",
        x + w - 80,
        bar_y + bar_h - 65,
        width=70,
        height=60,
        preserveAspectRatio=True,
        mask='auto'
    )

    # WAVES
    wave_y = bar_y + 10
    wave_path = os.path.abspath("static/assets/header_waves.png")
    canvas.drawImage(
        wave_path,
        x,
        wave_y,
        width=w,
        height=wave_h,
        preserveAspectRatio=False,
        mask='auto'
    )

    canvas.restoreState()

# ====================================
# WEEKLY PRINT FOOTER
# ====================================

def draw_footer(canvas, doc):
    canvas.setFont("Helvetica", 9)
    canvas.setFillColorRGB(0.3, 0.3, 0.3)

    # Page number
    page_num = canvas.getPageNumber()
    canvas.drawRightString(
        doc.width + doc.leftMargin,
        20,
        f"Page {page_num}"
    )

    # Footer text
    canvas.drawString(
        doc.leftMargin,
        20,
        "© 2026 Transport Hub — All Rights Reserved"
    )

# ================================
# WEEKLY PRINT VIEW (PDF MODE)
# ================================

@app.route("/print-weekly")
def print_weekly():

    # 1. Load enriched weekly data exported by /weekly_view
    try:
        with open("weekly_export.json", "r") as f:
            duties = json.load(f)
    except Exception:
        return redirect("/weekly_view")

    # 2. PDF output path
    pdf_path = os.path.abspath("weekly_report.pdf")

    # 3. Build PDF document
    doc = BaseDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=40
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height - 90,
        id='normal'
    )

    doc.addPageTemplates([
        PageTemplate(
            id='UK TransportHub',
            frames=[frame],
            onPage=lambda canvas, doc: draw_header(canvas, doc, "UK Transport Hub — Weekly Duty Records"),
            onPageEnd=lambda canvas, doc: draw_footer(canvas, doc)
        )
    ])


    elements = [Spacer(1, 0)]

    # ============================
    # TABLE HEADER
    # ============================

    data = [
        ["Date","Duty No.","OSA","Vehicle","Start","Finish","Actual Finish",
         "Total Duty","Driving Time","Breaks","Passenger Issues","NDW",
         "Delay Type","Duty Status","Driving Status","Overall Status"]
    ]

    # ============================
    # CELL STYLES
    # ============================

    cell_style = ParagraphStyle(
        name="CellStyle",
        fontSize=8,
        leading=10,
        wordWrap='CJK'
    )

    # ============================
    # STATUS BADGE (FORMAT ONLY)
    # ============================

    def status_badge(text):
        if text == "Green":
            return Paragraph(
                "<para align='center'><font color='#FFFFFF'><b>GREEN</b></font></para>",
                ParagraphStyle(name="GreenBadge", backColor=colors.HexColor("#4CAF50"),
                               textColor=colors.white, fontSize=7, leading=8,
                               borderPadding=(2,4,2,4), alignment=1))
        if text == "Amber":
            return Paragraph(
                "<para align='center'><font color='#000000'><b>AMBER</b></font></para>",
                ParagraphStyle(name="AmberBadge", backColor=colors.HexColor("#FFC107"),
                               textColor=colors.black, fontSize=7, leading=8,
                               borderPadding=(2,4,2,4), alignment=1))
        if text == "Red":
            return Paragraph(
                "<para align='center'><font color='#FFFFFF'><b>RED</b></font></para>",
                ParagraphStyle(name="RedBadge", backColor=colors.HexColor("#F44336"),
                               textColor=colors.white, fontSize=7, leading=8,
                               borderPadding=(2,4,2,4), alignment=1))
        return Paragraph(f"<para align='center'>{text}</para>", cell_style)

    # ============================
    # BUILD TABLE ROWS (NO LOGIC)
    # ============================

    for d in duties:

        # Convert plain text into Paragraph objects
        breaks_cell = Paragraph(f"<para align='center'>{d['breaks']}</para>", cell_style)
        passenger_cell = Paragraph(f"<para align='center'>{d['passenger_issues']}</para>", cell_style)
        ndw_cell = Paragraph(f"<para align='center'>{d['ndw']}</para>", cell_style)
        delay_cell = Paragraph(f"<para align='center'>{d['delay_type']}</para>", cell_style)

        data.append([
            d["date"],
            d["duty_number"],
            d["osa"],
            d["vehicle_type"],
            d["start_time"],
            d["finish_time"],
            d["actual_finish"],
            d["total_duty"],
            d["driving_time"],
            breaks_cell,
            passenger_cell,
            ndw_cell,
            delay_cell,
            status_badge(d["duty_status"]),
            status_badge(d["driving_status"]),
            status_badge(d["overall_status"])
        ])

    # ============================
    # TABLE + STYLE
    # ============================

    table = Table(data, colWidths=[
        doc.width * 0.07,   # Date
        doc.width * 0.05,   # Duty No
        doc.width * 0.04,   # OSA
        doc.width * 0.05,   # Vehicle
        doc.width * 0.04,   # Start
        doc.width * 0.04,   # Finish
        doc.width * 0.07,   # Actual Finish Time
        doc.width * 0.06,   # Total Duty
        doc.width * 0.07,   # Driving Time
        doc.width * 0.04,   # Breaks
        doc.width * 0.10,   # Passenger Issues
        doc.width * 0.04,   # NDW
        doc.width * 0.10,   # Delay Type
        doc.width * 0.07,   # Duty Status
        doc.width * 0.08,   # Driving Status
        doc.width * 0.08    # Overall Status
    ], repeatRows=1)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0A2A66")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ALIGN", (0,0), (-1,0), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),

        ("ROWHEIGHT", (0,1), (-1,-1), None),

        ("ALIGN", (0,1), (-1,-1), "CENTER"),

        ("ALIGN", (12,1), (12,-1), "LEFT"),
        ("VALIGN", (12,1), (12,-1), "TOP"),

        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BOX", (0,0), (-1,-1), 1, colors.black),

        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))

    elements.append(Spacer(1, 30))
    elements.append(table)

    # ============================
    # BUILD PDF
    # ============================

    doc.build(elements)

    # ============================
    # OPEN PDF + REDIRECT
    # ============================

    try:
        os.startfile(pdf_path)
    except:
        pass

    return redirect("/weekly_view")

# =================================================
# WEEKLY TAKINGS GET ROUTE (SAFE FALLBACKS)
# =================================================
@app.route("/weekly-takings-add", methods=["GET"])
@login_required
def weekly_takings_add_page():
    try:
        user = current_user

        # Fetch optional relationships safely
        company = None
        if getattr(user, "company", None):
            company = Company.query.filter_by(name=user.company).first()

        settings = Settings.query.filter_by(user_id=user.id).first()

        # 1. Determine start day safely
        start_day = "monday" # Default fallback

        if getattr(user, "week_start_override", None):
            start_day = user.week_start_override.lower()
        elif company and getattr(company, "week_start_day", None):
            start_day = company.week_start_day.lower()
        elif settings and getattr(settings, "week_start", None):
            start_day = settings.week_start.lower()

        day_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6
        }

        # Fall back to monday if day string isn't recognized
        day_index = day_map.get(start_day, 0)

        today = date.today()
        weekday = today.weekday()

        offset = (weekday - day_index) % 7
        week_start = today - timedelta(days=offset)

        return render_template(
            "weekly_takings_add.html",
            week_start=week_start.strftime("%Y-%m-%d")
        )

    except Exception as e:
        print("WEEKLY TAKINGS GET ERROR:", repr(e))
        raise e

# =================================================
# WEEKLY TAKINGS POST ROUTE (FULL BLOCK)
# =================================================
@app.route("/weekly-takings-add", methods=["POST"], endpoint="weekly_takings_add_submit")
@login_required
def weekly_takings_add_submit():

    data = request.get_json()

    try:
        # ---------------------------------------------------------
        # 1. Extract core fields
        # ---------------------------------------------------------
        entry_date_raw = data.get("entry_date")
        week_start_raw = data.get("week_start")

        if not entry_date_raw:
            return {"error": "Missing entry_date"}, 400

        entry_date = datetime.strptime(entry_date_raw, "%Y-%m-%d").date()

        week_start = None
        if week_start_raw:
            week_start = datetime.strptime(week_start_raw, "%Y-%m-%d").date()

        # Normalise day
        day = data.get("day", "").title()

        # Convert totals to float
        full_amount_total = float(data.get("full_amount_total", 0))
        float_total = float(data.get("float_total", 0))
        tips_total = float(data.get("tips_total", 0))
        takings_total = float(data.get("takings_total", 0))
        remaining_float = float(data.get("remaining_float", 0))
        full_amount_diff = float(data.get("full_amount_diff", 0))
        final_takings = float(data.get("final_takings", 0))

        # ---------------------------------------------------------
        # 2. Daily breakdown
        # ---------------------------------------------------------
        daily_weekly_takings_data = data.get("daily_weekly_takings_data", [])

        normalised_daily_data = []
        for item in daily_weekly_takings_data:
            normalised_daily_data.append({
                "day": item.get("day"),
                "bus1": float(item.get("bus1", 0)),
                "bus2": float(item.get("bus2", 0)),
                "bus3": float(item.get("bus3", 0))
            })

        # ---------------------------------------------------------
        # 3. Counts & annulled
        # ---------------------------------------------------------
        annulled = data.get("annulled", [])
        full_amount_count = data.get("full_amount_count", [])
        float_amount_count = data.get("float_amount_count", [])
        tips_amount_count = data.get("tips_amount_count", [])
        takings_amount_count = data.get("takings_amount_count", [])

        user_id = current_user.id

        # ---------------------------------------------------------
        # 4. Check existing entry
        # ---------------------------------------------------------
        existing = WeeklyTakings.query.filter_by(
            user_id=user_id,
            week_start=week_start,
            day=day
        ).first()

        if existing:

            existing.entry_date = entry_date
            existing.full_amount_total = full_amount_total
            existing.float_total = float_total
            existing.tips_total = tips_total
            existing.takings_total = takings_total
            existing.remaining_float = remaining_float
            existing.full_amount_diff = full_amount_diff
            existing.final_takings = final_takings

            existing.full_amount_count = full_amount_count
            existing.float_amount_count = float_amount_count
            existing.tips_amount_count = tips_amount_count
            existing.takings_amount_count = takings_amount_count

            existing.daily_weekly_takings_data = normalised_daily_data

            db.session.flush()

            existing.takings_total = sum(
                (item.get("bus1", 0) or 0) +
                (item.get("bus2", 0) or 0) +
                (item.get("bus3", 0) or 0)
                for item in normalised_daily_data
            )

            AnnulledTicket.query.filter_by(takings_id=existing.id).delete()

            for item in annulled:
                ticket = AnnulledTicket(
                    takings_id=existing.id,
                    ticket_no=item.get("ticket_no") or "N/A",
                    description=item.get("description"),
                    price=float(item.get("price", 0))
                )
                db.session.add(ticket)

        else:

            new_entry = WeeklyTakings(
                user_id=user_id,
                entry_date=entry_date,
                week_start=week_start,
                day=day,
                full_amount_total=full_amount_total,
                float_total=float_total,
                tips_total=tips_total,
                takings_total=takings_total,
                remaining_float=remaining_float,
                full_amount_diff=full_amount_diff,
                final_takings=final_takings,
                full_amount_count=full_amount_count,
                float_amount_count=float_amount_count,
                tips_amount_count=tips_amount_count,
                takings_amount_count=takings_amount_count,
                daily_weekly_takings_data=normalised_daily_data
            )

            db.session.add(new_entry)
            db.session.flush()

            for item in annulled:
                ticket = AnnulledTicket(
                    takings_id=new_entry.id,
                    ticket_no=item.get("ticket_no") or "N/A",
                    description=item.get("description"),
                    price=float(item.get("price", 0))
                )
                db.session.add(ticket)


            new_entry.takings_total = sum(
                (item.get("bus1", 0) or 0) +
                (item.get("bus2", 0) or 0) +
                (item.get("bus3", 0) or 0)
                for item in normalised_daily_data
            )

        db.session.commit()

        return {"status": "ok"}, 200

    except Exception as e:
        db.session.rollback()
        print("WEEKLY TAKINGS ADD ERROR:", repr(e))
        return {"error": str(e)}, 500


# ============================================================
# ANNULLED TICKETS FETCH ROUTE
# ============================================================
@app.route("/annulled_data/<int:takings_id>")
def annulled_data(takings_id):
    try:
        tickets = AnnulledTicket.query.filter_by(takings_id=takings_id).all()

        return {
            "tickets": [
                {
                    "ticket_no": t.ticket_no,
                    "description": t.description,
                    "price": t.price
                }
                for t in tickets
            ]
        }

    except Exception as e:
        print("ANNULLED ERROR:", e)
        return {"error": str(e)}, 500



# ============================================================
# WEEKLY TAKINGS VIEW ROUTE
# ============================================================
@app.route("/weekly-takings-view", methods=["GET"])
def weekly_takings_view():

    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    user = User.query.get(user_id)

    company = Company.query.filter_by(name=user.company).first()
    settings = Settings.query.filter_by(user_id=user.id).first()

    week_map = {
        "Monday": 0, "Mon": 0, "0": 0,
        "Tuesday": 1, "Tue": 1, "1": 1,
        "Wednesday": 2, "Wed": 2, "2": 2,
        "Thursday": 3, "Thu": 3, "3": 3,
        "Friday": 4, "Fri": 4, "4": 4,
        "Saturday": 5, "Sat": 5, "5": 5,
        "Sunday": 6, "Sun": 6, "6": 6
    }

    week_map_reverse = {
        0: "Monday",
        1: "Tuesday",
        2: "Wednesday",
        3: "Thursday",
        4: "Friday",
        5: "Saturday",
        6: "Sunday"
    }

    # ---------------------------------------------------------
    # ⭐ Correct priority order for start day
    # ---------------------------------------------------------
    if settings and settings.week_start_day:
        start_day_value = week_map.get(settings.week_start_day.capitalize(), 0)

    elif user.week_start_override:
        start_day_value = week_map.get(user.week_start_override.capitalize(), 0)

    elif company and company.week_start_day:
        start_day_value = week_map.get(company.week_start_day.capitalize(), 0)

    else:
        start_day_value = 0  # Monday fallback

    # ⭐ You MUST keep this line — your template needs it
    company_start_of_week_name = week_map_reverse.get(start_day_value, "Unknown")



    # ---------------------------------------------------------
    # ⭐ Correct week_start calculation using chosen start day
    # ---------------------------------------------------------
    week_start_str = request.args.get("week_start")

    if week_start_str:
        # TEXT for DB matching
        week_start_db = week_start_str

        # DATE for calculations
        week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date()

    else:
        today = date.today()
        weekday = today.weekday()
        offset = (weekday - start_day_value) % 7

        # DATE for calculations
        week_start = today - timedelta(days=offset)

        # TEXT for DB matching
        week_start_db = week_start.strftime("%Y-%m-%d")

    # DATE calculations (safe)
    week_end = week_start + timedelta(days=7)
    week_start_uk = week_start.strftime("%d/%m/%Y")
    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)


    # ---------------------------------------------------------
    # 3. Fetch entries for the selected week
    # ---------------------------------------------------------

    # Convert week_start (date) into the TEXT format stored in DB
    week_start_str = week_start.strftime("%Y-%m-%d")

    entries = WeeklyTakings.query.filter(
        WeeklyTakings.user_id == user.id,
        WeeklyTakings.entry_date >= week_start,
        WeeklyTakings.entry_date < week_end
    ).order_by(WeeklyTakings.entry_date.asc()).all()



    # ---------------------------------------------------------
    # Build annulled_map for template
    # ---------------------------------------------------------
    annulled_map = {}

    for e in entries:
        annulled_map[e.id] = AnnulledTicket.query.filter_by(takings_id=e.id).all()

    # ---------------------------------------------------------
    # 4. Extract daily breakdown
    # ---------------------------------------------------------
    all_breakdowns = []
    for record in entries:
        breakdown = record.daily_weekly_takings_data or []
        for day_entry in breakdown:
            all_breakdowns.append({
                "day": day_entry.get("day"),
                "bus1": day_entry.get("bus1", 0),
                "bus2": day_entry.get("bus2", 0),
                "bus3": day_entry.get("bus3", 0)
            })

    # ---------------------------------------------------------
    # 5. If no entries, return empty template
    # ---------------------------------------------------------
    if not entries:
        return render_template(
            "weekly_takings_view.html",
            entries=[],
            breakdowns=[],
            week_start=week_start,
            week_start_uk=week_start_uk,
            company_start_of_week=company_start_of_week_name,
            total_full_amount=0,
            total_float_total=0,
            total_tips_total=0,
            total_takings_total=0,
            total_remaining_float=0,
            total_full_amount_diff=0,
            total_final_takings=0,
            total_annulled=0,
            prev_week=prev_week,
            next_week=next_week,
            annulled_map=annulled_map
        )


    # ---------------------------------------------------------
    # 6. Totals
    # ---------------------------------------------------------
    total_full_amount = sum(e.full_amount_total for e in entries)
    total_float_total = sum(e.float_total for e in entries)
    total_tips_total = sum(e.tips_total for e in entries)
    total_takings_total = sum(e.takings_total for e in entries)
    total_remaining_float = sum(e.remaining_float for e in entries)
    total_full_amount_diff = sum(e.full_amount_diff for e in entries)
    total_final_takings = sum(e.final_takings for e in entries)

    total_annulled = 0
    for e in entries:
        tickets = AnnulledTicket.query.filter_by(takings_id=e.id).all()
        for t in tickets:
            total_annulled += float(t.price)

    # ---------------------------------------------------------
    # 7. Render template
    # ---------------------------------------------------------
    return render_template(
        "weekly_takings_view.html",
        entries=entries,
        breakdowns=all_breakdowns,
        week_start=week_start,
        week_start_uk=week_start_uk,
        company_start_of_week=company_start_of_week_name,
        total_full_amount=total_full_amount,
        total_float_total=total_float_total,
        total_tips_total=total_tips_total,
        total_takings_total=total_takings_total,
        total_remaining_float=total_remaining_float,
        total_full_amount_diff=total_full_amount_diff,
        total_final_takings=total_final_takings,
        total_annulled=total_annulled,
        prev_week=prev_week,
        next_week=next_week,
        annulled_map=annulled_map   # ⭐ REQUIRED
    )


    
# =============================================
# WEEKLY TAKINGS DATA CONTAINER
# =============================================   
@app.route("/weekly-takings-data")
def weekly_takings_data():

    week_start_str = request.args.get("week_start")
    day = request.args.get("day")

    week_start = week_start_str   # TEXT

    entry = WeeklyTakings.query.filter_by(
        user_id=session["user_id"],
        week_start=week_start_str,   # TEXT match
        day=day
    ).first()


    return render_template(
        "takings_data.html",
        daily_weekly_takings_data=entry.daily_weekly_takings_data if entry else []
    )


# ============================================================
# WEEKLY TAKINGS EDIT ROUTE
# ============================================================
@app.route("/weekly-takings-edit/<int:entry_id>", methods=["GET", "POST"])
def weekly_takings_edit(entry_id):
    if "user_id" not in session:
        return redirect("/weekly-takings-view")


    entry = WeeklyTakings.query.get_or_404(entry_id)

    # ============================================================
    # POST — SAVE CHANGES
    # ============================================================
    if request.method == "POST":
        data = request.get_json(force=True)

        if not data.get("week_start"):
            return "Missing week_start", 400

        # Update core fields
        entry.week_start = datetime.strptime(data["week_start"], "%Y-%m-%d").date()
        entry.day = data.get("day", "").title()

        # Update count arrays
        entry.full_amount_count = data.get("full_amount_count", [])
        entry.float_amount_count = data.get("float_amount_count", [])
        entry.tips_amount_count = data.get("tips_amount_count", [])
        entry.takings_amount_count = data.get("takings_amount_count", [])

        # Update daily breakdown
        entry.daily_weekly_takings_data = data.get("daily_weekly_takings_data", [])

        # Recalculate total_amount
        total_amount = 0.0
        for item in entry.full_amount_count:
            total_amount += float(item.get("total", 0))
        entry.total_amount = total_amount

        # ============================================================
        # ANNULLED TICKETS — DELETE OLD + INSERT NEW
        # ============================================================
        AnnulledTicket.query.filter_by(takings_id=entry.id).delete()

        annulled_list = data.get("annulled", [])
        for item in annulled_list:
            ticket = AnnulledTicket(
                takings_id=entry.id,
                ticket_no=item.get("ticket_no") or "N/A",
                description=item.get("description"),
                price=float(item.get("price", 0))
            )
            db.session.add(ticket)

        db.session.commit()
        return jsonify({"status": "ok"})

    # ============================================================
    # GET — BUILD DICTIONARY FOR TEMPLATE
    # ============================================================
    entry_dict = {
        "id": entry.id,
        "entry_date": entry.entry_date.strftime("%Y-%m-%d"),
        "week_start": entry.week_start.strftime("%Y-%m-%d"),
        "day": entry.day,

        "full_amount_total": entry.full_amount_total,
        "float_total": entry.float_total,
        "tips_total": entry.tips_total,
        "takings_total": entry.takings_total,
        "remaining_float": entry.remaining_float,
        "full_amount_diff": entry.full_amount_diff,
        "final_takings": entry.final_takings,

        "full_amount_count": entry.full_amount_count or [],
        "float_amount_count": entry.float_amount_count or [],
        "tips_amount_count": entry.tips_amount_count or [],
        "takings_amount_count": entry.takings_amount_count or [],

        "daily_weekly_takings_data": entry.daily_weekly_takings_data or [],

        "annulled": [
            {
                "ticket_no": t.ticket_no,
                "description": t.description,
                "price": t.price
            }
            for t in AnnulledTicket.query.filter_by(takings_id=entry.id).all()
        ]
    }

    return render_template("weekly_takings_edit.html", entry=entry_dict)


# ==========================================
# GET RECORD
# ==========================================
@app.route("/getRecord")
def getRecord():
    record_id = request.args.get("id")
    entry = WeeklyTakings.query.get_or_404(record_id)

    return jsonify({
        "entry": {
            "id": entry.id,
            "week_start": entry.week_start,
            "day": entry.day,
            "full_amount_count": entry.full_amount_count or [],
            "float_amount_count": entry.float_amount_count or [],
            "tips_amount_count": entry.tips_amount_count or [],
            "takings_amount_count": entry.takings_amount_count or [],
            "daily_weekly_takings_data": entry.daily_weekly_takings_data or [],
            "total_amount": entry.total_amount
        }
    })

# ============================================
# WEEKLY TAKINGS DELETE ENTRY
# ============================================

@app.route("/weekly-takings-delete/<int:entry_id>", methods=["POST"])
def weekly_takings_delete(entry_id):
    if "user_id" not in session:
        return redirect("/dashboard")

    entry = WeeklyTakings.query.filter_by(
        id=entry_id,
        user_id=session["user_id"]
    ).first()

    if entry:
        db.session.delete(entry)
        db.session.commit()

    return redirect("/weekly-takings-view")



# =============================================
# TAKINGS MONTHLY
# =============================================

@app.route("/weekly_takings")
def monthly_takings():
    # Week offset for navigation (previous/next week)
    week_offset = int(request.args.get("week_offset", 0))

    # Determine the selected week start (Monday)
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_week = start_of_week + timedelta(weeks=week_offset)

    end_of_week = start_of_week + timedelta(days=6)


    # Query takings for the week
    takings_entries = Takings.query.filter(
        Takings.date >= start_of_week,
        Takings.date <= end_of_week
    ).order_by(Takings.date).all()

    week_data = []

    # Build daily rows
    for entry in takings_entries:
        duty = Duties.query.filter_by(date=entry.date).first()

        week_data.append({
            "date": entry.date.strftime("%d %b %Y"),
            "total_cash": entry.total_cash,
            "float_amount": entry.float_amount,
            "net_cash": entry.net_cash,
            "annulled_total": entry.annulled_total,
            "float_remaining": entry.float_remaining,
            "grand_total": entry.grand_total,
            "earnings": duty.earnings if duty else 0,
            "hourly_rate": duty.hourly_rate if duty else 0
        })

    # Weekly totals
    weekly_totals = {
        "total_cash": sum(d["total_cash"] for d in week_data),
        "float_amount": sum(d["float_amount"] for d in week_data),
        "net_cash": sum(d["net_cash"] for d in week_data),
        "annulled_total": sum(d["annulled_total"] for d in week_data),
        "float_remaining": sum(d["float_remaining"] for d in week_data),
        "grand_total": sum(d["grand_total"] for d in week_data),
        "earnings": sum(d["earnings"] for d in week_data),
        "hourly_rate": 0  # hourly rate is not summed; leave 0 or blank
    }

    week_label = f"{start_of_week.strftime('%d %b %Y')} → {end_of_week.strftime('%d %b %Y')}"

    return render_template(
        "takings_weekly.html",
        week_data=week_data,
        weekly_totals=weekly_totals,
        week_label=week_label,
        week_start=start_of_week
    )

# ============================    
# MONTHLY VIEW DRIVERS LOG 
# ============================
# HELPER FUNCTIONS
# ============================

def get_tax_year_range(year):
    start = date(year, 4, 6)
    end = date(year + 1, 4, 5)
    return start, end

def get_calendar_year_range(year):
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    return start, end

def expand_to_full_weeks(month_start, month_end):
    start_offset = month_start.weekday()          # Monday = 0
    end_offset = 6 - month_end.weekday()          # Sunday = 6

    view_start = month_start - timedelta(days=start_offset)
    view_end = month_end + timedelta(days=end_offset)

    return view_start, view_end

# ============================
# MONTHLY VIEW (REWRITTEN)
# ============================
@app.route("/monthly-view")
@login_required
def monthly_view():
    # Flexible role authorization check (matches weekly view & fixes redirect loop)
    is_authorized = (
        getattr(current_user, 'role_driver', False) or 
        getattr(current_user, 'role_enthusiast', False) or 
        getattr(current_user, 'role_superadmin', False) or
        getattr(current_user, 'level', 0) in [1, 2, 3, 4]
    )
    
    if not is_authorized:
        flash("You do not have permission to view this page.", "danger")
        return redirect("/")

    # Fetch settings safely using current_user.id
    settings = Settings.query.filter_by(user_id=current_user.id).first()
    year_mode = settings.year_mode if settings else "calendar"

    # ============================
    # COMPANY WEEK START
    # ============================
    week_map = {
        "Monday": 0, "Mon": 0, "0": 0,
        "Tuesday": 1, "Tue": 1, "1": 1,
        "Wednesday": 2, "Wed": 2, "2": 2,
        "Thursday": 3, "Thu": 3, "3": 3,
        "Friday": 4, "Fri": 4, "4": 4,
        "Saturday": 5, "Sat": 5, "5": 5,
        "Sunday": 6, "Sun": 6, "6": 6
    }
    week_start_day = week_map.get(str(settings.week_start_day).strip(), 0)

    # ============================
    # SELECTED MONTH/YEAR
    # ============================
    month = int(request.args.get("month", date.today().month))
    year = int(request.args.get("year", date.today().year))

    if year_mode == "tax":
        year_start, year_end = get_tax_year_range(year)
        year_label = str(year)
    else:
        year_start, year_end = get_calendar_year_range(year)
        year_label = str(year)

    month_start = date(year, month, 1)
    month_end = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)

    # ============================
    # PAD MONTH TO FULL COMPANY WEEKS
    # ============================
    start_offset = (month_start.weekday() - week_start_day) % 7
    view_start = month_start - timedelta(days=start_offset)

    end_offset = (week_start_day - month_end.weekday() - 1) % 7
    view_end = month_end + timedelta(days=end_offset)

    # ============================
    # FETCH DUTIES
    # ============================
    all_duties = WeeklyDuty.query.filter_by(user_id=current_user.id).all()
    duties = []

    def add_minutes_to_time(time_str, minutes):
        if not time_str or ":" not in time_str:
            return time_str
        h, m = map(int, time_str.split(":"))
        total = h * 60 + m + minutes
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"

    # Decode + enrich
    for d in all_duties:
        try:
            d_date = datetime.strptime(d.duty_date, "%Y-%m-%d").date()
        except:
            continue
            
        d.duty_date = d_date

        if not (view_start <= d_date <= view_end):
            continue

        # ⭐ MUST BE FIRST — ensures sorting works
        d.date = d_date
        d.date_uk = d.date.strftime("%d/%m/%Y")

        # ⭐ Actual finish logic
        if getattr(d, "actual_finish_time") not in [None, "", "None"]:
            pass
        else:
            d.actual_finish_time = d.finish_time

        # ⭐ Decode lists
        d.breaks_list = json.loads(d.breaks) if d.breaks else []
        d.passenger_list = json.loads(d.passenger_issue) if d.passenger_issue else []
        d.ndw_list = json.loads(d.ndw_entries) if d.ndw_entries else []

        # ============================
        # ENRICH DUTY OBJECT (NEW LOGIC)
        # ============================

        # OSA / QSA
        d.qsa = getattr(d, "outstation_allowance", 0)

        # ⭐ USE DATABASE TOTALS — DO NOT RECALCULATE ANYTHING
        d.total_breaks = d.total_breaks or "00:00"
        d.total_passenger_issue = d.total_passenger_issue or "00:00"
        d.total_lost_time = d.total_lost_time or "00:00"
        d.total_ndw = d.total_ndw or "00:00"
        
        # -----------------------------
        # DELAY TYPE (same as weekly)
        # -----------------------------
        traffic = getattr(d, "traffic_delay_minutes", 0) or 0
        incident = getattr(d, "incident_delay_minutes", 0) or 0

        issue_total_mins = sum(int(p.get("delay", 0)) for p in d.passenger_list)

        if traffic > 0 and incident > 0:
            d.delay_type = (
                f"Traffic ({traffic} mins)<br/>"
                f"Incident ({incident} mins)"
            )
        elif traffic > 0:
            d.delay_type = f"Traffic ({traffic} mins)"
        elif incident > 0:
            d.delay_type = f"Incident ({incident} mins)"
        elif issue_total_mins > 0:
            d.delay_type = f"Passenger ({issue_total_mins} mins)"
        else:
            d.delay_type = "None"

        # ⭐ Add delay minutes for export
        d.delay_minutes = traffic + incident + issue_total_mins


        duties.append(d)

    # ⭐ SORT AFTER LOOP
    duties.sort(key=lambda x: x.date, reverse=True)

    # ============================
    # SORT BY DATE (NEWEST FIRST)
    # ============================
    duties = sorted(duties, key=lambda d: d.date, reverse=True)


    # ============================
    # MONTH SCROLL
    # ============================
    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year

    # ============================
    # SEASONS
    # ============================
    if year_mode == "tax":
        seasons = [f"{y}–{y+1}" for y in range(2025, 2050)]
        selected_season = f"{year}–{year+1}"
    else:
        seasons = list(range(2025, 2050))
        selected_season = year
        

    # -------------------------------------------------------
    # MONTHLY TOTALS (MATCH YOUR ENRICHMENT FIELDS)
    # -------------------------------------------------------

    monthly_totals = {
        "duty": "None",
        "driving": "None",
        "breaks": "None",
        "issue": "None",
        "ndw": "None",
        "delay": "None"
    }

    duty_mins = 0
    driving_mins = 0
    break_mins = 0
    issue_mins = 0
    ndw_mins = 0
    delay_mins = 0

    for d in duties:

        # Total Duty
        if d.total_duty and ":" in d.total_duty:
            h, m = d.total_duty.split(":")
            duty_mins += int(h)*60 + int(m)

        # Driving Time
        if d.driving_time and ":" in d.driving_time:
            h, m = d.driving_time.split(":")
            driving_mins += int(h)*60 + int(m)

        # Breaks
        if d.total_breaks and ":" in d.total_breaks:
            h, m = d.total_breaks.split(":")
            break_mins += int(h)*60 + int(m)

        # Passenger Issues
        if d.total_passenger_issue and ":" in d.total_passenger_issue:
            h, m = d.total_passenger_issue.split(":")
            issue_mins += int(h)*60 + int(m)

        # NDW
        if d.total_ndw and ":" in d.total_ndw:
            h, m = d.total_ndw.split(":")
            ndw_mins += int(h)*60 + int(m)

        # Delay (traffic + incident)
        delay_mins += getattr(d, "traffic_delay_minutes", 0) or 0
        delay_mins += getattr(d, "incident_delay_minutes", 0) or 0


    def mins_to_hhmm(total):
        return f"{total//60:02d}:{total%60:02d}" if total > 0 else "None"

    monthly_totals["duty"] = mins_to_hhmm(duty_mins)
    monthly_totals["driving"] = mins_to_hhmm(driving_mins)
    monthly_totals["breaks"] = mins_to_hhmm(break_mins)
    monthly_totals["issue"] = mins_to_hhmm(issue_mins)
    monthly_totals["ndw"] = mins_to_hhmm(ndw_mins)
    monthly_totals["delay"] = f"{delay_mins} mins" if delay_mins > 0 else "None"

    # ============================
    # EXPORT ENRICHED DUTIES FOR PRINT
    # ============================
    export = []
    for d in duties:
        export.append({
            "date": d.duty_date.strftime("%Y-%m-%d"),
            "duty_number": d.duty_number,
            "osa": d.outstation_allowance or 0,
            "vehicle_type": d.vehicle_type,
            "start_time": d.start_time,
            "finish_time": d.finish_time,
            "actual_finish": d.actual_finish_time,
            "total_duty": d.total_duty,
            "driving_time": d.driving_time,
            "breaks": d.total_breaks,
            "passenger_issues": d.total_passenger_issue,
            "ndw": d.total_ndw,
            "delay_type": d.delay_type,
            "duty_status": d.duty_status,
            "driving_status": d.driving_status,
            "overall_status": d.overall_status
        })

    with open("monthly_export.json", "w") as f:
        json.dump(export, f, indent=4)

    return render_template(
        "monthly_view.html",
        settings=settings,
        monthly_totals=monthly_totals,
        month=month,
        month_name=month_start.strftime("%B"),
        year_label=year_label,
        year=year,
        year_mode=year_mode,
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        seasons=seasons,
        selected_season=selected_season,
        duties=duties
    )

# =================================
# MONTHLY PRINT ROUTE
# =================================

@app.route("/print-monthly")
def print_monthly():

    # 1. Load enriched monthly data exported by /monthly_view
    try:
        with open("monthly_export.json", "r") as f:
            duties = json.load(f)
    except Exception as e:
        return redirect("/monthly-view")

    # 2. PDF output path
    pdf_path = os.path.abspath("monthly_report.pdf")

    # 3. Build PDF document
    doc = BaseDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=40
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height - 90,
        id='normal'
    )

    doc.addPageTemplates([
        PageTemplate(
            id='UK TransportHubMonthly',
            frames=[frame],
            onPage=lambda canvas, doc: draw_header(canvas, doc, title="UK Transport Hub — Monthly Duty Records"),
            onPageEnd=lambda canvas, doc: draw_footer(canvas, doc)
        )
    ])

    elements = [Spacer(1, 0)]

    # ============================
    # TABLE HEADER
    # ============================

    data = [
        ["Date","Duty No.","OSA","Vehicle","Start","Finish","Actual Finish",
         "Total Duty","Driving Time","Breaks","Passenger Issues","NDW",
         "Delay Type","Duty Status","Driving Status","Overall Status"]
    ]

    # ============================
    # CELL STYLES
    # ============================

    cell_style = ParagraphStyle(
        name="CellStyle",
        fontSize=8,
        leading=10,
        wordWrap='CJK'
    )

    # ============================
    # STATUS BADGE
    # ============================

    def status_badge(text):
        if text == "Green":
            return Paragraph(
                "<para align='center'><font color='#FFFFFF'><b>GREEN</b></font></para>",
                ParagraphStyle(name="GreenBadge", backColor=colors.HexColor("#4CAF50"),
                               textColor=colors.white, fontSize=7, leading=8,
                               borderPadding=(2,4,2,4), alignment=1))
        if text == "Amber":
            return Paragraph(
                "<para align='center'><font color='#000000'><b>AMBER</b></font></para>",
                ParagraphStyle(name="AmberBadge", backColor=colors.HexColor("#FFC107"),
                               textColor=colors.black, fontSize=7, leading=8,
                               borderPadding=(2,4,2,4), alignment=1))
        if text == "Red":
            return Paragraph(
                "<para align='center'><font color='#FFFFFF'><b>RED</b></font></para>",
                ParagraphStyle(name="RedBadge", backColor=colors.HexColor("#F44336"),
                               textColor=colors.white, fontSize=7, leading=8,
                               borderPadding=(2,4,2,4), alignment=1))
        return Paragraph(f"<para align='center'>{text}</para>", cell_style)

    # ============================
    # BUILD TABLE ROWS
    # ============================

    for d in duties:

        # Convert times to strings (HH:MM)
        breaks_value = str(d["breaks"])
        passenger_value = str(d["passenger_issues"])
        ndw_value = str(d["ndw"])

        breaks_cell = Paragraph(f"<para align='center'>{breaks_value}</para>", cell_style)
        passenger_cell = Paragraph(f"<para align='center'>{passenger_value}</para>", cell_style)
        ndw_cell = Paragraph(f"<para align='center'>{ndw_value}</para>", cell_style)
        delay_cell = Paragraph(f"<para align='center'>{d['delay_type']}</para>", cell_style)

        data.append([
            d["date"],
            d["duty_number"],
            d["osa"],
            d["vehicle_type"],
            d["start_time"],
            d["finish_time"],
            d["actual_finish"],
            d["total_duty"],
            d["driving_time"],
            breaks_cell,
            passenger_cell,
            ndw_cell,
            delay_cell,
            status_badge(d["duty_status"]),
            status_badge(d["driving_status"]),
            status_badge(d["overall_status"])
        ])

    # ============================
    # TABLE + STYLE
    # ============================

    table = Table(data, colWidths=[
        doc.width * 0.07,   # Date
        doc.width * 0.05,   # Duty No
        doc.width * 0.04,   # OSA
        doc.width * 0.05,   # Vehicle
        doc.width * 0.04,   # Start
        doc.width * 0.04,   # Finish
        doc.width * 0.07,   # Actual Finish Time
        doc.width * 0.06,   # Total Duty
        doc.width * 0.07,   # Driving Time
        doc.width * 0.04,   # Breaks
        doc.width * 0.10,   # Passenger Issues
        doc.width * 0.04,   # NDW
        doc.width * 0.10,   # Delay Type
        doc.width * 0.07,   # Duty Status
        doc.width * 0.08,   # Driving Status
        doc.width * 0.08    # Overall Status
    ], repeatRows=1)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0A2A66")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ALIGN", (0,0), (-1,0), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),

        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BOX", (0,0), (-1,-1), 1, colors.black),

        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))

    elements.append(Spacer(1, 30))
    elements.append(table)

    # ============================
    # BUILD PDF
    # ============================

    doc.build(elements)

    # ============================
    # OPEN PDF + REDIRECT
    # ============================

    try:
        os.startfile(pdf_path)
    except:
        pass

    return redirect("/monthly-view")

# =====================================
# ARTICLES
# =====================================

@app.route("/driver-information")
def driver_information_page():
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()
    return render_template("articles.html", settings=settings)


@app.route("/articles/<int:num>")
def article(num):
    settings = Settings.query.filter_by(user_id=session.get("user_id")).first()

    titles = {
        1: "Welcome to the Drivers Hub",
        2: "Making the Most of the Drivers Hub",
        3: "Passenger Safety & Accessibility",
        4: "Driver Support & Wellbeing",
        5: "Dealing With Difficult Passengers",
        6: "Driver Fatigue & Alertness",
        7: "Managing Stress on the Road",
        8: "Staying Safe on the Road: A Driver’s Practical Guide",
        9: "Profesionalism on the Road: A Driver's Customer Service Guide",
        10: "Respect and Teamwork on the Road",
    }

    published_dates = {
        1: "Wednesday 10 June 2026",
        2: "Thursday 11 June 2026",
        3: "Friday 12 June 2026",
        4: "Saturday 13 June 2026",
        5: "Sunday 14 June 2026",
        6: "Monday 15 June 2026",
        7: "Tuesday 16 June 2026",
        8: "Wednesday 17 June 2026",
        9: "Thursday 18 June 2026",
        10: "Friday 19 June 2026",
    }

    prev_url = f"/articles/{num-1}" if num > 1 else None
    next_url = f"/articles/{num+1}" if num < len(titles) else None

    return render_template(
        f"article_{num}.html",
        settings=settings,
        title=titles[num],
        prev_url=prev_url,
        next_url=next_url,
        published=published_dates[num],
        updated=None
    )

# ============================================================
# PROFILE & EDIT PROFILE ROUTES (Age Rules: 16+ Enthusiast, 18+ Driver)
# ============================================================
@app.route("/profile")
def profile():
    user_id = session.get("user_id")
    if not user_id and current_user.is_authenticated:
        user_id = getattr(current_user, "id", None)

    if not user_id:
        return redirect("/login")

    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        session.clear()
        return redirect("/login")

    user = db.session.get(User, user_id)
    if not user:
        return redirect("/login")

    # =========================================================
    # FORCE SUPERADMIN & ALL ROLE FLAGS ON CURRENT USER (Run once, then delete)
    user.role_superadmin = True
    user.role_admin = True
    user.role_driver = True
    user.role_enthusiast = True
    user.level = 1
    db.session.commit()
    # =========================================================

    # Auto-assign joined date if missing (using today's date)
    if not user.joined_date:
        user.joined_date = datetime.today().strftime("%Y-%m-%d")
        db.session.commit()

    settings = Settings.query.filter_by(user_id=user.id).first()
    if not settings:
        settings = Settings(
            user_id=user.id,
            username=user.username or "User",
            account_type="enthusiast",
            year_mode="calendar",
            theme="light"
        )
        db.session.add(settings)
        db.session.commit()

    # Age and role validation check on load
    if getattr(user, 'date_of_birth', None):
        try:
            dob_date = datetime.strptime(user.date_of_birth, "%Y-%m-%d")
            today = datetime.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
            
            if age < 16:
                flash("Access restricted: You must be at least 16 years old.", "danger")
            elif age < 18:
                user.role_driver = False
                user.role_enthusiast = True
                user.level = 4
                db.session.commit()
        except (ValueError, TypeError):
            pass

    return_to = request.referrer or (url_for("home_target") if 'home_target' in globals() else "/")
    return render_template(
        "profile.html",
        user=user,
        settings=settings,
        return_to=return_to
    )

# -----------------------------------------------------
# EDIT PROFILE ROUTE
# -----------------------------------------------------
from werkzeug.utils import secure_filename
import os

@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    user = User.query.get(user_id)
    if not user:
        return redirect("/login")

    settings = Settings.query.filter_by(user_id=user.id).first()
    return_to = request.referrer or url_for("profile")

    if request.method == "POST":
        data = request.get_json() or request.form

        # Save all basic profile details
        user.first_name = data.get("first_name")
        user.last_name = data.get("last_name")
        user.username = data.get("username")
        user.email = data.get("email")
        user.phone = data.get("phone")
        user.address = data.get("address")
        user.postcode = data.get("postcode")
        user.date_of_birth = data.get("date_of_birth")
        user.company = data.get("company")
        user.region = data.get("region")
        user.depot = data.get("depot")
        user.outstation = data.get("outstation")

        # Ensure joined date exists if missing
        if not user.joined_date:
            user.joined_date = datetime.today().strftime("%Y-%m-%d")

        # Handle Profile Photo Upload
        if "profile_photo" in request.files:
            file = request.files["profile_photo"]
            if file and file.filename != "":
                filename = secure_filename(file.filename)
                upload_folder = os.path.join("static", "uploads")
                os.makedirs(upload_folder, exist_ok=True)
                file_path = os.path.join(upload_folder, filename)
                file.save(file_path)
                user.profile_photo = f"/static/uploads/{filename}"

        # Only apply driver/enthusiast age rules if the user is NOT a Superadmin or Admin
        if not user.role_superadmin and not user.role_admin:
            role_driver_val = data.get("role_driver")
            role_enthusiast_val = data.get("role_enthusiast")

            requested_driver = str(role_driver_val) in ["1", "true", "True", "on"]
            requested_enthusiast = str(role_enthusiast_val) in ["1", "true", "True", "on"]

            if user.date_of_birth:
                try:
                    dob_date = datetime.strptime(user.date_of_birth, "%Y-%m-%d")
                    today = datetime.today()
                    age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
                    
                    if age < 16:
                        flash("You must be at least 16 years old to hold an account.", "danger")
                        user.role_driver = False
                        user.role_enthusiast = False
                    elif age < 18:
                        if requested_driver:
                            flash("Drivers must be 18 or older. Role adjusted to Enthusiast.", "warning")
                        user.role_driver = False
                        user.role_enthusiast = True
                        user.level = 4
                    else:
                        if requested_driver:
                            user.role_driver = True
                            user.role_enthusiast = False
                            user.level = 3  # Driver level
                        elif requested_enthusiast:
                            user.role_driver = False
                            user.role_enthusiast = True
                            user.level = 4  # Enthusiast level
                except (ValueError, TypeError):
                    pass

        db.session.commit()
        
        if request.is_json:
            return jsonify({"message": "Profile updated successfully"}), 200
        flash("Profile updated successfully", "success")
        return redirect(url_for("profile"))

    return render_template(
        "edit_profile.html",
        user=user,
        settings=settings,
        return_to=return_to,
        home_target="home"
    )
# ---------------------------------------------------------
# UPLOAD PROFILE PHOTO
# ---------------------------------------------------------
@app.route("/upload_profile_photo", methods=["POST"])
def upload_profile_photo():
    email = session.get("email")
    if not email:
        return redirect("/login")

    user = User.query.filter_by(email=email).first()
    if not user:
        return redirect("/login")

    if "photo" not in request.files:
        return redirect(url_for("profile"))

    file = request.files["photo"]

    if file.filename == "":
        return redirect(url_for("profile"))

    upload_folder = app.config.get("UPLOAD_FOLDER", "static/profile_photos")
    os.makedirs(upload_folder, exist_ok=True)

    original_name = secure_filename(file.filename)
    ext = original_name.lower().split(".")[-1]

    timestamp = int(datetime.utcnow().timestamp())
    filename = f"{user.id}_{timestamp}.jpg"
    filepath = os.path.join(upload_folder, filename)

    if ext == "heic":
        heif_file = pillow_heif.read_heif(file.read())
        image = Image.frombytes(
            heif_file.mode,
            heif_file.size,
            heif_file.data,
            "raw"
        )
    else:
        image = Image.open(file)

    image.thumbnail((600, 600))
    image.save(filepath, "JPEG", quality=85)

    user.profile_photo = f"/static/profile_photos/{filename}"
    db.session.commit()

    return redirect(url_for("profile"))


# ---------------------------------------------------------
# DELETE PROFILE PHOTO
# ---------------------------------------------------------
@app.route("/delete_profile_photo", methods=["POST"])
def delete_profile_photo():
    email = session.get("email")
    if not email:
        return redirect("/login")

    user = User.query.filter_by(email=email).first()
    if not user:
        return redirect("/login")

    if user.profile_photo and user.profile_photo.startswith("/static/profile_photos/"):
        try:
            os.remove("." + user.profile_photo)
        except Exception:
            pass

    user.profile_photo = None
    db.session.commit()

    return redirect(url_for("profile"))


# ---------------------------------------------------------
# SEND MESSAGE (SQLALCHEMY)
# ---------------------------------------------------------

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json or {}
    sender_id = session.get("user_id")

    if not sender_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    message_text = (data.get("message") or "").strip()
    if not message_text:
        return jsonify({"status": "error", "message": "Message cannot be empty"}), 400

    chat_type = data.get("chat_type")
    if chat_type not in ("company", "all", "private"):
        return jsonify({"status": "error", "message": "Invalid chat type"}), 400

    receiver_id = data.get("receiver_id")
    company_id = data.get("company_id")

    if chat_type == "company":
        if company_id is None:
            company_id = session.get("company_id")

    new_msg = Message(
        chat_type=chat_type,
        sender_id=sender_id,
        receiver_id=receiver_id,
        company_id=company_id,
        message=message_text
    )

    db.session.add(new_msg)
    db.session.commit()

    return jsonify({"status": "ok"})


# ---------------------------------------------------------
# COMPANY CHAT HISTORY (SQLALCHEMY)
# ---------------------------------------------------------

@app.route("/history/company")
def history_company():
    company_id = session.get("company_id")

    if company_id is None:
        return jsonify([])

    messages = (
        db.session.query(Message, User)
        .join(User, Message.sender_id == User.id)
        .filter(Message.chat_type == "company")
        .filter(Message.company_id == company_id)
        .order_by(Message.timestamp.asc())
        .all()
    )

    result = []
    for msg, user in messages:
        sender_name = user.username or f"{user.first_name} {user.last_name}"
        result.append({
            "sender_id": msg.sender_id,
            "sender_name": sender_name,
            "message": msg.message,
            "timestamp": msg.timestamp.replace(tzinfo=pytz.utc)
                                      .astimezone(uk)
                                      .strftime("%A %d %B %Y %H:%M %Z")
        })

    return jsonify(result)


# ---------------------------------------------------------
# ALL DRIVERS CHAT HISTORY (SQLALCHEMY)
# ---------------------------------------------------------

@app.route("/history/all")
def history_all():
    messages = (
        db.session.query(Message, User)
        .join(User, Message.sender_id == User.id)
        .filter(Message.chat_type == "all")
        .order_by(Message.timestamp.asc())
        .all()
    )

    result = []
    for msg, user in messages:
        sender_name = user.username or f"{user.first_name} {user.last_name}"
        result.append({
            "sender_id": msg.sender_id,
            "sender_name": sender_name,
            "message": msg.message,
            "timestamp": msg.timestamp.replace(tzinfo=pytz.utc)
                                      .astimezone(uk)
                                      .strftime("%A %d %B %Y %H:%M %Z")
        })

    return jsonify(result)


# ---------------------------------------------------------
# PRIVATE CHAT HISTORY (SQLALCHEMY)
# ---------------------------------------------------------

@app.route("/history/private/<int:other_id>")
def history_private(other_id):
    user_id = session.get("user_id")

    if not user_id:
        return jsonify([])

    messages = (
        db.session.query(Message, User)
        .join(User, Message.sender_id == User.id)
        .filter(Message.chat_type == "private")
        .filter(
            ((Message.sender_id == user_id) & (Message.receiver_id == other_id)) |
            ((Message.sender_id == other_id) & (Message.receiver_id == user_id))
        )
        .order_by(Message.timestamp.asc())
        .all()
    )

    result = []
    for msg, user in messages:
        sender_name = user.username or f"{user.first_name} {user.last_name}"
        result.append({
            "sender_id": msg.sender_id,
            "sender_name": sender_name,
            "message": msg.message,
            "timestamp": msg.timestamp.replace(tzinfo=pytz.utc)
                                      .astimezone(uk)
                                      .strftime("%A %d %B %Y %H:%M %Z")
        })

    return jsonify(result)


# ---------------------------------------------------------
# USER LIST
# ---------------------------------------------------------

@app.get("/api/users")
def get_users():
    current_id = session.get("user_id")
    users = User.query.filter(User.id != current_id).all()

    return jsonify([
        {"id": u.id, "name": f"{u.first_name} {u.last_name}"}
        for u in users
    ])

@app.route("/home")
def go_home():
    return redirect("/driver_dashboard")
    

with app.app_context():
    db.create_all()

# ------------------------------------------
# DEBUG CHECK ROLES
# ------------------------------------------
@app.route('/debug-check-roles')
def debug_check_roles():
    if not current_user.is_authenticated:
        return "Access denied: Not logged in.", 403
        
    if current_user.email.strip().lower() != 'info@transporthub.uk':
        return f"Access denied: Email mismatch ({current_user.email}).", 403
        
    all_users = User.query.all()
    output = "<h2>User Roles Diagnostic</h2><table border='1' cellpadding='5' style='border-collapse: collapse;'><tr style='background: #f2f2f2;'><th>Email</th><th>Highest Role</th><th>Level</th><th>Superadmin</th><th>Admin</th><th>Driver</th><th>Enthusiast</th></tr>"
    
    for u in all_users:
        output += f"<tr><td>{u.email}</td><td><b>{u.highest_role_display}</b></td><td>{u.level}</td><td>{u.role_superadmin}</td><td>{u.role_admin}</td><td>{u.role_driver}</td><td>{u.role_enthusiast}</td></tr>"
    
    output += "</table>"
    return output

@app.route("/debug-current-user")
@login_required
def debug_current_user():
    return f"""
    Logged in as: {current_user.email}<br>
    Level: {current_user.level} (Type: {type(current_user.level)})<br>
    role_superadmin: {current_user.role_superadmin} (Type: {type(current_user.role_superadmin)})<br>
    role_admin: {current_user.role_admin} (Type: {type(current_user.role_admin)})<br>
    <b>Evaluated role_label: {current_user.role_label}</b>
    """
# ---------------------------------------------------------
# RUN SERVER
# ---------------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
