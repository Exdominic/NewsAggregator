from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
import requests, os, re
from bson.objectid import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
from itsdangerous import URLSafeTimedSerializer
from scipy import stats
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from datetime import datetime, timezone, UTC
import matplotlib.pyplot as plt
from reportlab.platypus import Image, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from flask_socketio import SocketIO
from flask_socketio import join_room, leave_room, emit






# ---------------- INIT ----------------
load_dotenv()
app = Flask(__name__)
app.secret_key = "secretkey123"

socketio = SocketIO(app, async_mode="gevent")

bcrypt = Bcrypt(app)
serializer = URLSafeTimedSerializer(app.secret_key)

# ---------------- EMAIL CONFIG ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv("EMAIL_USER")
app.config['MAIL_PASSWORD'] = os.getenv("EMAIL_PASS")
app.config['MAIL_DEFAULT_SENDER'] = os.getenv("EMAIL_USER")

mail = Mail(app)

# ---------------- CONFIG ----------------
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ---------------- DATABASE ----------------
client = MongoClient(os.getenv("MONGO_URI"))
db = client["newsaggregator"]

users = db["users"]
favorites = db["favorites"]

team_collection = db["team"]
contacts = db["contacts"]
get_comments= db["comments"]
stats = db["stats"]
notifications = db.notifications

API_KEY = os.getenv("NEWS_API_KEY")

# ---------------- HELPERS ----------------
def is_valid_email(email):
    return re.match(r'^[^@]+@[^@]+\.[^@]+$', email)

def is_admin():
    return session.get("role") == "admin"

def serialize_docs(docs):
    clean = []
    for d in docs:
        d["_id"] = str(d["_id"])
        clean.append(d)
    return clean

def send_admin_stats():
    socketio.emit("admin_stats", {
        "users": users.count_documents({}),
        "favorites": favorites.count_documents({})
    })

# ---------------- HOME ----------------
@app.route('/')
def home():
    if "user" not in session:
        return redirect(url_for("login"))

    search_query = request.args.get("q", "")
    category = request.args.get("category", "")

    news_api_key = os.getenv("NEWS_API_KEY")
    gnews_key = os.getenv("GNEWS_API_KEY")
    guardian_key = os.getenv("GUARDIAN_API_KEY")

    articles = []

    try:
        # =========================
        # 1. NEWSAPI
        # =========================
        if search_query:
            url1 = f"https://newsapi.org/v2/everything?q={search_query}&apiKey={news_api_key}"
        elif category:
            url1 = f"https://newsapi.org/v2/top-headlines?country=us&category={category}&apiKey={news_api_key}"
        else:
            url1 = f"https://newsapi.org/v2/top-headlines?country=us&apiKey={news_api_key}"

        res1 = requests.get(url1, timeout=10)
        data1 = res1.json().get("articles", [])

        for a in data1:
            articles.append({
                "title": a.get("title"),
                "description": a.get("description"),
                "url": a.get("url"),
                "image": a.get("urlToImage") or "/static/images/default.jpg",
                "source": "NewsAPI",
                "category": category or "general"
            })

        # =========================
        # 2. GNEWS
        # =========================
        if search_query:
            url2 = f"https://gnews.io/api/v4/search?q={search_query}&lang=en&token={gnews_key}"
        else:
            url2 = f"https://gnews.io/api/v4/top-headlines?lang=en&country=us&token={gnews_key}"

        res2 = requests.get(url2, timeout=10)
        data2 = res2.json().get("articles", [])

        for a in data2:
            articles.append({
                "title": a.get("title"),
                "description": a.get("description"),
                "url": a.get("url"),
                "image": a.get("image") or "/static/images/default.jpg",
                "source": "GNews",
                "category": category or "general"
            })

        # =========================
        # 3. GUARDIAN API
        # =========================
        try:
            guardian_url = (
                "https://content.guardianapis.com/search"
                "?show-fields=thumbnail,trailText&page-size=10"
                f"&api-key={guardian_key}"
            )

            res3 = requests.get(guardian_url, timeout=10)
            data3 = res3.json().get("response", {}).get("results", [])

            for a in data3:
                fields = a.get("fields", {})

                articles.append({
                    "title": a.get("webTitle"),
                    "description": fields.get("trailText"),
                    "url": a.get("webUrl"),
                    "image": fields.get("thumbnail") or "/static/images/default.jpg",
                    "source": "The Guardian",
                    "category": category or "general"
                })

        except Exception as e:
            print("Guardian error:", e)

        # =========================
        # 4. REMOVE DUPLICATES
        # =========================
        seen = set()
        unique_articles = []

        for a in articles:
            if a["url"] and a["url"] not in seen:
                unique_articles.append(a)
                seen.add(a["url"])

        # =========================
        # 5. MERGE MONGODB REACTIONS (🔥 IMPORTANT FIX)
        # =========================
        for article in unique_articles:

            db_article = favorites.find_one({"url": article["url"]})

            if db_article:
                article["likes"] = db_article.get("likes", 0)
                article["dislikes"] = db_article.get("dislikes", 0)
                article["comments"] = db_article.get("comments", [])
            else:
                article["likes"] = 0
                article["dislikes"] = 0
                article["comments"] = []

        # =========================
        # 6. SAVE / UPDATE FAVORITES
        # =========================
        for article in unique_articles:
            favorites.update_one(
                {"url": article["url"]},
                {
                    "$set": {
                        "title": article["title"],
                        "description": article["description"],
                        "image": article["image"],
                        "url": article["url"],
                        "source": article["source"],
                        "category": article["category"]
                    },
                    "$setOnInsert": {
                        "likes": 0,
                        "dislikes": 0,
                        "comments": []
                    }
                },
                upsert=True
            )

        # =========================
        # 7. USER
        # =========================
        user = users.find_one({"email": session["email"]})

        return render_template(
            "index.html",
            news=unique_articles,
            user=user
        )

    except Exception as e:
        print("HOME ERROR:", e)
        flash("Error fetching news")
        return redirect(url_for("home"))

