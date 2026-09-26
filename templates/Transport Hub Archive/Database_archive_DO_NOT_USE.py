from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ---------------------------------------------------------
# USER MODEL
# ---------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # BASIC INFO
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(100), nullable=True)

    email = db.Column(db.String(120), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # driver or enthusiast

    # DATES
    date_of_birth = db.Column(db.String(10), nullable=True)   # YYYY-MM-DD
    joined_date = db.Column(db.String(10), nullable=True)     # YYYY-MM-DD

    # CONTACT INFO
    address = db.Column(db.String(200), nullable=True)
    postcode = db.Column(db.String(20), nullable=True)
    phone = db.Column(db.String(20), nullable=True)

    # DRIVER INFO
    psv_number = db.Column(db.String(50), nullable=True)
    licence_number = db.Column(db.String(50), nullable=True)

    # COMPANY (TEXT FIELD)
    company = db.Column(db.String(120), nullable=True)

    # PROFILE PHOTO
    profile_photo = db.Column(db.String(300), nullable=True)

    # UNIQUE RULE: one email per role
    __table_args__ = (
        db.UniqueConstraint('email', 'role', name='unique_email_role'),
    )

    # AUTO‑CALCULATED AGE
    @property
    def age(self):
        if not self.date_of_birth:
            return None

        from datetime import datetime
        dob = datetime.strptime(self.date_of_birth, "%Y-%m-%d")
        today = datetime.today()

        return today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )


# ---------------------------------------------------------
# COMPANY MODEL
# ---------------------------------------------------------
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)


# ---------------------------------------------------------
# PASSWORD RESET TOKEN
# ---------------------------------------------------------
class PasswordResetToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(200), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)


# ---------------------------------------------------------
# LOGIN CODE
# ---------------------------------------------------------
class LoginCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(4), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)


# ---------------------------------------------------------
# MESSAGE MODEL (MATCHES YOUR RAW SQL TABLE)
# ---------------------------------------------------------
class Message(db.Model):
    __tablename__ = "messages"  # IMPORTANT: matches your SQLite table name

    id = db.Column(db.Integer, primary_key=True)

    chat_type = db.Column(db.String(20), nullable=False)      # company, all, private
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=True)        # private chat only
    company_id = db.Column(db.Integer, nullable=True)         # company chat only

    message = db.Column(db.Text, nullable=False)

    # Matches: timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    timestamp = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp()
    )
