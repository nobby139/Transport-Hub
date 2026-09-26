from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from flask_login import UserMixin
import pytz

db = SQLAlchemy()

# ---------------------------------------------------------
# USER MODEL (FINAL + CORRECT)
# ---------------------------------------------------------
class User(db.Model, UserMixin):
    __tablename__ = "users"   # make sure this matches your actual table name in SQLite

    id = db.Column(db.Integer, primary_key=True)

    # BASIC INFO
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(100), nullable=True)

    email = db.Column(db.String(120), nullable=False, unique=True)
    password = db.Column(db.String(200), nullable=False)

    # ROLE LEVEL SYSTEM (ENFORCED)
    level = db.Column(db.Integer, default=4)

    # MULTI‑ROLE FLAGS
    role_superadmin = db.Column(db.Boolean, default=False)
    role_admin = db.Column(db.Boolean, default=False)
    role_driver = db.Column(db.Boolean, default=False)
    role_enthusiast = db.Column(db.Boolean, default=True)

    role_change_message = db.Column(db.String(255))
    downgrade_reason = db.Column(db.String(255))

    deleted = db.Column(db.Boolean, default=False)

    # SUSPENSION
    suspended_until = db.Column(db.DateTime, nullable=True)
    suspension_reason = db.Column(db.String(255), nullable=True)

    # DATES
    date_of_birth = db.Column(db.String(10), nullable=True)
    joined_date = db.Column(db.String(10), nullable=True)

    # CONTACT INFO
    address = db.Column(db.String(200), nullable=True)
    postcode = db.Column(db.String(20), nullable=True)
    phone = db.Column(db.String(20), nullable=True)

    # DRIVER INFO
    psv_number = db.Column(db.String(50), nullable=True)
    licence_number = db.Column(db.String(50), nullable=True)

    # COMPANY + REGION + DEPOT
    company = db.Column(db.String(120), nullable=True)
    region = db.Column(db.String(120), nullable=True)
    depot = db.Column(db.String(120), nullable=True)
    outstation = db.Column(db.String(120), nullable=True)

    # WEEK START OVERRIDE
    week_start_override = db.Column(db.String(10), default=None)

    # PROFILE PHOTO
    profile_photo = db.Column(db.String(300), nullable=True)
    
    # 2FA FIELDS
    totp_secret = db.Column(db.String(32), nullable=True)
    is_2fa_enabled = db.Column(db.Boolean, default=False, nullable=False)

    # AUTO‑CALCULATED AGE
    @property
    def age(self):
        if not self.date_of_birth:
            return None
        dob = datetime.strptime(self.date_of_birth, "%Y-%m-%d")
        today = datetime.today()
        return today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )

    # AUTO ROLE LABEL (NEW)
    @property
    def role_label(self):
        # If boolean flags are set, they override numeric level
        if self.role_superadmin:
            return "Superadmin"
        if self.role_admin:
            return "Admin"
        if self.role_driver:
            return "Driver"
        if self.role_enthusiast:
            return "Enthusiast"

        # Fallback to numeric level
        mapping = {
            1: "Superadmin",
            2: "Admin",
            3: "Enthusiast",
            4: "Driver"
        }
        return mapping.get(self.level, "Unknown")
 
 
# =========================================================
# ROLE CHANGE HISTORY
# =========================================================
class RoleChangeHistory(db.Model):
    __tablename__ = "role_change_history"

    id = db.Column(db.Integer, primary_key=True)

    # Timestamp
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Which user was changed
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Which admin performed the change
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # upgrade / downgrade / suspend / delete
    change_type = db.Column(db.String(50), nullable=False)

    # Enthusiast / Driver / Both / Admin / Superadmin
    old_role = db.Column(db.String(50), nullable=True)
    new_role = db.Column(db.String(50), nullable=True)

    # Reason selected from your dropdown
    reason = db.Column(db.String(255), nullable=True)

    # Extra info (e.g., suspension length)
    extra_info = db.Column(db.String(255), nullable=True)

    # Relationships
    user = db.relationship("User", foreign_keys=[user_id], backref="role_change_history")
    admin = db.relationship("User", foreign_keys=[admin_id], backref="role_changes_made")

# ---------------------------------------------------------
# SITE STATS MODEL
# ---------------------------------------------------------
class SiteStats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_count = db.Column(db.Integer, default=0)
    home_visit_count = db.Column(db.Integer, default=0)
    deleted_count = db.Column(db.Integer, default=0)
    lifted_count = db.Column(db.Integer, default=0)
    
# ---------------------------------------------------------
# COMPANY MODEL
# ---------------------------------------------------------
class Company(db.Model):
    __tablename__ = "company"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    # Correct column (already exists in your DB)
    week_start_day = db.Column(db.String(10), nullable=False)

    # Old column (still exists, but you will stop using it)
    week_start = db.Column(db.String(10), default="monday")

