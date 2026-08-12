from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import os
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import stripe
import qrcode
from io import BytesIO
import base64
import resend

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-this')

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')
PLATFORM_FEE_PERCENTAGE = float(os.getenv('PLATFORM_FEE_PERCENTAGE', 10))

# Database setup - FIXED to use absolute path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "tickets.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# File upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ------------------------
# DATABASE MODELS
# ------------------------

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_seller = db.Column(db.Boolean, default=False)
    stripe_account_id = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    tickets = db.relationship('Ticket', backref='seller', lazy=True)
    purchases = db.relationship('Purchase', backref='buyer', lazy=True)

class Ticket(db.Model):
    __tablename__ = 'ticket'
    
    id = db.Column(db.Integer, primary_key=True)
    event_name = db.Column(db.String(200), nullable=False)
    event_date = db.Column(db.DateTime, nullable=False)
    venue = db.Column(db.String(200), nullable=False)
    seat_section = db.Column(db.String(50))
    seat_row = db.Column(db.String(10))
    seat_number = db.Column(db.String(10))
    price = db.Column(db.Float, nullable=False)
    original_price = db.Column(db.Float)
    proof_image = db.Column(db.String(300))
    is_sold = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    purchase = db.relationship('Purchase', backref='ticket', uselist=False)

class Purchase(db.Model):
    __tablename__ = 'purchase'
    
    id = db.Column(db.Integer, primary_key=True)
    stripe_payment_intent_id = db.Column(db.String(200), unique=True)
    amount = db.Column(db.Float, nullable=False)
    platform_fee = db.Column(db.Float, nullable=False)
    seller_payout = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)

# Create tables
with app.app_context():
    db.drop_all()  # Force fresh start
    db.create_all()
    print("✅ Database created with ALL columns including stripe_account_id!")

# ------------------------
# HELPER FUNCTIONS
# ------------------------

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def calculate_fees(amount):
    """Calculate platform fee and seller payout"""
    platform_fee = (amount * PLATFORM_FEE_PERCENTAGE) / 100
    seller_payout = amount - platform_fee
    return round(platform_fee, 2), round(seller_payout, 2)

