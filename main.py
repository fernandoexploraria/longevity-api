import os
import json
import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS
from services.whoop_service import get_whoop_status, force_whoop_refresh, get_whoop_latest, get_whoop_summary

app = Flask(__name__)
CORS(app)

def get_db_connection():
    db_user = os.environ.get("DB_USER", "postgres")
    db_pass = os.environ.get("DB_PASS")
    db_name = os.environ.get("DB_NAME", "longevity_app")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")
    return psycopg2.connect(
        dbname=db_name, user=db_user, password=db_pass, host=f"/cloudsql/{instance_connection_name}"
    )

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "Longevity API is active and ready."}), 200

@app.route("/api/patients", methods=["GET"])
def get_patients():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                u.id, 
                u.phone_number, 
                u.first_name, 
                u.last_name, 
                u.email, 
                u.height_meter, 
                u.weight_kilogram, 
                u.max_heart_rate,
                u.last_inbound_interaction,
                CASE WHEN w.whoop_user_id IS NOT NULL THEN TRUE ELSE FALSE END as is_whoop_connected,
                w.whoop_user_id,
                w.token_expires_at
            FROM users u
            LEFT JOIN whoop_connections w ON u.id = w.user_id;
        """)
        patients = []
        for row in cur.fetchall():
            patients.append({
                "id": row[0],
                "phone": row[1],
                "first_name": row[2],
                "last_name": row[3],
                "email": row[4],
                "height_meter": float(row[5]) if row[5] is not None else None,
                "weight_kilogram": float(row[6]) if row[6] is not None else None,
                "max_heart_rate": row[7],
                "last_inbound_interaction": row[8].isoformat() if row[8] else None,
                "is_whoop_connected": row[9],
                "whoop_user_id": row[10],
                "whoop_token_expires_at": row[11].isoformat() if row[11] else None
            })
        cur.close()
        conn.close()
        return jsonify(patients), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/patients/<phone_number>", methods=["GET"])
def get_patient_detail(phone_number):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                u.id, u.phone_number, u.first_name, u.last_name, u.email, 
                u.height_meter, u.weight_kilogram, u.max_heart_rate, u.last_inbound_interaction,
                CASE WHEN w.whoop_user_id IS NOT NULL THEN TRUE ELSE FALSE END as is_whoop_connected,
                w.whoop_user_id
            FROM users u
            LEFT JOIN whoop_connections w ON u.id = w.user_id
            WHERE u.phone_number = %s;
        """, (phone_number,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "Patient not found"}), 404
            
        patient_data = {
            "id": row[0],
            "phone": row[1],
            "first_name": row[2],
            "last_name": row[3],
            "email": row[4],
            "height_meter": float(row[5]) if row[5] is not None else None,
            "weight_kilogram": float(row[6]) if row[6] is not None else None,
            "max_heart_rate": row[7],
            "last_inbound_interaction": row[8].isoformat() if row[8] else None,
            "is_whoop_connected": row[9],
            "whoop_user_id": row[10]
        }
        
        cur.execute("""
            SELECT id, app_name, user_id, session_id, invocation_id, author, content, timestamp
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC
            LIMIT 500;
        """, (phone_number,))
        
        conversation_history = []
        for e_row in cur.fetchall():
            content_data = e_row[6]
            if isinstance(content_data, str):
                try:
                    content_data = json.loads(content_data)
                except Exception:
                    pass
            conversation_history.append({
                "event_id": e_row[0],
                "app_name": e_row[1],
                "user_id": e_row[2],
                "session_id": e_row[3],
                "invocation_id": e_row[4],
                "author": e_row[5],
                "content": content_data,
                "timestamp": e_row[7].isoformat() if e_row[7] else None
            })
            
        patient_data["conversation_history"] = conversation_history
        cur.close()
        conn.close()
        return jsonify(patient_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/patients/<phone_number>/whoop/status", methods=["GET"])
def api_whoop_status(phone_number):
    result = get_whoop_status(phone_number)
    if result.get("status") == "error":
        return jsonify(result), 409
    return jsonify(result), 200

@app.route("/api/patients/<phone_number>/whoop/refresh", methods=["POST"])
def api_whoop_refresh(phone_number):
    result = force_whoop_refresh(phone_number)
    if result.get("status") == "error":
        return jsonify(result), 409
    return jsonify(result), 200

@app.route("/api/patients/<phone_number>/whoop/latest", methods=["GET"])
def api_whoop_latest(phone_number):
    result = get_whoop_latest(phone_number)
    if result.get("status") == "error":
        # 409 means token invalid or patient not found based on our internal spec
        return jsonify(result), 409 if result.get("reason") in ["not_connected", "whoop_reauth_required"] else 500
    return jsonify(result), 200

@app.route("/api/patients/<phone_number>/whoop/summary", methods=["GET"])
def api_whoop_summary(phone_number):
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
        
    result = get_whoop_summary(phone_number, days)
    if result.get("status") == "error":
        return jsonify(result), 409 if result.get("reason") in ["not_connected", "whoop_reauth_required"] else 500
    return jsonify(result), 200   

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