# ---------------------------------------------------------
# PASSWORD RESET TOKEN
# ---------------------------------------------------------
class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(200), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

# ---------------------------------------------------------
# LOGIN CODE
# ---------------------------------------------------------
class LoginCode(db.Model):
    __tablename__ = "login_codes"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(4), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

# =========================================================
# PLATFORM MESSAGES MODEL
# =========================================================
class UserMessages(db.Model):
    __tablename__ = "user_messages"

    id = db.Column(db.Integer, primary_key=True)

    # Who sent the message (Admin or User)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Who receives the message
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Message title
    title = db.Column(db.String(200), nullable=False)

    # Message body
    body = db.Column(db.Text, nullable=False)

    # Message type:
    # admin_to_user, user_to_admin,
    # system_upgrade, system_downgrade,
    # system_suspend, system_lift_suspend
    type = db.Column(db.String(50), nullable=False)

    # Should this message appear as a popup?
    popup_required = db.Column(db.Boolean, default=False)

    # Timestamp
    created_at = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp()
    )

    # Read/dismiss states
    read = db.Column(db.Boolean, default=False)
    dismissed = db.Column(db.Boolean, default=False)

    # Relationships
    sender = db.relationship("User", foreign_keys=[sender_id], backref="sent_user_messages")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_user_messages")
    
    # ⭐ UK time conversion (BST/GMT automatic)
    def local_time(self):
        uk = pytz.timezone("Europe/London")
        return self.created_at.replace(tzinfo=pytz.utc).astimezone(uk)

# ---------------------------------------------------------
# MESSAGE MODEL
# ---------------------------------------------------------
class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)

    chat_type = db.Column(db.String(20), nullable=False)      # company, all, private

    # Foreign keys
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

    message = db.Column(db.Text, nullable=False)

    timestamp = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp()
    )

    # Relationships
    sender = db.relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_messages")
    company = db.relationship("Company", backref="messages")

# ---------------------------------------------------------
# WEEKLY DUTY MODEL (FULL VERSION - FIXED)
# ---------------------------------------------------------
class WeeklyDuty(db.Model):
    __tablename__ = "weekly_duties"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    duty_date = db.Column(db.String(20))
    start_of_week = db.Column(db.String(20))
    duty_number = db.Column(db.String(20))
    vehicle_type = db.Column(db.String(20))
    start_time = db.Column(db.String(20))
    finish_time = db.Column(db.String(20))
    actual_finish_time = db.Column(db.String(20))
    total_duty = db.Column(db.String(20))
    driving_time = db.Column(db.String(20))
    breaks = db.Column(db.String(20))
    total_breaks = db.Column(db.String(20))
    traffic_delay = db.Column(db.String(20))
    traffic_delay_minutes = db.Column(db.String(20))
    incident_delay = db.Column(db.String(20))
    incident_delay_minutes = db.Column(db.String(20))
    delay_type = db.Column(db.String(50))
    passenger_issue = db.Column(db.String(20))
    total_passenger_issue = db.Column(db.String(20))
    total_lost_time = db.Column(db.String(20))
    ndw_entries = db.Column(db.String(20))
    total_ndw = db.Column(db.String(20))
    duty_status = db.Column(db.String(20))
    driving_status = db.Column(db.String(20))
    overall_status = db.Column(db.String(20))
    outstation_allowance = db.Column(db.String(20))
    
# ---------------------------------------------------------
# DUTY BUS MODEL (FOR MULTIPLE VEHICLES PER SHIFT)
# ---------------------------------------------------------
class DutyBus(db.Model):
    __tablename__ = 'duty_bus'
    
    id = db.Column(db.Integer, primary_key=True)
    duty_id = db.Column(db.Integer, db.ForeignKey('weekly_duties.id'), nullable=False)
    bus_no = db.Column(db.String(50), nullable=False)
    reg_plate = db.Column(db.String(50), nullable=False)
    time_from = db.Column(db.String(20), nullable=False)
    time_to = db.Column(db.String(20), nullable=False)

    # Relationship back to WeeklyDuty so we can easily query all buses for a shift
    duty = db.relationship('WeeklyDuty', backref=db.backref('buses', cascade='all, delete-orphan'))    

# ---------------------------------------------------------
# SETTINGS MODEL (GLOBAL SITE SETTINGS)
# ---------------------------------------------------------
class Settings(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)

    # Link settings to a user
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    # Username for visibility in DB Browser
    username = db.Column(db.String(100), nullable=False)

    # Account Type
    account_type = db.Column(db.String(20), default="driver")

    # Theme: light or dark
    theme = db.Column(db.String(10), default="light")

    # Year mode: calendar or tax
    year_mode = db.Column(db.String(10), default="calendar")

    # Preset selected (6-apr, 1-apr, 1-jan, custom)
    tax_year_preset = db.Column(db.String(20), default="6-apr")

    # Custom date if preset = custom
    custom_tax_year_start = db.Column(db.String(20), nullable=True)

    # Week start day
    week_start_day = db.Column(db.String(10), nullable=False)

    # Relationship to User
    user = db.relationship("User", backref="settings", uselist=False)