def generate_ticket_qr(ticket_id, event_name, venue, date):
    """Generate a QR code that links to the ticket verification page"""
    # Create a verification URL (change to your domain when deployed)
    base_url = "http://127.0.0.1:5000"  # Local development
    # base_url = "https://tickethub.onrender.com"  # Production
    
    verify_url = f"{base_url}/verify_ticket/{ticket_id}"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(verify_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="#6C3CE1", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str

# ------------------------
# LOAD USER FOR LOGIN
# ------------------------

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ------------------------
# ROUTES
# ------------------------

@app.route('/')
def home():
    tickets = Ticket.query.filter_by(is_sold=False).order_by(Ticket.created_at.desc()).all()
    return render_template('home.html', tickets=tickets)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.password == password:
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        is_seller = request.form.get('is_seller') == 'yes'
        
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered!', 'error')
            return redirect(url_for('register'))
        
        existing_username = User.query.filter_by(username=username).first()
        if existing_username:
            flash('Username already taken!', 'error')
            return redirect(url_for('register'))
        
        # Create new user
        new_user = User(
            username=username,
            email=email,
            password=password,
            is_seller=is_seller
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_seller:
        my_tickets = Ticket.query.filter_by(seller_id=current_user.id).all()
    else:
        my_tickets = []
    
    # Get purchase history for buyers
    my_purchases = Purchase.query.filter_by(buyer_id=current_user.id).all()
    
    return render_template('dashboard.html', tickets=my_tickets, purchases=my_purchases)

@app.route('/list_ticket', methods=['GET', 'POST'])
@login_required
def list_ticket():
    # Only sellers can list tickets
    if not current_user.is_seller:
        flash('You need to be a seller to list tickets!', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        # Get form data
        event_name = request.form.get('event_name')
        event_date_str = request.form.get('event_date')
        venue = request.form.get('venue')
        seat_section = request.form.get('seat_section')
        seat_row = request.form.get('seat_row')
        seat_number = request.form.get('seat_number')
        price = request.form.get('price')
        original_price = request.form.get('original_price')
        
        # Validate required fields
        if not all([event_name, event_date_str, venue, price]):
            flash('Please fill in all required fields (Event Name, Date, Venue, and Price).', 'error')
            return render_template('list_ticket.html')
        
        try:
            # Parse date
            event_date = datetime.strptime(event_date_str, '%Y-%m-%dT%H:%M')
            price_float = float(price)
            original_price_float = float(original_price) if original_price else None
        except ValueError as e:
            flash(f'Invalid date or price format: {str(e)}', 'error')
            return render_template('list_ticket.html')
        
        # Handle file upload
        proof_filename = None
        if 'proof_image' in request.files:
            file = request.files['proof_image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                proof_filename = f"{timestamp}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], proof_filename))
        
        # Create new ticket
        new_ticket = Ticket(
            event_name=event_name,
            event_date=event_date,
            venue=venue,
            seat_section=seat_section,
            seat_row=seat_row,
            seat_number=seat_number,
            price=price_float,
            original_price=original_price_float,
            proof_image=proof_filename,
            seller_id=current_user.id
        )
        
        db.session.add(new_ticket)
        db.session.commit()
        
        flash('Ticket listed successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('list_ticket.html')

@app.route('/buy_ticket/<int:ticket_id>')
@login_required
def buy_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    
    # Check if ticket is already sold
    if ticket.is_sold:
        flash('This ticket has already been sold!', 'error')
        return redirect(url_for('home'))
    
    # Check if user is trying to buy their own ticket
    if ticket.seller_id == current_user.id:
        flash('You cannot buy your own ticket!', 'error')
        return redirect(url_for('home'))
    
    # Calculate fees
    platform_fee, seller_payout = calculate_fees(ticket.price)
    
    # Create Stripe Checkout Session
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': ticket.event_name,
                        'description': f"{ticket.venue} - {ticket.event_date.strftime('%B %d, %Y')}",
                    },
                    'unit_amount': int(ticket.price * 100),  # Stripe uses cents
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', ticket_id=ticket.id, _external=True),
            cancel_url=url_for('home', _external=True),
            metadata={
                'ticket_id': ticket.id,
                'seller_id': ticket.seller_id,
                'platform_fee': platform_fee,
                'seller_payout': seller_payout
            }
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f'Payment error: {str(e)}', 'error')
        return redirect(url_for('home'))

@app.route('/payment_success/<int:ticket_id>')
@login_required
def payment_success(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    
    if ticket.is_sold:
        flash('This ticket has already been sold!', 'error')
        return redirect(url_for('home'))
    
    platform_fee, seller_payout = calculate_fees(ticket.price)
    
    ticket.is_sold = True
    
    purchase = Purchase(
        stripe_payment_intent_id=f"test_{datetime.now().timestamp()}",
        amount=ticket.price,
        platform_fee=platform_fee,
        seller_payout=seller_payout,
        status='completed',
        buyer_id=current_user.id,
        ticket_id=ticket.id
    )
    
    db.session.add(purchase)
    db.session.commit()
    
    # ===== SEND EMAIL TO BUYER USING RESEND =====
    try:
        # Set Resend API key
        resend.api_key = os.environ.get("RESEND_API_KEY")
        
        qr_img = generate_ticket_qr(
            ticket.id,
            ticket.event_name,
            ticket.venue,
            ticket.event_date.strftime('%B %d, %Y at %I:%M %p')
        )
        
        # Build seat info
        if ticket.seat_section:
            seat_info = f"Section {ticket.seat_section}, Row {ticket.seat_row}, Seat {ticket.seat_number}"
        else:
            seat_info = "N/A"
        
        params = {
            "from": "TicketHub <onboarding@resend.dev>",  # Testing domain
            "to": [current_user.email],
            "subject": f"🎟️ Your Ticket for {ticket.event_name}",
            "html": f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #6C3CE1, #8B5CF6); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                    <h1 style="color: white; margin: 0;">🎟️ TicketHub</h1>
                    <p style="color: rgba(255,255,255,0.8); margin: 5px 0 0 0;">Your ticket is confirmed!</p>
                </div>
                <div style="padding: 30px; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 10px 10px;">
                    <h2 style="color: #1a202c; margin-top: 0;">{ticket.event_name}</h2>
                    <p><strong>📍 Venue:</strong> {ticket.venue}</p>
                    <p><strong>📅 Date:</strong> {ticket.event_date.strftime('%B %d, %Y at %I:%M %p')}</p>
                    <p><strong>💺 Seat:</strong> {seat_info}</p>
                    <p><strong>💰 Price:</strong> ${purchase.amount:.2f}</p>
                    <p><strong>📦 Order #:</strong> {purchase.id}</p>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <p style="font-size: 0.9rem; color: #718096;">Scan this QR code at the event entrance</p>
                        <img src="data:image/png;base64,{qr_img}" style="max-width: 200px; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px;">
                    </div>
                    
                    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                    
                    <p style="font-size: 0.8rem; color: #718096; text-align: center;">
                        This is a digital ticket. Please present this email or the QR code at the event entrance.
                        <br><br>
                        © 2026 TicketHub. All rights reserved.
                    </p>
                </div>
            </body>
            </html>
            """
        }
        
        email_response = resend.Emails.send(params)
        print(f"✅ Email sent to {current_user.email}")
        print(f"📧 Resend response: {email_response}")
        
    except Exception as e:
        print(f"⚠️ Email failed: {e}")
        # Don't fail the purchase if email fails
    
    flash('🎉 Ticket purchased successfully! Check your email for the ticket.', 'success')
    return render_template('purchase_success.html', ticket=ticket, purchase=purchase)

@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    # This will handle real payment confirmations
    return jsonify({'status': 'success'})

@app.route('/verify_ticket/<int:ticket_id>')
def verify_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    
    if ticket.is_sold:
        purchase = Purchase.query.filter_by(ticket_id=ticket.id).first()
        buyer_name = purchase.buyer.username if purchase else "Unknown"
        status = "✅ VALID TICKET"
        status_color = "#34D399"
    else:
        buyer_name = "Not yet purchased"
        status = "❌ INVALID TICKET"
        status_color = "#F87171"
    
    return render_template('verify_ticket.html', 
                         ticket=ticket, 
                         status=status, 
                         status_color=status_color,
                         buyer_name=buyer_name)

...
@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_seller:
        my_tickets = Ticket.query.filter_by(seller_id=current_user.id).all()
        
        # Calculate total earnings from sold tickets
        sold_tickets = Ticket.query.filter_by(seller_id=current_user.id, is_sold=True).all()
        total_earnings = sum(ticket.price for ticket in sold_tickets)
        total_sales = len(sold_tickets)
    else:
        my_tickets = []
        total_earnings = 0
        total_sales = 0
    
    my_purchases = Purchase.query.filter_by(buyer_id=current_user.id).all()
    
    return render_template('dashboard.html', 
                         tickets=my_tickets, 
                         purchases=my_purchases,
                         total_earnings=total_earnings,
                         total_sales=total_sales)



# ------------------------
# RUN THE APP
# ------------------------

if __name__ == '__main__':
    app.run(debug=True)

    