#-----------------Registration----------------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        if not is_valid_email(email):
            flash("Invalid email")
            return redirect("/register")

        if password != confirm:
            flash("Passwords do not match")
            return redirect("/register")

        if users.find_one({"email": email}):
            flash("Email already exists")
            return redirect("/register")

        admin_email = os.getenv("ADMIN_EMAIL")
        role = "admin" if email == admin_email else "user"

        hashed = bcrypt.generate_password_hash(password).decode()

        # 🔥 DEFAULT VERIFIED (since email may fail)
        verified_status = True

        # -------- EMAIL TOKEN --------
        token = serializer.dumps(email, salt="verify")
        link = url_for("verify_email", token=token, _external=True)

        msg = Message(
            "Verify Your Account",
            recipients=[email]
        )
        msg.body = f"Click to verify your account:\n{link}"

        # -------- SAFE EMAIL SEND --------
        try:
            mail.send(msg)
            verified_status = False   # require verification if email works
            flash("Verification email sent")
        except Exception as e:
            print("Email failed:", e)
            flash("Email failed → auto verified")

        # -------- SAVE USER --------
        users.insert_one({
            "username": username,
            "email": email,
            "password": hashed,
            "role": role,
            "verified": verified_status,
            "banned": False
        })

        # 🔥 AUTO LOGIN (KEY PART)
        session["user"] = username
        session["email"] = email
        session["role"] = role

        flash("Account created & logged in ✅")
        return redirect("/")

    return render_template("register.html")

@app.route('/verify/<token>')
def verify_email(token):
    try:
        email = serializer.loads(token, salt="verify", max_age=3600)
    except Exception as e:
        print("Verify error:", e)
        return "Invalid or expired verification link"

    users.update_one(
        {"email": email},
        {"$set": {"verified": True}}
    )

    return "✅ Email verified successfully! You can now login."

