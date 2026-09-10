import sys
from flask import Blueprint, request, jsonify
from fhir_service import get_or_create_fhir_patient, get_fhir_patient_record

fhir_bp = Blueprint("fhir_bp", __name__, url_prefix="/provider")

@fhir_bp.route("/health", methods=["GET"])
def fhir_health():
    return jsonify({"status": "FHIR module active"}), 200

@fhir_bp.route("/patient", methods=["POST"])
def create_or_sync_patient():
    data = request.get_json() or {}
    phone_number = data.get("phone_number")
    
    if not phone_number:
        return jsonify({"error": "Missing phone_number"}), 400
        
    try:
        fhir_patient_id = get_or_create_fhir_patient(phone_number, data.get("first_name"))
        return jsonify({"status": "success", "phone_number": phone_number, "fhir_patient_id": fhir_patient_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fhir_bp.route("/patient/<phone_number>", methods=["GET"])
def get_patient(phone_number):
    try:
        record = get_fhir_patient_record(phone_number)
        return jsonify({"status": "success", "phone_number": phone_number, "patient_record": record}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404
