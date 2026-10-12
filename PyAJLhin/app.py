import json
import random
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = "my_super_secret_key_for_basketball_event"

# --- ฐานข้อมูล SQLite (.db) ---
DB_PATH = "database.db"


def get_db():
  conn = sqlite3.connect(DB_PATH)
  conn.row_factory = sqlite3.Row
  return conn


def init_db():
  """สร้างตารางในฐานข้อมูล SQLite อัตโนมัติหากยังไม่มี"""
  conn = get_db()
  cursor = conn.cursor()

  # ตารางผู้ใช้งาน (Users)
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT NOT NULL,
        fullname TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        role TEXT DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

  # ตารางการจองอีเว้นท์ (Bookings)
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        package_id TEXT NOT NULL,
        package_name TEXT NOT NULL,
        base_price INTEGER NOT NULL,
        options TEXT DEFAULT '',
        extra_price INTEGER DEFAULT 0,
        total_price INTEGER NOT NULL,
        event_date TEXT NOT NULL,
        note TEXT DEFAULT '',
        status TEXT DEFAULT 'pending', -- 'pending' (รอตรวจสอบ), 'confirmed' (ว่าง/อนุมัติ), 'rejected' (คิวเต็ม)
        admin_note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

  # สร้างบัญชีผู้ดูแลระบบเริ่มต้น (Default Admin) หากยังไม่มี
  cursor.execute("SELECT * FROM users WHERE username = 'admin'")
  if not cursor.fetchone():
    cursor.execute(
        """
        INSERT INTO users (username, password, email, fullname, phone, role)
        VALUES ('admin', 'admin1234', 'basketballeventbooking@gmail.com', 'ผู้ดูแลระบบ (Admin)', '081-234-5678', 'admin')
        """
    )

  conn.commit()
  conn.close()


# เรียกสร้างตารางทันทีเมื่อรันแอป
init_db()

# ตัวเก็บ OTP ชั่วคราวในหน่วยความจำ {email: otp_code}
otp_store = {}


# ส่งข้อมูล current_user ให้ Jinja Template ทุกหน้าโดยอัตโนมัติ
@app.context_processor
def inject_user():
  current_username = session.get("user")
  current_user_obj = None
  if current_username:
    try:
      conn = get_db()
      cursor = conn.cursor()
      cursor.execute(
          "SELECT * FROM users WHERE username = ?", (current_username,)
      )
      current_user_obj = cursor.fetchone()
      conn.close()
    except Exception as e:
      current_user_obj = None
  return dict(current_user=current_user_obj)


# --- ข้อมูลแพ็กเกจจัดงานอีเว้นท์แข่งขันบาสเกตบอล ---
EVENT_PACKAGES = {
    "small": {
        "id": "small",
        "name": "แพ็กเกจแข่งขันบาสเกตบอล ไซส์เล็ก (Small)",
        "price": 5000,
        "description": "เหมาะสำหรับงานกระชับมิตร ทีมเล็กๆ หรือทัวร์นาเมนต์ภายใน",
        "features": [
            "สนามแข่งมาตรฐาน 1 สนาม",
            "เวลาแข่งขันครึ่งวัน (4 ชั่วโมง)",
            "อุปกรณ์พื้นฐาน (ลูกบาสเกตบอล, นกหวีด, ป้ายคะแนน)",
            "ผู้ดูแลสนามและประสานงาน 1 ท่าน",
        ],
    },
    "medium": {
        "id": "medium",
        "name": "แพ็กเกจแข่งขันบาสเกตบอล ไซส์กลาง (Medium)",
        "price": 12000,
        "description": (
            "เหมาะสำหรับการแข่งขันระดับองค์กร ชุมชน หรือกระชับมิตรระหว่างสถาบัน"
        ),
        "features": [
            "สนามแข่งมาตรฐาน 2 สนาม",
            "เวลาแข่งขันเต็มวัน (8 ชั่วโมง)",
            "กรรมการตัดสินมืออาชีพมีใบอนุญาต 2 ท่าน",
            "เครื่องดื่มเกลือแร่และน้ำดื่มสำหรับนักกีฬา",
            "เจ้าหน้าที่ปฐมพยาบาลเบื้องต้น",
        ],
    },
    "large": {
        "id": "large",
        "name": "แพ็กเกจแข่งขันบาสเกตบอล ไซส์ใหญ่ (Large)",
        "price": 25000,
        "description": (
            "เหมาะสำหรับทัวร์นาเมนต์ใหญ่ ชิงรางวัล หรืออีเว้นท์ระดับประเทศ"
        ),
        "features": [
            "สนามแข่งเต็มรูปแบบ + อัฒจันทร์รองรับผู้ชม",
            "จัดแข่งขัน 2 วันเต็ม",
            "กรรมการสมาคมฯ พร้อมโต๊ะเทคนิคบันทึกสถิติครบทีม",
            "ระบบถ่ายทอดสด Live Streaming Full HD หลายมุมกล้อง",
            "ชุดของรางวัลและถ้วยเกียรติยศ",
            "ทีมแพทย์สนามและหน่วยกู้ชีพสแตนด์บาย",
        ],
    },
}

