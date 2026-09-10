import sys
from flask import Blueprint, request, jsonify
from fhir_service import (
    get_or_create_fhir_patient,
    get_fhir_patient_record,
    create_fhir_observation,
    get_fhir_patient_observations,
    delete_fhir_observation,
    PatientNotFoundError,
    UnsupportedTypeError
)

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
    except PatientNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error in /provider/patient POST: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

@fhir_bp.route("/patient/<phone_number>", methods=["GET"])
def get_patient(phone_number):
    try:
        record = get_fhir_patient_record(phone_number)
        return jsonify({"status": "success", "phone_number": phone_number, "patient_record": record}), 200
    except PatientNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error in GET /provider/patient/{phone_number}: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

@fhir_bp.route("/patient/<phone_number>/observation", methods=["POST"])
def add_observation(phone_number):
    data = request.get_json() or {}
    obs_type = data.get("type")
    value = data.get("value")
    unit = data.get("unit")
    
    if not obs_type or value is None or not unit:
        return jsonify({"error": "Missing 'type', 'value', or 'unit' in payload"}), 400
        
    try:
        obs_id = create_fhir_observation(phone_number, obs_type, float(value), unit)
        return jsonify({"status": "success", "phone_number": phone_number, "observation_id": obs_id}), 201
    except PatientNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except (UnsupportedTypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error in POST /provider/patient/{phone_number}/observation: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

@fhir_bp.route("/patient/<phone_number>/observations", methods=["GET"])
def list_observations(phone_number):
    try:
        observations = get_fhir_patient_observations(phone_number)
        return jsonify({"status": "success", "phone_number": phone_number, "count": len(observations), "observations": observations}), 200
    except PatientNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error in GET /provider/patient/{phone_number}/observations: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

@fhir_bp.route("/patient/<phone_number>/observation/<observation_id>", methods=["DELETE"])
def delete_observation(phone_number, observation_id):
    try:
        delete_fhir_observation(phone_number, observation_id)
        return jsonify({"status": "success", "message": f"Observation {observation_id} deleted."}), 200
    except PatientNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return jsonify({"error": error_msg}), 404
        print(f"Error in DELETE /provider/patient/{phone_number}/observation/{observation_id}: {e}", file=sys.stderr)
        return jsonify({"error": error_msg}), 500