# ---------------- RESET PASSWORD ----------------
@app.route('/reset/<token>', methods=['GET','POST'])
def reset_token(token):
    try:
        email = serializer.loads(token, salt="reset", max_age=3600)
    except:
        return "Invalid or expired link"

    if request.method == 'POST':
        password = request.form.get("password")

        hashed = bcrypt.generate_password_hash(password).decode()
        users.update_one({"email": email}, {"$set": {"password": hashed}})

        flash("Password reset successful")
        return redirect("/login")

    return render_template("reset_password.html")

@app.route('/reset_request', methods=['GET', 'POST'])
def reset_request():
    if request.method == 'POST':
        email = request.form.get("email")

        user = users.find_one({"email": email})
        if user:
            token = serializer.dumps(email, salt="reset")
            link = url_for("reset_token", token=token, _external=True)

            print("RESET LINK:", link)  # TEMP

            flash("Check terminal for reset link")
        else:
            flash("Email not found")

    return render_template("reset_request.html")

# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':

        email = request.form.get("email")
        password = request.form.get("password")

        user = users.find_one({"email": email})  # ✅ FIXED

        if user:
            if not user.get("verified", True):
                flash("Verify your email first")
                return redirect(url_for("login"))

            if bcrypt.check_password_hash(user["password"], password):

                session["user"] = user["username"]
                session["email"] = user["email"]
                session["role"] = user.get("role", "user")
                session["user_image"] = user.get("image", "/static/images/user.png")

                # ✅ track activity (FIXED datetime too)
              
                users.update_one(
                    {"email": email},
                    {"$push": {"activity": datetime.now(timezone.utc)}}
                )
                
                flash("Login successful")

                return redirect(url_for("home"))

        flash("Invalid credentials")

    return render_template("login.html")



# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect("/login")