# --- ตั้งค่า Gmail SMTP (สำหรับส่ง OTP จริง ด้วย Google App Password 16 หลัก) ---
MAIL_SERVER = "smtp.gmail.com"
MAIL_PORT = 587
MAIL_USERNAME = "basketballeventbooking@gmail.com"
MAIL_PASSWORD = "lhko rxnl ubfz awvz"
EMAIL_SENDER = MAIL_USERNAME


def send_otp_email(receiver_email, otp_code):
  """ฟังก์ชันส่ง OTP ทาง Gmail SMTP พร้อมระบบความปลอดภัยและการจัดหน้าสวยงาม"""
  sender_email = MAIL_USERNAME.strip()
  app_password = MAIL_PASSWORD.replace(" ", "").strip()

  if (
      not sender_email
      or sender_email == "your_gmail_account@gmail.com"
      or "@" not in sender_email
  ):
    return (
        False,
        "ยังไม่ได้ระบุอีเมลผู้ส่ง กรุณาใส่อีเมล Gmail ของคุณในตัวแปร"
        " MAIL_USERNAME",
    )

  try:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"ระบบจองอีเว้นท์บาสเกตบอล <{sender_email}>"
    msg["To"] = receiver_email
    msg["Subject"] = "รหัสยืนยัน OTP สำหรับสมัครสมาชิกเว็บไซต์อีเว้นท์บาสเกตบอล"

    # ข้อความแบบ Plain Text
    body_plain = f"""สวัสดีครับ,

รหัส OTP สำหรับยืนยันการสมัครสมาชิกของคุณคือ: {otp_code}

รหัสนี้มีอายุการใช้งานสั้น กรุณาอย่าบอกรหัสนี้ให้ผู้อื่นทราบ
"""

    # ข้อความแบบ HTML สวยงาม
    body_html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: Arial, sans-serif; background-color: #f0f7ff; padding: 25px; margin: 0;">
        <div style="max-width: 480px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; padding: 32px; box-shadow: 0 4px 12px rgba(29,138,242,0.08); border: 1px solid #e0f2fe;">
            <div style="text-align: center; margin-bottom: 20px;">
                <span style="display: inline-block; font-size: 28px; background-color: #e0f2fe; padding: 12px; border-radius: 16px;">🏀</span>
            </div>
            <h2 style="color: #1d8af2; text-align: center; margin-top: 0; font-size: 22px;">ยืนยันการสมัครสมาชิก</h2>
            <p style="color: #475569; font-size: 15px;">สวัสดีครับ,</p>
            <p style="color: #475569; font-size: 15px; line-height: 1.6;">คุณได้ทำการสมัครสมาชิกเว็บไซต์ระบบจองจัดงานแข่งขันบาสเกตบอล รหัส OTP 6 หลักสำหรับยืนยันตัวตนของคุณคือ:</p>
            <div style="text-align: center; margin: 28px 0;">
                <span style="display: inline-block; font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #ffffff; background-color: #1d8af2; padding: 14px 30px; border-radius: 16px; box-shadow: 0 4px 10px rgba(29,138,242,0.25);">
                    {otp_code}
                </span>
            </div>
            <p style="color: #94a3b8; font-size: 13px; text-align: center;">รหัสนี้มีอายุการใช้งานชั่วคราว กรุณาอย่าเปิดเผยรหัสนี้แก่ผู้อื่น</p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15)
    server.starttls()
    server.login(sender_email, app_password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()
    return True, None
  except smtplib.SMTPServerDisconnected:
    return (
        False,
        "การเชื่อมต่อไปยัง Gmail ถูกตัด (กรุณาตรวจสอบว่าใส่อีเมล Gmail ใน"
        " MAIL_USERNAME ถูกต้อง)",
    )
  except smtplib.SMTPAuthenticationError:
    return (
        False,
        "เข้าสู่ระบบ Gmail ไม่สำเร็จ กรุณาตรวจสอบอีเมลและรหัสผ่านแอป 16 หลัก",
    )
  except Exception as e:
    try:
      print("Error sending email via Gmail:", repr(e))
    except Exception:
      pass
    return False, str(e)


# ==========================================
# ROUTES
# ==========================================


# 1. หน้าแรก (แสดง Hero, ปฏิทินวันว่าง, แพ็กเกจ และมาตรฐานการแข่งขัน)
@app.route("/")
def hello_world():
  conn = get_db()
  cursor = conn.cursor()
  # ดึงรายการวันที่ได้รับการคอนเฟิร์มแล้ว (คิวเต็ม) มาแสดงเตือนในหน้าแรก
  cursor.execute(
      "SELECT event_date, package_name FROM bookings WHERE status = 'confirmed'"
      " ORDER BY event_date ASC"
  )
  booked_dates = cursor.fetchall()
  conn.close()
  return render_template(
      "index.html", packages=EVENT_PACKAGES, booked_dates=booked_dates
  )


# API สำหรับเช็กสถานะวันว่างแบบเร็วจากหน้าแรก
@app.route("/api/check-date")
def api_check_date():
  query_date = request.args.get("date", "").strip()
  if not query_date:
    return jsonify({"status": "invalid"})

  conn = get_db()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT status FROM bookings WHERE event_date = ? ORDER BY id DESC",
      (query_date,),
  )
  rows = cursor.fetchall()
  conn.close()

  # ตรวจสอบสถานะการจองในวันนั้น
  has_confirmed = any(r["status"] == "confirmed" for r in rows)
  has_pending = any(r["status"] == "pending" for r in rows)

  if has_confirmed:
    return jsonify({"status": "confirmed"})  # คิวเต็ม
  elif has_pending:
    return jsonify({"status": "pending"})  # รอการตรวจสอบ
  else:
    return jsonify({"status": "available"})  # ว่างพร้อมจอง


