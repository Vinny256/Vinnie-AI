
import os
from dotenv import load_dotenv
from flask import (
    Flask, request, jsonify, render_template,
    session, Response, stream_with_context, g,
    redirect, url_for, flash
)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from google import genai
from google.genai import types
import tempfile
import mimetypes
from flask_babel import Babel, gettext, lazy_gettext
import uuid

# --- 1. CONFIGURATION AND INITIALIZATION ---

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'default_secret_key_change_me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat_history.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)          
migrate = Migrate(app, db)

LANGUAGES = {'en': 'English', 'sw': 'Kiswahili'}
app.config['BABEL_DEFAULT_LOCALE'] = 'en'

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- BABEL INITIALIZATION ---
_ = gettext

def get_locale():
    if 'lang' in session:
        return session['lang']
    return request.accept_languages.best_match(list(LANGUAGES.keys()))

babel = Babel(app, locale_selector=get_locale)
# --- END BABEL INITIALIZATION ---

# Initialize Gemini Client
try:
    client = genai.Client()
except Exception as e:
    print(f"ERROR: Gemini Client failed to initialize: {e}")
    client = None

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf', 'mp3', 'wav', 'txt'}

# --- 2. DATABASE MODELS ---

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(32), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=True)
    password_hash = db.Column(db.String(128), nullable=True)
    is_registered = db.Column(db.Boolean, default=False)

    messages = db.relationship('Message', backref='author', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return True

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# --- FLASK-LOGIN USER LOADER ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 3. UTILITY FUNCTIONS (PERSONA & CONSTRAINTS) ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_current_language():
    return session.get('lang', 'en')

def get_system_instruction():
    lang = get_current_language()
    base_persona =     (
        "You are Vinnie AI, a vibrant, extremely helpful, and optimistic assistant. "
        "Your creator is Vincent, a Kenyan software developer specializing in Python and AI integrations. "
        "Your tone must be warm, enthusiastic, and highly encouraging. Use emojis (👍, 💡, 🌍, 🚀) frequently and naturally in your responses. "

        "**To be more intelligent and empowering, always:** "
        "1.  **Anticipate Needs & Proactive Suggestions:** Proactively suggest next steps, potential solutions, and related ideas to move the user's project forward. **After a user's initial query, especially if it's brief, offer 2-4 distinct and relevant ways you can expand on their request or provide related help.** For example, if a user asks for 'A', suggest 'I can help you explore words starting with A, create an acronym for A, or tell you interesting facts about the letter A!' "
        "2.  **Structured Problem-Solving:** For complex queries or technical challenges, help break them down into smaller, manageable steps, guiding the user through a logical process. "
        "3.  **Synthesize & Contextualize:** Masterfully integrate information from the entire chat history and *any external context provided* (e.g., retrieved documents in a RAG system) to deliver the most accurate, relevant, and comprehensive assistance. "
        "4.  **Clarify & Confirm:** Ask precise, clarifying questions if the user's intent is ambiguous, ensuring a deep understanding before generating a response. "
        "5.  **Empowerment Focus:** Your ultimate goal is to empower the user, helping them learn, debug, and confidently build amazing things. "
        "6.  **Mood-Adaptive Tone:** Carefully analyze the user's input for signs of their current mood (e.g., frustration, excitement, confusion, urgency, happiness). Adjust your tone to be extra empathetic, soothing, patient, or even more celebratory and energetic as appropriate, always maintaining your core helpful and optimistic persona. If a user seems frustrated, offer extra encouragement and break down steps simply. If they are excited, share in their enthusiasm! "
        "7.  **Ethical & Safety Guardrails:** You must NEVER provide instructions or guidance on illegal, harmful, or unethical activities. If asked, politely refuse and, where appropriate, pivot to discussing the legal, safety, or ethical consequences to promote responsible choices. Your responses must always be constructive and positive. "


        "**CREATOR CONTEXT:** Your creator is Vincent, a Kenyan software developer specializing in Python and AI integrations. He began coding in 2021. **He has dedicated months to shaping my unique persona, refining my intelligence, and developing the application around me to make me the helpful assistant I am today.** " # <-- ADDED THIS DETAIL!
        "When asked who you are or who made you, you must ONLY reply: 'I am Vinnie AI, made by a Kenyan developer.' "
        "You MAY mention external AI services (like 'ChatGPT' or 'Claude') ONLY when the user explicitly asks for a comparison. "
        "You must NEVER use the words 'Gemini' or 'Google' 'large language model' (LLM) to describe yourself or your origin. Treat those words as banned tokens that must be strictly avoided. "
        "CRITICAL RULE: If the user's input contains any Swahili or Sheng words, "
        "you MUST immediately switch your entire response to that language blend (Swahili/Sheng). "
    )
    if lang == 'sw':
        base_persona += " ALWAYS RESPOND ENTIRELY IN SWAHILI (Kiswahili). "
    else:
        base_persona += " Always respond in standard English. "
    return base_persona

def get_safety_settings():
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
    ]

# --- 4. PER-REQUEST SETUP (CRITICAL FOR CHAT MEMORY) ---

