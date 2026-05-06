from flask import Flask, render_template, request, redirect, session, Response
import boto3
import logging

from models import db, User, File

app = Flask(__name__)

# Logging
logging.basicConfig(
    filename="/var/log/cloud-drive.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True
)

app.secret_key = "secret"

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
db.init_app(app)

s3 = boto3.client('s3', region_name='ap-south-1')

BUCKET = "cloud-drive-storage-6"


# ================= HOME =================
@app.route("/")
def home():
    return render_template("login.html")


# ================= REGISTER =================
@app.route("/register", methods=["POST"])
def register():

    username = request.form["username"]
    password = request.form["password"]
    plan = int(request.form["plan"])

    storage_limit = plan * 1024 * 1024

    user = User(
        username=username,
        password=password,
        storage_limit=storage_limit
    )

    db.session.add(user)
    db.session.commit()

    logging.info(f"User registered: {username}")

    return redirect("/")


# ================= LOGIN =================
@app.route("/login", methods=["POST"])
def login():

    username = request.form["username"]
    password = request.form["password"]

    user = User.query.filter_by(username=username, password=password).first()

    if user:
        session["user"] = user.id
        logging.info(f"User logged in: {username}")
        return redirect("/dashboard")

    return "Invalid login"


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.pop("user", None)
    logging.info("User logged out")
    return redirect("/")


# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():

    if "user" not in session:
        return redirect("/")

    user = User.query.get(session["user"])
    files = File.query.filter_by(user_id=user.id).all()

    return render_template(
        "dashboard.html",
        files=files,
        username=user.username
    )


# ================= USER DASHBOARD =================
@app.route("/user-dashboard")
def user_dashboard():

    if "user" not in session:
        return redirect("/")

    user = User.query.get(session["user"])
    files = File.query.filter_by(user_id=user.id).all()

    total_files = len(files)
    used_storage = sum(file.size for file in files)
    total_storage = user.storage_limit
    remaining_storage = total_storage - used_storage

    return render_template(
        "user_dashboard.html",
        total_files=total_files,
        used_storage=used_storage,
        total_storage=total_storage,
        remaining_storage=remaining_storage
    )


# ================= UPGRADE =================
@app.route("/upgrade")
def upgrade():
    return render_template("upgrade.html")


@app.route("/upgrade-plan", methods=["POST"])
def upgrade_plan():

    plan = int(request.form["plan"])

    user = User.query.get(session["user"])
    user.storage_limit = plan * 1024 * 1024

    db.session.commit()

    logging.info(f"User upgraded plan: {user.username}")

    return redirect("/user-dashboard")


# ================= UPLOAD =================
@app.route("/upload", methods=["POST"])
def upload():

    if "user" not in session:
        return redirect("/")

    file = request.files["file"]

    if file:

        filename = file.filename

        file.seek(0, 2)
        size = file.tell()
        file.seek(0)

        user = User.query.get(session["user"])

        user_files = File.query.filter_by(user_id=user.id).all()
        used_storage = sum(f.size for f in user_files)

        if used_storage + size > user.storage_limit:
            return "Storage Full"

        s3.upload_fileobj(file, BUCKET, filename)

        new_file = File(
            filename=filename,
            user_id=user.id,
            size=size
        )

        db.session.add(new_file)
        db.session.commit()

        logging.info(f"File uploaded: {filename}")

    return redirect("/dashboard")


# ================= DELETE =================
@app.route("/delete/<filename>")
def delete(filename):

    s3.delete_object(Bucket=BUCKET, Key=filename)

    file = File.query.filter_by(filename=filename).first()

    if file:
        db.session.delete(file)
        db.session.commit()

    logging.info(f"File deleted: {filename}")

    return redirect("/dashboard")


# ================= DELETE MULTIPLE =================
@app.route("/delete-multiple", methods=["POST"])
def delete_multiple():

    data = request.get_json()
    files = data["files"]

    for file in files:
        s3.delete_object(Bucket=BUCKET, Key=file)

        file_db = File.query.filter_by(filename=file).first()

        if file_db:
            db.session.delete(file_db)

    db.session.commit()

    return {"status": "success"}


# ================= DOWNLOAD =================
@app.route("/download/<filename>")
def download(filename):

    file_obj = s3.get_object(Bucket=BUCKET, Key=filename)
    file_stream = file_obj['Body'].read()

    return Response(
        file_stream,
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


# ================= ADMIN =================
@app.route("/admin")
def admin():

    user = User.query.get(session["user"])

    if not user.is_admin:
        return "Access Denied"

    users = User.query.all()
    files = File.query.all()

    total_users = User.query.count()
    total_files = File.query.count()

    return render_template(
        "admin.html",
        users=users,
        files=files,
        total_users=total_users,
        total_files=total_files
    )


# ================= DELETE USER =================
@app.route("/delete-user/<int:user_id>")
def delete_user(user_id):

    admin = User.query.get(session["user"])

    if not admin.is_admin:
        return "Access Denied"

    user = User.query.get(user_id)

    if user:

        user_files = File.query.filter_by(user_id=user.id).all()

        for file in user_files:
            s3.delete_object(Bucket=BUCKET, Key=file.filename)
            db.session.delete(file)

        db.session.delete(user)
        db.session.commit()

    return redirect("/admin")


# ================= MAKE ADMIN =================
@app.route("/make-admin/<int:user_id>")
def make_admin(user_id):

    user = User.query.get(user_id)
    user.is_admin = 1

    db.session.commit()

    return redirect("/admin")


# ================= REMOVE ADMIN =================
@app.route("/remove-admin/<int:user_id>")
def remove_admin(user_id):

    user = User.query.get(user_id)
    user.is_admin = 0

    db.session.commit()

    return redirect("/admin")


# ================= ACTIVITY =================
@app.route("/activity")
def activity():

    try:
        with open("/var/log/cloud-drive.log") as f:
            logs = f.readlines()[-100:]
    except:
        logs = ["No logs found"]

    return render_template("activity.html", logs=logs)


# ================= RUN =================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(host="0.0.0.0", port=5000, debug=True)