# 2. หน้า Login
@app.route("/login", methods=["GET", "POST"])
def login():
  if request.method == "POST":
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, password),
    )
    user = cursor.fetchone()
    conn.close()

    if user:
      session["user"] = user["username"]
      flash(
          f"ยินดีต้อนรับคุณ {user['fullname'] or user['username']} เข้าสู่ระบบสำเร็จ!",
          "success",
      )
      return redirect(url_for("hello_world"))
    else:
      flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง", "error")

  return render_template("login.html")


# 3. หน้า Register (ส่ง OTP ทางอีเมล แล้วสร้างข้อมูลลง SQLite .db)
@app.route("/register", methods=["GET", "POST"])
def register():
  if request.method == "POST":
    action = request.form.get("action")

    # ขั้นตอนที่ 1: รับข้อมูลแล้วส่ง OTP ไปทางอีเมล
    if action == "request_otp":
      username = (request.form.get("username") or "").strip()
      password = (request.form.get("password") or "").strip()
      email = (request.form.get("email") or "").strip()

      if not username or not password or not email:
        flash("กรุณากรอกข้อมูลให้ครบทุกช่อง", "error")
        return render_template("register.html", step=1)

      conn = get_db()
      cursor = conn.cursor()
      cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
      existing_user = cursor.fetchone()
      conn.close()

      if existing_user:
        flash("ชื่อผู้ใช้นี้ถูกใช้งานแล้ว กรุณาใช้ชื่ออื่น", "error")
        return render_template("register.html", step=1)

      generated_otp = str(random.randint(100000, 999999))
      session["temp_register"] = {
          "username": username,
          "password": password,
          "email": email,
      }
      otp_store[email] = generated_otp

      email_sent, error_msg = send_otp_email(email, generated_otp)
      if email_sent:
        flash(
            f"📧 ระบบได้ส่งรหัส OTP 6 หลักไปยังอีเมล {email} เรียบร้อยแล้ว",
            "success",
        )
        return render_template("register.html", step=2, email=email)
      else:
        flash(
            f"❌ ไม่สามารถส่งอีเมลได้: {error_msg or 'กรุณาตรวจสอบการตั้งค่า Gmail'}",
            "error",
        )
        return render_template("register.html", step=1)

    # ขั้นตอนที่ 2: ตรวจสอบ OTP และบันทึกลงฐานข้อมูล SQLite
    elif action == "verify_otp":
      user_otp = (request.form.get("otp") or "").strip()
      temp_data = session.get("temp_register")

      if temp_data:
        email = temp_data["email"]
        correct_otp = (otp_store.get(email) or "").strip()

        if user_otp and correct_otp and user_otp == correct_otp:
          try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                """
                            INSERT INTO users (username, password, email, fullname, role)
                            VALUES (?, ?, ?, ?, 'user')
                            """,
                (
                    temp_data["username"],
                    temp_data["password"],
                    temp_data["email"],
                    temp_data["username"],
                ),
            )
            conn.commit()
            conn.close()

            session.pop("temp_register", None)
            otp_store.pop(email, None)

            # เข้าสู่ระบบให้อัตโนมัติหลังยืนยัน OTP สำเร็จ
            session["user"] = temp_data["username"]
            flash(
                "🎉 ยืนยัน OTP และสมัครสมาชิกสำเร็จ ยินดีต้อนรับ!",
                "success",
            )
            return redirect(url_for("hello_world"))
          except Exception as e:
            flash(f"เกิดข้อผิดพลาดในการบันทึกข้อมูล: {e}", "error")
            return render_template("register.html", step=1)
        else:
          flash(
              "❌ รหัส OTP ไม่ถูกต้อง กรุณาตรวจสอบในอีเมลอีกครั้ง",
              "error",
          )
          return render_template("register.html", step=2, email=email)
      else:
        flash(
            "⚠️ เซสชันหมดอายุ กรุณากรอกข้อมูลเพื่อขอรหัส OTP ใหม่อีกครั้ง",
            "error",
        )
        return render_template("register.html", step=1)

  return render_template("register.html", step=1)