# ---------------- PROFILE ----------------
@app.route('/profile', methods=['GET','POST'])
def profile():
    if "user" not in session:
        return redirect("/login")

    user = users.find_one({"email": session["email"]})

    if request.method == 'POST':
        username = request.form.get("username")
        password = request.form.get("password")
        file = request.files.get("image")

        update = {}

        if username:
            update["username"] = username
            session["user"] = username
            
         
        if password:
            update["password"] = bcrypt.generate_password_hash(password)
        

        # 🔥 IMAGE UPLOAD FIX
        if file and file.filename != "":
            filename = secure_filename(file.filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(path)

            update["image"] = "/" + path.replace("\\", "/")  # FIX PATH

        if update:
            users.update_one({"email": session["email"]}, {"$set": update})
            flash("Profile updated")
        new_password = request.form.get("new_password")

        if new_password:
         hashed = bcrypt.generate_password_hash(new_password).decode()
         update["password"] = hashed
         
         flash("Profile updated successfully!")
        
        return redirect(url_for("profile"))

        return redirect("/profile")

    return render_template("profile.html", user=user)

# ---------------- CONTACT ----------------
@app.route('/contact', methods=['POST'])
def contact():
    contacts.insert_one({
        "name": request.form.get("name"),
        "email": request.form.get("email"),
        "message": request.form.get("message")
    })
    return "", 204

# ---------------- CHATBOT ----------------
@app.route('/chat', methods=['POST'])
def chat():
    msg = request.json.get("message", "")

    res = requests.get(f"https://newsapi.org/v2/everything?q={msg}&apiKey={API_KEY}").json()
    articles = res.get("articles", [])[:3]

    return {
        "replies": [{"title": a["title"], "url": a["url"]} for a in articles]
    }
    
@app.route('/ai_news')
def ai_news():
    if "user" not in session:
        return {"data": []}

    favs = list(favorites.find({"user": session["email"]}))

    categories = []
    for f in favs:
        if f.get("category"):
            categories.append(f["category"])

    # pick most common category
    category = max(set(categories), key=categories.count) if categories else "technology"

    url = f"https://newsapi.org/v2/top-headlines?category={category}&apiKey={API_KEY}"
    res = requests.get(url).json()

    return {"data": res.get("articles", [])}

# ---------------- FAVORITES ----------------

@app.route('/favorite', methods=['POST'])
def favorite():
    data = {
        "title": request.form.get("title"),
        "description": request.form.get("description"),
        "url": request.form.get("url"),
        "image": request.form.get("image"),
        "user": session["email"]
    }

    if not favorites.find_one({"url": data["url"], "user": data["user"]}):
        favorites.insert_one(data)

    return "", 204

@app.route('/favorites')
def view_favorites():
    favs = list(favorites.find({"user": session["email"]}))
    user = users.find_one({"email": session["email"]})
    return render_template("favorites.html", news=favs, user=user)

@app.route('/delete', methods=['POST'])
def delete():
    favorites.delete_one({
        "url": request.form.get("url"),
        "user": session["email"]
    })
    return "", 204



@app.route('/react', methods=['POST'])
def react():
    url = request.form.get("url")
    action = request.form.get("action")

    if not url or action not in ["like", "dislike"]:
        return {"error": "invalid request"}, 400

    if action == "like":
        favorites.update_one(
            {"url": url},
            {"$inc": {"likes": 1}}
        )
    else:
        favorites.update_one(
            {"url": url},
            {"$inc": {"dislikes": 1}}
        )

    doc = favorites.find_one({"url": url})

    # CREATE NOTIFICATION
    notifications.insert_one({
        "url": url,
        "type": action,
        "user": session.get("user"),
        "likes": doc.get("likes", 0),
        "dislikes": doc.get("dislikes", 0),
        "comments_count": len(doc.get("comments", [])),
        "read": False
    })

    return {
        "likes": doc.get("likes", 0),
        "dislikes": doc.get("dislikes", 0),
        "comments": len(doc.get("comments", []))
    }
@app.route('/comment', methods=['POST'])
def comment():
    url = request.form.get("url")
    text = request.form.get("text")

    if not url or not text:
        return {"error": "invalid request"}, 400

    comment_data = {
        "user": session.get("user"),
        "text": text
    }

    favorites.update_one(
        {"url": url},
        {"$push": {"comments": comment_data}}
    )

    doc = favorites.find_one({"url": url})

    # NOTIFICATION
    notifications.insert_one({
        "url": url,
        "type": "comment",
        "user": session.get("user"),
        "text": text,
        "likes": doc.get("likes", 0),
        "dislikes": doc.get("dislikes", 0),
        "comments_count": len(doc.get("comments", [])),
        "read": False
    })

    return {
        "comments": len(doc.get("comments", []))
    }
@app.route('/notifications')
def get_notifications():
    data = list(notifications.find().sort("_id", -1).limit(20))

    unread = notifications.count_documents({"read": False})

    result = []

    for n in data:
        result.append({
            "title": n.get("type"),
            "user": n.get("user"),
            "likes": n.get("likes", 0),
            "dislikes": n.get("dislikes", 0),
            "comments_count": n.get("comments_count", 0)
        })

    return {
        "unread": unread,
        "data": result
    }

#----------------- REPLIES ----------------

@app.route('/reply', methods=['POST'])
def reply():
    favorites.update_one(
        {"url": request.form.get("url"), "comments.text": request.form.get("parent")},
        {
            "$push": {
                "comments.$.replies": {
                    "user": session["user"],
                    "text": request.form.get("text")
                }
            }
        }
    )
    return "", 204

#----------------- ADMIN FAVORITES ----------------
@app.route('/all_favorites')
def all_favorites():
    if session.get("role") != "admin":
        return "Unauthorized", 403

    data = list(favorites.find().sort("_id", -1))

    for d in data:
        d["_id"] = str(d["_id"])

    return render_template("all_favorites.html", news=data)


#------------Admin Dashboard----------------
@app.route('/admin')
def admin():
    if session.get("role") != "admin":
        return "Access denied", 403

    total_users = users.count_documents({})
    total_favorites = favorites.count_documents({})

    all_users = list(users.find())  

    return render_template(
        "admin.html",
        total_users=total_users,
        total_favorites=total_favorites,
        users=all_users   
    )
    
@app.route('/delete_user/<email>')
def delete_user(email):
    if session.get("role") != "admin":
        return "Unauthorized", 403

    users.delete_one({"email": email})
    favorites.delete_many({"user": email})  # cleanup

    return redirect("/admin")

@app.route('/admin_stats')
def admin_stats():
    if session.get("role") != "admin":
        return {}, 403

    users_count = users.count_documents({})
    fav_count = favorites.count_documents({})

    return {
        "users": users_count,
        "favorites": fav_count
    }
    
@app.route('/analytics')
def analytics():
    if session.get("role") != "admin":
        return {}, 403

    # 🔥 Top categories
    pipeline = [
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ]

    categories = list(favorites.aggregate(pipeline))

    # 🔥 Likes vs dislikes
    data = list(favorites.find({}, {"likes":1, "dislikes":1, "comments":1}))

    total_likes = sum(d.get("likes", 0) for d in data)
    total_dislikes = sum(d.get("dislikes", 0) for d in data)
    total_comments = sum(len(d.get("comments", [])) for d in data)

    return {
        "categories": categories,
        "likes": total_likes,
        "dislikes": total_dislikes,
        "comments": total_comments,
        "users": users.count_documents({}),
        "favorites": favorites.count_documents({})
    }
    
def push_analytics():
    socketio.emit("analytics_update", {
        "users": users.count_documents({}),
        "favorites": favorites.count_documents({})
    }, room="admin")


    

# ---------------- TEAM (ADMIN) ----------------

@app.route('/upload_team', methods=['POST'])
def upload_team():
    if "user" not in session:
        return redirect("/login")

    if session.get("role") != "admin":
        return "Unauthorized", 403

    role = request.form.get("role")
    file = request.files.get("image")

    print("ROLE:", role)
    print("FILE:", file)

    if not role or not file:
        print("Missing role or file")
        return redirect("/about")

    # SAVE FILE
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    image_path = "/" + filepath.replace("\\", "/")

    print("IMAGE PATH:", image_path)

    # 🔥 USE VARIABLES (NO CONFUSION)
    filter_query = {"role": role}
    update_query = {"$set": {"image": image_path}}

    # 🔥 CLEAN CALL (NO ERROR POSSIBLE)
    team_collection.update_one(filter_query, update_query, upsert=True)

    print("UPLOAD SUCCESS")

    return redirect("/about")



#----------------- REPORTS ----------------

@app.route('/report')
def report():
    if session.get("role") != "admin":
        return "Unauthorized", 403

    period = request.args.get("type", "daily")  # default daily
    
    now = datetime.now(UTC)

    if period == "daily":
        start = now - timedelta(days=1)
    elif period == "weekly":
        start = now - timedelta(days=7)
    elif period == "monthly":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(days=365)

    # 👥 USERS COUNT
    total_users = users.count_documents({})

    # ⭐ FAVORITES COUNT
    total_favorites = favorites.count_documents({})

    # 🔥 MOST POPULAR ARTICLES
    trending = list(favorites.find().sort("clicks", -1).limit(5))

    # 💬 TOTAL COMMENTS
    pipeline = [
        {"$project": {"count": {"$size": {"$ifNull": ["$comments", []]}}}},
        {"$group": {"_id": None, "total": {"$sum": "$count"}}}
    ]
    comment_result = list(favorites.aggregate(pipeline))
    total_comments = comment_result[0]["total"] if comment_result else 0

    return render_template( "report.html",
    period=period,
    users=total_users,
    favorites=total_favorites,
    comments=total_comments,
    trending=trending
)
    
@app.route('/active_users')
def active_users():
    if session.get("role") != "admin":
        return {}, 403

    pipeline = [
        {"$unwind": "$activity"},
        {
            "$group": {
                "_id": {
                    "day": {"$dayOfMonth": "$activity"},
                    "month": {"$month": "$activity"}
                },
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"_id.month": 1, "_id.day": 1}}
    ]

    data = list(users.aggregate(pipeline))

    labels = [f"{d['_id']['day']}/{d['_id']['month']}" for d in data]
    values = [d["count"] for d in data]

    return {"labels": labels, "values": values}

def generate_ai_summary():
    total_users = users.count_documents({})
    total_favorites = favorites.count_documents({})

    total_likes = sum([f.get("likes", 0) for f in favorites.find()])
    total_comments = sum([len(f.get("comments", [])) for f in favorites.find()])

    summary = f"""
    The platform currently has {total_users} users.
    A total of {total_favorites} articles have been saved.
    Users have interacted with content through {total_likes} likes and {total_comments} comments.
    Engagement is {'high' if total_likes > 10 else 'growing'}.
    """

    return summary
    
    
#----------------- PDF REPORT ----------------
@app.route('/report_pdf')
def report_pdf():
    if session.get("role") != "admin":
        return "Unauthorized", 403

    

    doc = SimpleDocTemplate("report.pdf", pagesize=A4)
    styles = getSampleStyleSheet()
    content = []

    # 🔵 BORDER TITLE
    content.append(Paragraph("<b><font color='blue'>News Aggregator Report</font></b>", styles['Title']))
    content.append(Spacer(1, 20))

    # 📊 DATA
    total_users = users.count_documents({})
    total_favorites = favorites.count_documents({})

    total_likes = sum([f.get("likes", 0) for f in favorites.find()])
    total_dislikes = sum([f.get("dislikes", 0) for f in favorites.find()])
    total_comments = sum([len(f.get("comments", [])) for f in favorites.find()])
    

    # 📌 SUMMARY TABLE
    data = [
        ["Metric", "Value"],
        ["Users", total_users],
        ["Favorites", total_favorites],
        ["Likes", total_likes],
        ["Dislikes", total_dislikes],
        ["Comments", total_comments],
    ]

    table = Table(data, colWidths=[200, 150])
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 1, colors.blue),
        ("BACKGROUND", (0,0), (-1,0), colors.lightblue),
    ]))

    content.append(table)
    content.append(Spacer(1, 20))

    # 📊 BAR CHART
    drawing = Drawing(400, 200)
    chart = VerticalBarChart()

    chart.x = 50
    chart.y = 30
    chart.height = 125
    chart.width = 300

    chart.data = [[total_users, total_favorites, total_likes]]
    chart.categoryAxis.categoryNames = ["Users", "Favorites", "Likes"]

    drawing.add(chart)
    content.append(drawing)

    # 🔥 PAGE BREAK (VERY IMPORTANT)
    content.append(PageBreak())

    # 🔥 TRENDING (LIMITED)
    trending = list(favorites.find().sort("clicks", -1).limit(5))

    trend_data = [["Title", "Clicks"]]
    for t in trending:
        trend_data.append([t.get("title", "")[:30], t.get("clicks", 0)])

    trend_table = Table(trend_data, colWidths=[250, 100])
    trend_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 1, colors.green),
    ]))

    content.append(Paragraph("<b>Top Trending</b>", styles['Heading2']))
    content.append(trend_table)
    
    summary = generate_ai_summary()

    content.append(Paragraph("<b>AI Insights</b>", styles['Heading2']))
    content.append(Spacer(1,10))
    content.append(Paragraph(summary, styles['Normal']))

    doc.build(content)

    return send_file("report.pdf", as_attachment=True)

@socketio.on('connect')
def handle_connect():
    if "email" in session:

        # ✅ USER ROOM
        join_room(session["email"])

        # ✅ ADMIN ROOM
        if session.get("role") == "admin":
            join_room("admin")

        print("User joined room:", session["email"])

# ---------------- ABOUT ----------------
@app.route('/about')
def about():
    team = list(team_collection.find())
    user = users.find_one({"email": session["email"]})
    return render_template("about.html",team=team, user=user)


# ---------------- RUN ----------------
if __name__ == "__main__":
    socketio.run(app, debug=True)