import os
import psycopg2
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def get_db_connection():
    return psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "longevity_app"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASS"),
        host=f"/cloudsql/{os.environ.get('INSTANCE_CONNECTION_NAME')}"
    )

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "Lovable API is active and ready."}), 200

@app.route("/api/users", methods=["GET"])
def get_users():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, phone_number, first_name, last_name, email FROM users;")
        users = [
            {"id": row[0], "phone": row[1], "first_name": row[2], "last_name": row[3], "email": row[4]} 
            for row in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify(users), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