# 4. หน้า Logout
@app.route("/logout")
def logout():
  session.pop("user", None)
  flash("ออกจากระบบเรียบร้อยแล้ว", "info")
  return redirect(url_for("hello_world"))


# 5. หน้าโปรไฟล์ & แก้ไขข้อมูลตัวเอง (Profile & Edit Profile)
@app.route("/profile", methods=["GET", "POST"])
def profile():
  if "user" not in session:
    flash("กรุณาเข้าสู่ระบบก่อนเข้าหน้าโปรไฟล์", "error")
    return redirect(url_for("login"))

  username = session.get("user")
  conn = get_db()
  cursor = conn.cursor()

  if request.method == "POST":
    action = request.form.get("action")

    # อัปเดตข้อมูลส่วนตัว (ชื่อ-นามสกุล, เบอร์โทร, อีเมล)
    if action == "update_profile":
      fullname = (request.form.get("fullname") or "").strip()
      phone = (request.form.get("phone") or "").strip()
      email = (request.form.get("email") or "").strip()

      cursor.execute(
          """
                UPDATE users SET fullname = ?, phone = ?, email = ?
                WHERE username = ?
                """,
          (fullname, phone, email, username),
      )
      conn.commit()
      flash("บันทึกการแก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว ✅", "success")

    # เปลี่ยนรหัสผ่าน
    elif action == "change_password":
      current_pass = (request.form.get("current_password") or "").strip()
      new_pass = (request.form.get("new_password") or "").strip()
      confirm_pass = (request.form.get("confirm_password") or "").strip()

      cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
      user_row = cursor.fetchone()

      if not user_row or user_row["password"] != current_pass:
        flash("รหัสผ่านปัจจุบันไม่ถูกต้อง", "error")
      elif new_pass != confirm_pass:
        flash("รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน", "error")
      elif len(new_pass) < 4:
        flash("รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 4 ตัวอักษร", "error")
      else:
        cursor.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (new_pass, username),
        )
        conn.commit()
        flash("เปลี่ยนรหัสผ่านใหม่เรียบร้อยแล้ว 🔒", "success")

    # โหมดสลับสิทธิ์ Admin/User สำหรับทดสอบ
    elif action == "toggle_role":
      cursor.execute("SELECT role FROM users WHERE username = ?", (username,))
      user_row = cursor.fetchone()
      new_role = "admin" if user_row["role"] != "admin" else "user"
      cursor.execute(
          "UPDATE users SET role = ? WHERE username = ?", (new_role, username)
      )
      conn.commit()
      flash(f"สลับบทบาทเป็น '{new_role}' สำเร็จแล้ว", "info")

    conn.close()
    return redirect(url_for("profile"))

  conn.close()
  return render_template("profile.html")


