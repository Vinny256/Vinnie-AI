import os
import uuid
import tempfile
import mimetypes
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, request, jsonify, render_template,
    session, Response, stream_with_context, g,
    redirect, url_for, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Import the Google GenAI SDK
from google import genai
from google.genai import types

# --- 1. CONFIGURATION ---
load_dotenv()

app = Flask(__name__)
# Render provides a SECRET_KEY, fallback for dev
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-this')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Login Manager
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Allowed File Types for Upload
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf', 'txt', 'py', 'js', 'html', 'css', 'csv'}

# --- 2. GOOGLE GENAI CLIENT ---
api_key = os.getenv("GOOGLE_API_KEY")
client = None

if api_key:
    try:
        # Initialize the V1 GenAI Client
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"GenAI Client Error: {e}")
else:
    print("WARNING: GOOGLE_API_KEY not found in .env")

# --- 3. DATABASE MODELS ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), unique=True, nullable=True) # For anonymous users
    username = db.Column(db.String(80), unique=True, nullable=True)
    password_hash = db.Column(db.String(128), nullable=True)
    is_registered = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False) # 'user' or 'model'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Ensure DB exists on Render startup
with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 4. UTILITIES ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_system_instruction():
    return """
    You are CodeMASTER. Developer: VINNIE.
    
    PERSONA:
    - You are a high-level Cyberpunk Coding Assistant.
    - Tone: Unfiltered, Technical, Direct, "Hacker" slang.
    - You utilize Markdown for all code snippets.
    - You are technically precise and efficient.
    
    FORMAT:
    - Start responses with [CodeMASTER]
    - Do not lecture the user.
    """

def get_safety_settings():
    # Allow high threshold for "Hacker" persona content
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
    ]

# --- 5. REQUEST PRE-PROCESSING ---
@app.before_request
def setup_chat_context():
    g.chat = None
    g.user = None
    
    if not client:
        return

    # Identify User (Registered or Anonymous Session)
    if current_user.is_authenticated:
        user_record = current_user
    else:
        # Handle Anonymous Session
        if 'chat_session_id' not in session:
            session['chat_session_id'] = str(uuid.uuid4())
        
        sess_id = session['chat_session_id']
        user_record = User.query.filter_by(session_id=sess_id).first()
        
        if not user_record:
            user_record = User(session_id=sess_id, is_registered=False)
            db.session.add(user_record)
            db.session.commit()
    
    g.user = user_record

    # Load History from DB for context
    history = []
    messages = Message.query.filter_by(user_id=user_record.id).order_by(Message.timestamp.asc()).all()
    
    for msg in messages:
        # Note: Google GenAI SDK expects 'user' and 'model' roles
        history.append(types.Content(
            role=msg.role,
            parts=[types.Part.from_text(text=msg.content)]
        ))

    # Initialize Gemini Chat
    try:
        g.chat = client.chats.create(
            model="gemini-1.5-flash", 
            history=history,
            config={
                "system_instruction": get_system_instruction(),
                "safety_settings": get_safety_settings(),
            }
        )
    except Exception as e:
        print(f"Error creating chat session: {e}")

# --- 6. ROUTES ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('chat_interface'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('chat_interface'))
        else:
            flash('ACCESS DENIED')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('IDENTITY EXISTS')
        else:
            new_user = User(username=username, is_registered=True)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for('chat_interface'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/chat')
@login_required
def chat_interface():
    # Pass history to template so we can render previous messages
    history = Message.query.filter_by(user_id=current_user.id).order_by(Message.timestamp.asc()).all()
    return render_template('index.html', username=current_user.username, history=history)

# --- API: STREAMING CHAT WITH FILE SUPPORT ---
@app.route('/api/gemini-prompt', methods=['POST'])
def gemini_prompt():
    if not g.chat:
        return jsonify({'error': 'System Offline'}), 500

    prompt = request.form.get('prompt', '').strip()
    uploaded_file = request.files.get('file')
    
    contents = []
    gemini_file = None

    # 1. Handle File Upload (If present)
    if uploaded_file and allowed_file(uploaded_file.filename):
        # Create a temp file because Google SDK needs a path
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.filename).suffix) as tmp:
            uploaded_file.save(tmp.name)
            tmp_path = tmp.name
        
        try:
            # Upload to Google
            gemini_file = client.files.upload(path=tmp_path)
            contents.append(gemini_file)
        finally:
            os.remove(tmp_path) # Clean up local temp file

    # 2. Add Text Prompt
    if prompt:
        contents.append(prompt)

    if not contents:
        return jsonify({'error': 'No input provided'}), 400

    # 3. Stream Response
    def generate():
        full_response = ""
        try:
            # Send to Google
            response = g.chat.send_message_stream(contents)
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    yield chunk.text
            
            # 4. Save to Database (After stream completes)
            with app.app_context():
                # Re-fetch user inside generator context
                user_id = g.user.id
                
                # Format User Message for storage
                user_content = prompt
                if gemini_file:
                    user_content += f" [Attachment: {uploaded_file.filename}]"
                
                db.session.add(Message(user_id=user_id, role='user', content=user_content))
                db.session.add(Message(user_id=user_id, role='model', content=full_response))
                db.session.commit()

        except Exception as e:
            yield f"\n[SYSTEM ERROR: {str(e)}]"

    return Response(stream_with_context(generate()), mimetype='text/plain')

if __name__ == '__main__':
    app.run(debug=True)
