import os
import sys
import requests
import psycopg2
import google.auth
from google.auth.transport.requests import Request
import datetime

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "longevity-agent-507715")
LOCATION = "us-central1"
DATASET_ID = "longevity-clinical-data"
STORE_ID = "user-vitals-store"

class PatientNotFoundError(Exception):
    pass

class UnsupportedTypeError(Exception):
    pass

LOINC_MAP = {
    "weight": {"code": "29463-7", "display": "Body Weight", "default_unit": "kg"},
    "body_weight": {"code": "29463-7", "display": "Body Weight", "default_unit": "kg"},
    "height": {"code": "8302-2", "display": "Body Height", "default_unit": "cm"},
    "body_height": {"code": "8302-2", "display": "Body Height", "default_unit": "cm"},
    "heart_rate": {"code": "8867-4", "display": "Heart rate", "default_unit": "bpm"},
    "blood_glucose": {"code": "15074-8", "display": "Glucose in Blood", "default_unit": "mg/dL"},
    "body_temperature": {"code": "8310-5", "display": "Body temperature", "default_unit": "C"},
    "oxygen_saturation": {"code": "2708-6", "display": "Oxygen saturation", "default_unit": "%"},
    "spo2": {"code": "2708-6", "display": "Oxygen saturation", "default_unit": "%"},
    "body_fat": {"code": "41982-0", "display": "Percentage body fat", "default_unit": "%"},
}

CODE_TO_TYPE_MAP = {v["code"]: k for k, v in LOINC_MAP.items()}

def get_db_connection():
    db_user = os.environ.get("DB_USER", "postgres")
    db_pass = os.environ.get("DB_PASS")
    db_name = os.environ.get("DB_NAME", "longevity_app")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")
    db_host = os.environ.get("DB_HOST")

    host = db_host if db_host else f"/cloudsql/{instance_connection_name}"
    return psycopg2.connect(
        dbname=db_name, user=db_user, password=db_pass, host=host
    )

def get_gcp_token() -> str:
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials, _ = google.auth.default(scopes=scopes)
    credentials.refresh(Request())
    return credentials.token

def get_or_create_fhir_patient(phone_number: str, first_name: str = None) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id, first_name FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row:
            raise PatientNotFoundError(f"User with phone_number '{phone_number}' does not exist.")

        fhir_patient_id, db_first_name = row[0], row[1]
        if fhir_patient_id:
            return fhir_patient_id

        patient_name = first_name if first_name else (db_first_name if db_first_name else "User")
        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Patient"
        headers = {"Authorization": f"Bearer {get_gcp_token()}", "Content-Type": "application/fhir+json; charset=utf-8"}
        
        payload = {
            "resourceType": "Patient",
            "identifier": [{"system": "https://whatsapp.com", "value": phone_number}],
            "name": [{"use": "official", "given": [patient_name]}]
        }

        res = requests.post(fhir_url, headers=headers, json=payload)
        if res.status_code in [200, 201]:
            fhir_patient_id = res.json().get("id")
            cur.execute("UPDATE users SET fhir_patient_id = %s WHERE phone_number = %s;", (fhir_patient_id, phone_number))
            conn.commit()
            return fhir_patient_id
        else:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")
    finally:
        cur.close()
        conn.close()

def get_fhir_patient_record(phone_number: str) -> dict:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row:
            raise PatientNotFoundError(f"User with phone_number '{phone_number}' does not exist.")
        if not row[0]:
            raise PatientNotFoundError(f"No FHIR Patient record found for phone_number '{phone_number}'.")

        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Patient/{row[0]}"
        headers = {"Authorization": f"Bearer {get_gcp_token()}", "Accept": "application/fhir+json"}

        res = requests.get(fhir_url, headers=headers)
        if res.status_code == 200:
            return res.json()
        else:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")
    finally:
        cur.close()
        conn.close()

def create_fhir_observation(phone_number: str, obs_type: str, value: float, unit: str) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row:
            raise PatientNotFoundError(f"User with phone_number '{phone_number}' does not exist.")
        if not row[0]:
            raise PatientNotFoundError(f"No FHIR Patient record found for '{phone_number}'.")
        
        obs_key = obs_type.lower().strip()
        if obs_key not in LOINC_MAP:
            supported = ", ".join(sorted(set(LOINC_MAP.keys())))
            raise UnsupportedTypeError(f"Unsupported observation type '{obs_type}'. Supported types: {supported}")

        type_info = LOINC_MAP[obs_key]
        code_payload = {"coding": [{"system": "http://loinc.org", "code": type_info["code"], "display": type_info["display"]}]}

        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Observation"
        headers = {"Authorization": f"Bearer {get_gcp_token()}", "Content-Type": "application/fhir+json; charset=utf-8"}
        
        payload = {
            "resourceType": "Observation",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs", "display": "Vital Signs"}]}],
            "code": code_payload,
            "subject": {"reference": f"Patient/{row[0]}"},
            "effectiveDateTime": datetime.datetime.utcnow().isoformat() + "Z",
            "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}
        }

        res = requests.post(fhir_url, headers=headers, json=payload)
        if res.status_code in [200, 201]:
            return res.json().get("id")
        else:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")
    finally:
        cur.close()
        conn.close()

def get_fhir_patient_observations(phone_number: str) -> list:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row:
            raise PatientNotFoundError(f"User with phone_number '{phone_number}' does not exist.")
        if not row[0]:
            raise PatientNotFoundError(f"No FHIR Patient record found for '{phone_number}'.")

        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Observation?subject=Patient/{row[0]}"
        headers = {"Authorization": f"Bearer {get_gcp_token()}", "Accept": "application/fhir+json"}

        res = requests.get(fhir_url, headers=headers)
        if res.status_code != 200:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")

        bundle = res.json() or {}
        entries = bundle.get("entry", [])
        observations = []

        for entry in entries:
            resource = entry.get("resource", {})
            coding = resource.get("code", {}).get("coding", [])
            code = coding[0].get("code") if coding else None
            
            observations.append({
                "id": resource.get("id"),
                "type": CODE_TO_TYPE_MAP.get(code, code or "unknown"),
                "display": coding[0].get("display") if coding else None,
                "code": code,
                "value": resource.get("valueQuantity", {}).get("value"),
                "unit": resource.get("valueQuantity", {}).get("unit"),
                "date": resource.get("effectiveDateTime")
            })

        observations.sort(key=lambda x: x.get("date") or "", reverse=True)
        return observations
    finally:
        cur.close()
        conn.close()

def delete_fhir_observation(phone_number: str, observation_id: str) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row or not row[0]:
            raise PatientNotFoundError(f"No FHIR Patient record found for '{phone_number}'.")

        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Observation/{observation_id}"
        headers = {"Authorization": f"Bearer {get_gcp_token()}"}
        
        res = requests.delete(fhir_url, headers=headers)
        if res.status_code in [200, 204]:
            return True
        elif res.status_code == 404:
            raise Exception(f"Observation {observation_id} not found.")
        else:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")
    finally:
        cur.close()
        conn.close()