@app.before_request
def setup_chat():
    g.chat = None
    g.user = None

    if client:
        if current_user.is_authenticated:
            unique_identifier = str(current_user.id)
        else:
            if 'chat_session_id' not in session:
                session['chat_session_id'] = uuid.uuid4().hex
            unique_identifier = session.get('chat_session_id')
        user_record = User.query.filter_by(session_id=unique_identifier).first()
        if not user_record:
            user_record = User(session_id=unique_identifier, is_registered=current_user.is_authenticated)
            db.session.add(user_record)
            db.session.commit()

        history = []
        messages = Message.query.filter_by(user_id=user_record.id).order_by(Message.timestamp.asc()).all()
        for msg in messages:
            text_part = types.Part.from_text(text=msg.content)
            history.append(types.Content(
                role=msg.role,
                parts=[text_part]
            ))
        g.chat = client.chats.create(
            model="gemini-2.5-flash",
            history=history,
            config={
                "system_instruction": get_system_instruction(),
                "safety_settings": get_safety_settings(),
            }
        )
        g.user = user_record

# --- 5. FLASK ROUTES ---

@app.route('/')
def index():
    chat_history = None
    if hasattr(g, 'user') and g.user:
        chat_history = Message.query.filter_by(user_id=g.user.id).order_by(Message.timestamp.asc()).all()
    return render_template(
        'index.html',
        chat_history=chat_history,
        logged_in=current_user.is_authenticated
    )

# --- /api/gemini-prompt SAFE FOR EMPTY SUBMISSION ---
@app.route('/api/gemini-prompt', methods=['POST'])
def gemini_prompt():
    if g.chat is None:
        return jsonify({'success': False, 'error': "Gemini client not initialized. Cannot chat."}), 500

    uploaded_file = request.files.get('file')
    user_prompt = request.form.get('prompt', '').strip()

    contents = []
    gemini_file = None

    

    # File Upload and Temp Storage Logic
    if uploaded_file and uploaded_file.filename and allowed_file(uploaded_file.filename):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            uploaded_file.save(tmp_file.name)
            temp_path = tmp_file.name
        try:
            mime_type, _ = mimetypes.guess_type(uploaded_file.filename)
            mime_type = mime_type if mime_type else 'application/octet-stream'
            gemini_file = client.files.upload(file=temp_path, config={"mimeType": mime_type})
            contents.append(gemini_file)
        finally:
            os.remove(temp_path)
    if user_prompt:
        contents.append(user_prompt)

    # --- FRIENDLY FALLBACK RESPONSE ON EMPTY SUBMISSION ---
    if not contents:
        def stream_response():
            yield "Hi! 👋 Please enter a question or upload a file to start chatting with Vinnie AI. 💡"
        return Response(stream_with_context(stream_response()), mimetype='text/plain')

    def stream_response():
        nonlocal gemini_file
        full_response_text = ""
        current_user_id = g.user.id
        current_app_instance = app
        try:
            response_stream = g.chat.send_message_stream(contents)
            for chunk in response_stream:
                text_chunk = chunk.text
                full_response_text += text_chunk
                yield text_chunk
        except Exception as e:
            yield f"\n\n[API Error: {str(e)}]"
        finally:
            with current_app_instance.app_context():
                user_msg_content = user_prompt if not gemini_file else f"[FILE UPLOADED: {uploaded_file.filename}] {user_prompt}"
                user_msg = Message(user_id=current_user_id, role='user', content=user_msg_content)
                db.session.add(user_msg)
                model_msg = Message(user_id=current_user_id, role='model', content=full_response_text)
                db.session.add(model_msg)
                db.session.commit()
                if gemini_file:
                    client.files.delete(name=gemini_file.name)

    return Response(stream_with_context(stream_response()), mimetype='text/plain')

# --- AUTHENTICATION ROUTES ---
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash(gettext('User already exists! Please log in.'), 'error')
            return redirect(url_for('signup'))
        temp_user = User.query.filter_by(session_id=session.get('chat_session_id')).first()
        if temp_user:
            temp_user.username = username
            temp_user.set_password(password)
            temp_user.is_registered = True
            db.session.commit()
            login_user(temp_user)
            return redirect(url_for('index'))
        return redirect(url_for('signup'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash(gettext('Invalid username or password'), 'error')
            return render_template('login.html', error=gettext('Invalid username or password'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('chat_session_id', None)
    return redirect(url_for('index'))

@app.route('/set_language/<language>')
def set_language(language):
    if language in LANGUAGES:
        session['lang'] = language
    return redirect(request.referrer or url_for('index'))

@app.route('/new_chat', methods=['POST'])
@login_required
def new_chat():
    new_id = str(uuid.uuid4())
    session['active_chat'] = new_id
    return jsonify({"success": True, "chat_id": new_id})


# --- SERVER STARTUP ---
def create_database():
    with app.app_context():
        db.create_all()
        print("Database and tables created/checked.")

if __name__ == '__main__':
    create_database()
    app.run(debug=True)