# 6. หน้าเลือกออพชั่นเสริมและส่งคำขอจองแพ็กเกจ
@app.route("/book/<package_id>", methods=["GET", "POST"])
def book_package(package_id):
  if "user" not in session:
    flash("กรุณาเข้าสู่ระบบก่อนทำการจองอีเว้นท์", "error")
    return redirect(url_for("login"))

  pkg = EVENT_PACKAGES.get(package_id)
  if not pkg:
    flash("ไม่พบแพ็กเกจที่คุณเลือก", "error")
    return redirect(url_for("hello_world"))

  if request.method == "POST":
    options = request.form.getlist("options")
    event_date = (request.form.get("event_date") or "").strip()
    note = (request.form.get("note") or "").strip()

    if not event_date:
      flash("กรุณาเลือกวันที่ต้องการจัดงานแข่งขัน", "error")
      return render_template("book.html", package=pkg)

    # คำนวณราคาออพชั่นเสริม
    extra_price = 0
    option_details = []
    for opt in options:
      if opt == "ref_extra":
        extra_price += 1500
        option_details.append("เพิ่มกรรมการเสริม (+1,500 บาท)")
      elif opt == "live_stream":
        extra_price += 3000
        option_details.append("ระบบ Live Streaming สด (+3,000 บาท)")
      elif opt == "water_snack":
        extra_price += 1000
        option_details.append("ชุดน้ำดื่มและอาหารว่าง (+1,000 บาท)")

    total_price = pkg["price"] + extra_price

    # บันทึกข้อมูลลงฐานข้อมูล SQLite ตาราง bookings (สถานะเริ่มต้นเป็น 'pending' รอแอดมินคอนเฟิร์ม)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT INTO bookings (username, package_id, package_name, base_price, options, extra_price, total_price, event_date, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
        (
            session.get("user"),
            pkg["id"],
            pkg["name"],
            pkg["price"],
            json.dumps(option_details, ensure_ascii=False),
            extra_price,
            total_price,
            event_date,
            note,
        ),
    )
    conn.commit()
    conn.close()

    flash(
        f"🎉 ส่งคำขอจองอีเว้นท์สำเร็จ! (ราคารวม {total_price:,} บาท)"
        " กรุณารอผู้จัดตรวจสอบและคอนเฟิร์มสถานะคิวงาน",
        "success",
    )
    return redirect(url_for("my_bookings"))

  return render_template("book.html", package=pkg)


