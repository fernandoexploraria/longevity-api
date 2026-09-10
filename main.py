import os
import json
import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS

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

@app.route("/api/patients/<phone>", methods=["GET"])
def get_patient_detail(phone):
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
        """, (phone,))
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
        """, (phone,))
        
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

@app.route("/api/patients/<phone>/fhir", methods=["GET"])
def get_patient_fhir(phone):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, phone_number, first_name, last_name, email, height_meter, weight_kilogram
            FROM users 
            WHERE phone_number = %s;
        """, (phone,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if not row:
            return jsonify({"error": "Patient not found"}), 404
            
        fhir_data = {
            "fhir_patient_id": f"pat-{row[0]}",
            "db_id": row[0],
            "phone_number": row[1],
            "first_name": row[2],
            "last_name": row[3],
            "email": row[4],
            "height_meter": float(row[5]) if row[5] is not None else None,
            "weight_kilogram": float(row[6]) if row[6] is not None else None,
            "resource_type": "Patient"
        }
        return jsonify(fhir_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/patients/<phone>/whoop-connection", methods=["GET"])
def get_whoop_connection(phone):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                w.whoop_user_id, 
                w.access_token, 
                w.refresh_token, 
                w.token_expires_at, 
                w.scopes
            FROM whoop_connections w
            JOIN users u ON u.id = w.user_id
            WHERE u.phone_number = %s;
        """, (phone,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if not row:
            return jsonify({"error": "Whoop connection not found for this patient"}), 404
            
        connection_data = {
            "whoop_user_id": row[0],
            "access_token": row[1],
            "refresh_token": row[2],
            "token_expires_at": row[3].isoformat() if row[3] else None,
            "scopes": row[4]
        }
        return jsonify(connection_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/patients/<phone>/whoop-connection", methods=["PUT"])
def update_whoop_connection(phone):
    try:
        data = request.get_json() or {}
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)
        
        if not new_access_token or not new_refresh_token:
            return jsonify({"error": "Missing access_token or refresh_token in payload"}), 400
            
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE whoop_connections 
            SET 
                access_token = %s,
                refresh_token = %s,
                token_expires_at = NOW() + %s * INTERVAL '1 second'
            FROM users
            WHERE whoop_connections.user_id = users.id AND users.phone_number = %s
            RETURNING whoop_connections.whoop_user_id, whoop_connections.token_expires_at;
        """, (new_access_token, new_refresh_token, expires_in, phone))
        
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if not updated:
            return jsonify({"error": "Patient or Whoop connection not found"}), 404
            
        return jsonify({
            "status": "success",
            "whoop_user_id": updated[0],
            "token_expires_at": updated[1].isoformat() if updated[1] else None
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))