# ---------------------------------------------------------
# SYSTEM SETTINGS MODEL 
# --------------------------------------------------------- 
class SystemSettings(db.Model):
    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)

    # Session timeout (minutes)
    session_timeout = db.Column(db.Integer, default=30)

    # Superadmin protection
    superadmin_protection = db.Column(db.Boolean, default=True)

    # Default suspension length (days)
    default_suspension_length = db.Column(db.Integer, default=7)

    # Log retention period (days)
    log_retention = db.Column(db.Integer, default=30)

    # Module toggles
    module_takings = db.Column(db.Boolean, default=True)
    module_incidents = db.Column(db.Boolean, default=True)
    module_logs = db.Column(db.Boolean, default=True)
    module_user_management = db.Column(db.Boolean, default=True)
    module_company_management = db.Column(db.Boolean, default=True)
    module_outstations = db.Column(db.Boolean, default=True)
    module_regions = db.Column(db.Boolean, default=True)

    # System-wide notifications
    notify_email = db.Column(db.Boolean, default=True)
    notify_suspension = db.Column(db.Boolean, default=True)
    notify_incident = db.Column(db.Boolean, default=True)
    notify_admin_action = db.Column(db.Boolean, default=True)

    # Default assignment
    default_company = db.Column(db.String(100), default="")
    default_region = db.Column(db.String(100), default="")
    default_depot = db.Column(db.String(100), default="")

    # Global theme
    theme = db.Column(db.String(20), default="light")


# ---------------------------------------------------------
# PAY RATE MODEL
# ---------------------------------------------------------
class PayRate(db.Model):
    __tablename__ = "pay_rates"

    id = db.Column(db.Integer, primary_key=True)
    effective_from = db.Column(db.Date, nullable=False)

    # Standard rates
    mon_fri_rate = db.Column(db.Float, nullable=True)
    sat_rate = db.Column(db.Float, nullable=True)
    sun_rate = db.Column(db.Float, nullable=True)
    bank_hol_rate = db.Column(db.Float, nullable=True)

    # Late duties
    late_week_rate = db.Column(db.Float, nullable=True)
    late_sat_rate = db.Column(db.Float, nullable=True)
    late_sun_rate = db.Column(db.Float, nullable=True)
    night_rate = db.Column(db.Float, nullable=True)

    # Special days
    christmas_rate = db.Column(db.Float, nullable=True)
    boxingday_rate = db.Column(db.Float, nullable=True)
    newyear_rate = db.Column(db.Float, nullable=True)
    goodfriday_rate = db.Column(db.Float, nullable=True)

# ==================================
# SPECIAL DAYS
# ==================================
class SpecialDay(db.Model):
    __tablename__ = "special_days"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    name = db.Column(db.String(50), nullable=False)
    rate = db.Column(db.Float, nullable=False)

# ---------------------------------------------------------
#       DAILY ENTRIES
# ---------------------------------------------------------
class DailyEntries(db.Model):
    __tablename__ = "daily_entries"

    id = db.Column(db.Integer, primary_key=True)

    # Foreign key back to User
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    entry_date = db.Column(db.Date)

    # NEW: support multiple buses
    bus1 = db.Column(db.Float, default=0.0)
    bus2 = db.Column(db.Float, default=0.0)
    bus3 = db.Column(db.Float, default=0.0)

    # Relationship to User
    user = db.relationship("User", backref="daily_entries")


    # … other fields …
# ---------------------------------------------------------
#       ANNULLED ENTRIES
# ---------------------------------------------------------
class AnnulledTicket(db.Model):
    __tablename__ = "annulled_ticket"

    id = db.Column(db.Integer, primary_key=True)

    # Foreign key back to WeeklyTakings
    takings_id = db.Column(
        db.Integer,
        db.ForeignKey("weekly_takings.id", ondelete="CASCADE"),
        nullable=False
    )

    ticket_no = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=True)
    price = db.Column(db.Float, nullable=False)

 
# ==============================================================
# WEEKLY TAKINGS
# ==============================================================