# 7. หน้าดูประวัติการจองของผู้ใช้ (My Bookings)
@app.route("/my-bookings")
def my_bookings():
  if "user" not in session:
    flash("กรุณาเข้าสู่ระบบก่อนดูประวัติการจอง", "error")
    return redirect(url_for("login"))

  current_user = session.get("user")
  conn = get_db()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT * FROM bookings WHERE username = ? ORDER BY id DESC",
      (current_user,),
  )
  raw_bookings = cursor.fetchall()
  conn.close()

  # แปลง options JSON เป็น list เพื่อแสดงผลใน template
  processed_bookings = []
  for b in raw_bookings:
    item = dict(b)
    try:
      item["options_list"] = json.loads(b["options"]) if b["options"] else []
    except Exception:
      item["options_list"] = []
    processed_bookings.append(item)

  return render_template("my_bookings.html", bookings=processed_bookings)


# 8. หน้าจัดการการจองของผู้ดูแลระบบ (Admin Bookings Management: คอนเฟิร์ม ว่าง / เต็ม)
@app.route("/admin/bookings", methods=["GET", "POST"])
def admin_bookings():
  if "user" not in session:
    flash("กรุณาเข้าสู่ระบบก่อน", "error")
    return redirect(url_for("login"))

  username = session.get("user")
  conn = get_db()
  cursor = conn.cursor()
  cursor.execute("SELECT role FROM users WHERE username = ?", (username,))
  user_row = cursor.fetchone()

  # ตรวจสอบสิทธิ์ Admin
  if not user_row or user_row["role"] != "admin":
    conn.close()
    flash(
        "ขออภัย หน้าดังกล่าวสงวนไว้สำหรับผู้ดูแลระบบ (Admin) เท่านั้น", "error"
    )
    return redirect(url_for("hello_world"))

  # ดำเนินการอัปเดตสถานะหรือลบรายการ
  if request.method == "POST":
    action = request.form.get("action")
    booking_id = request.form.get("booking_id")

    if action == "update_status":
      new_status = request.form.get("new_status")
      admin_note = (request.form.get("admin_note") or "").strip()

      cursor.execute(
          """
                UPDATE bookings SET status = ?, admin_note = ?
                WHERE id = ?
                """,
          (new_status, admin_note, booking_id),
      )
      conn.commit()

      status_text = (
          "✅ คอนเฟิร์มว่า 'ว่าง' (อนุมัติ)"
          if new_status == "confirmed"
          else "❌ คอนเฟิร์มว่า 'เต็ม' (ปฏิเสธ)"
      )
      flash(f"อัปเดตรายการจอง #{booking_id} เป็น {status_text} สำเร็จ", "success")

    elif action == "delete_booking":
      cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
      conn.commit()
      flash(f"ลบรายการจอง #{booking_id} เรียบร้อยแล้ว", "info")

    conn.close()
    return redirect(url_for("admin_bookings"))

  # ดึงข้อมูลการจองทั้งหมด พร้อมข้อมูลติดต่อของผู้จอง
  cursor.execute("""
    SELECT b.*, u.email as user_email, u.phone as user_phone, u.fullname as user_fullname
    FROM bookings b
    LEFT JOIN users u ON b.username = u.username
    ORDER BY b.id DESC
    """)
  raw_bookings = cursor.fetchall()

  # สรุปสถิติ
  stats = {
      "total": len(raw_bookings),
      "pending": sum(1 for b in raw_bookings if b["status"] == "pending"),
      "confirmed": sum(1 for b in raw_bookings if b["status"] == "confirmed"),
      "rejected": sum(1 for b in raw_bookings if b["status"] == "rejected"),
  }

  processed_bookings = []
  for b in raw_bookings:
    item = dict(b)
    try:
      item["options_list"] = json.loads(b["options"]) if b["options"] else []
    except Exception:
      item["options_list"] = []
    processed_bookings.append(item)

  conn.close()
  return render_template(
      "admin_bookings.html", bookings=processed_bookings, stats=stats
  )


if __name__ == "__main__":
  app.run(debug=True)