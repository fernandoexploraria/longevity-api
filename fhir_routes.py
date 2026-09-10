import sys
from flask import Blueprint, request, jsonify
from fhir_service import get_or_create_fhir_patient, get_fhir_patient_record, create_fhir_observation

fhir_bp = Blueprint("fhir_bp", __name__, url_prefix="/provider")

@fhir_bp.route("/health", methods=["GET"])
def fhir_health():
    """Simple health check for the FHIR Blueprint."""
    return jsonify({"status": "FHIR module active"}), 200

@fhir_bp.route("/patient", methods=["POST"])
def create_or_sync_patient():
    """
    Endpoint called by Lovable frontend to ensure a user exists as a FHIR Patient in GCP.
    Payload: {"phone_number": "525513001529"}
    """
    data = request.get_json() or {}
    phone_number = data.get("phone_number")
    first_name = data.get("first_name")
    
    if not phone_number:
        return jsonify({"error": "Missing phone_number"}), 400
        
    try:
        fhir_patient_id = get_or_create_fhir_patient(phone_number, first_name)
        return jsonify({
            "status": "success",
            "phone_number": phone_number,
            "fhir_patient_id": fhir_patient_id
        }), 200
    except Exception as e:
        print(f"Error in /provider/patient POST: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

@fhir_bp.route("/patient/<phone_number>", methods=["GET"])
def get_patient(phone_number):
    """
    Endpoint called by Lovable frontend to fetch a user's FHIR Patient record.
    URL: GET /provider/patient/525513001529
    """
    try:
        record = get_fhir_patient_record(phone_number)
        return jsonify({
            "status": "success",
            "phone_number": phone_number,
            "patient_record": record
        }), 200
    except Exception as e:
        print(f"Error in GET /provider/patient/{phone_number}: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 404

@fhir_bp.route("/patient/<phone_number>/observation", methods=["POST"])
def add_observation(phone_number):
    """
    Endpoint called by Lovable to add a vital sign to a patient's FHIR record.
    Payload: {"type": "weight", "value": 75.5, "unit": "kg"}
    """
    data = request.get_json() or {}
    obs_type = data.get("type")
    value = data.get("value")
    unit = data.get("unit")
    
    if not obs_type or value is None or not unit:
        return jsonify({"error": "Missing type, value, or unit in payload"}), 400
        
    try:
        obs_id = create_fhir_observation(phone_number, obs_type, float(value), unit)
        return jsonify({
            "status": "success", 
            "phone_number": phone_number, 
            "observation_id": obs_id
        }), 201
    except Exception as e:
        print(f"Error in POST /provider/patient/{phone_number}/observation: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500