class WeeklyTakings(db.Model):
    __tablename__ = "weekly_takings"

    id = db.Column(db.Integer, primary_key=True)

    # Metadata
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    entry_date = db.Column(db.Date, nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    day = db.Column(db.String(20), nullable=False)

    daily_weekly_takings_data = db.Column(db.JSON, default=[])

    full_amount_total = db.Column(db.Float, default=0.0)
    float_total = db.Column(db.Float, default=0.0)
    tips_total = db.Column(db.Float, default=0.0)
    takings_total = db.Column(db.Float, default=0.0)
    remaining_float = db.Column(db.Float, default=0.0)
    full_amount_diff = db.Column(db.Float, default=0.0)
    final_takings = db.Column(db.Float, default=0.0)

    full_amount_count = db.Column(db.JSON, default=[])
    float_amount_count = db.Column(db.JSON, default=[])
    tips_amount_count = db.Column(db.JSON, default=[])
    takings_amount_count = db.Column(db.JSON, default=[])

    # Relationship to annulled tickets
    annulled_ticket_rows = db.relationship(
        "AnnulledTicket",
        backref="weekly_takings",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy=True
    )



    # ============================================================
    # JSON-SERIALISABLE DICTIONARY FOR EDIT PAGE
    # ============================================================
    def to_dict(self):
        return {
            "id": self.id,
            "entry_date": str(self.entry_date),
            "week_start": self.week_start.strftime("%d/%m/%Y"),
            "day": self.day,
            "full_amount_total": self.full_amount_total,
            "float_total": self.float_total,
            "tips_total": self.tips_total,
            "takings_total": self.takings_total,
            "remaining_float": self.remaining_float,
            "full_amount_diff": self.full_amount_diff,
            "final_takings": self.final_takings,

            # ⭐ FIX THIS TOO — remove self.annulled ⭐
            "annulledTickets": [
                {
                    "ticket_no": t.ticket_no,
                    "description": t.description,
                    "price": t.price
                }
                for t in self.annulled_ticket_rows
            ],

            "full_amount_count": self.full_amount_count or [],
            "float_amount_count": self.float_amount_count or [],
            "tips_amount_count": self.tips_amount_count or [],
            "takings_amount_count": self.takings_amount_count or [],
            "daily_weekly_takings_data": self.daily_weekly_takings_data or []
        }

# ==============================================================
# TAKINGS AND MONEY COUNT
# ==============================================================

class Takings(db.Model):
    __tablename__ = "takings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Date links takings → duties
    date = db.Column(db.Date, nullable=False, unique=True)

    # Denominations
    p50 = db.Column(db.Integer, default=0)     # £50
    p20 = db.Column(db.Integer, default=0)     # £20
    p10 = db.Column(db.Integer, default=0)     # £10
    p5  = db.Column(db.Integer, default=0)     # £5
    p2  = db.Column(db.Integer, default=0)     # £2
    p1  = db.Column(db.Integer, default=0)     # £1
    p050 = db.Column(db.Integer, default=0)    # 50p
    p020 = db.Column(db.Integer, default=0)    # 20p
    p010 = db.Column(db.Integer, default=0)    # 10p
    p005 = db.Column(db.Integer, default=0)    # 5p
    p002 = db.Column(db.Integer, default=0)    # 2p
    p001 = db.Column(db.Integer, default=0)    # 1p

    # Float + totals
    float_amount = db.Column(db.Float, default=0.0)
    total_cash = db.Column(db.Float, default=0.0)
    net_cash = db.Column(db.Float, default=0.0)

    # Annulled tickets
    annulled_total = db.Column(db.Float, default=0.0)

    # Float Remaining + Grand Total
    float_remaining = db.Column(db.Float, default=0.0)
    grand_total = db.Column(db.Float, default=0.0)

    # Relationship
    user = db.relationship("User", backref="takings")

# ===================================================
#   ENTHUSIAST HUB
# ===================================================
class Enthusiast(db.Model):
    __tablename__ = "enthusiasts"

    id = db.Column(db.Integer, primary_key=True)
    
    # Enthusiast identity
    username = db.Column(db.String(50), nullable=False)
    
    # Bus & coach data
    operator = db.Column(db.String(100))          # e.g. First Bus, Stagecoach
    vehicle_number = db.Column(db.String(50))     # fleet number
    registration = db.Column(db.String(20))       # license plate
    make_model = db.Column(db.String(100))        # e.g. Volvo B7TL, Plaxton Panther
    depot = db.Column(db.String(100))             # depot or garage
    route_number = db.Column(db.String(20))       # service route
    route_destination = db.Column(db.String(200)) # Route Destination
    notes = db.Column(db.Text)                    # enthusiast notes (rare find, livery, etc.)

    # Meta
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    spotted_at = db.Column(db.DateTime, default=datetime.utcnow)
    photos_uploaded = db.Column(db.Integer, default=0)

class EnthusiastPhoto(db.Model):
    __tablename__ = "enthusiast_photos"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    enthusiast_id = db.Column(db.Integer, db.ForeignKey("enthusiasts.id"), nullable=False)
    enthusiast = db.relationship("Enthusiast", backref="photos")

