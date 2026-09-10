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

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
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
    """Generates an OAuth 2.0 token using Cloud Run's service account identity."""
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials, _ = google.auth.default(scopes=scopes)
    credentials.refresh(Request())
    return credentials.token

def get_or_create_fhir_patient(phone_number: str, first_name: str = None) -> str:
    """
    Called when Provider clicks 'Create Medical Record' in Lovable.
    Retrieves the existing user from PostgreSQL, uses their saved WhatsApp profile data 
    to create the GCP FHIR Patient resource, and links the fhir_patient_id back to PostgreSQL.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT fhir_patient_id, first_name FROM users WHERE phone_number = %s;",
            (phone_number,)
        )
        row = cur.fetchone()

        if not row:
            raise Exception(f"User with phone_number '{phone_number}' does not exist in PostgreSQL.")

        fhir_patient_id, db_first_name = row[0], row[1]

        if fhir_patient_id:
            return fhir_patient_id

        patient_name = first_name if first_name else (db_first_name if db_first_name else "User")

        fhir_url = (
            f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Patient"
        )

        headers = {
            "Authorization": f"Bearer {get_gcp_token()}",
            "Content-Type": "application/fhir+json; charset=utf-8"
        }

        payload = {
            "resourceType": "Patient",
            "identifier": [
                {
                    "system": "https://whatsapp.com",
                    "value": phone_number
                }
            ],
            "name": [
                {
                    "use": "official",
                    "given": [patient_name]
                }
            ]
        }

        res = requests.post(fhir_url, headers=headers, json=payload)

        if res.status_code in [200, 201]:
            fhir_patient_id = res.json().get("id")

            cur.execute(
                "UPDATE users SET fhir_patient_id = %s WHERE phone_number = %s;",
                (fhir_patient_id, phone_number)
            )
            conn.commit()
            return fhir_patient_id
        else:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")

    finally:
        cur.close()
        conn.close()

def get_fhir_patient_record(phone_number: str) -> dict:
    """
    Retrieves the raw FHIR Patient resource from GCP Healthcare API using the user's phone number.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()

        if not row or not row[0]:
            raise Exception(f"No FHIR Patient record found for phone number '{phone_number}'.")

        fhir_patient_id = row[0]

        fhir_url = (
            f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Patient/{fhir_patient_id}"
        )

        headers = {
            "Authorization": f"Bearer {get_gcp_token()}",
            "Accept": "application/fhir+json"
        }

        res = requests.get(fhir_url, headers=headers)

        if res.status_code == 200:
            return res.json()
        else:
            raise Exception(f"GCP FHIR Store Error ({res.status_code}): {res.text}")

    finally:
        cur.close()
        conn.close()

def create_fhir_observation(phone_number: str, obs_type: str, value: float, unit: str) -> str:
    """
    Creates a new FHIR Observation resource (e.g., Weight, Height) linked to the patient.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row or not row[0]:
            raise Exception(f"No FHIR Patient record found for '{phone_number}'.")
        
        fhir_patient_id = row[0]
        
        if obs_type.lower() == "weight":
            code = {"coding": [{"system": "http://loinc.org", "code": "29463-7", "display": "Body Weight"}]}
        elif obs_type.lower() == "height":
            code = {"coding": [{"system": "http://loinc.org", "code": "8302-2", "display": "Body Height"}]}
        elif obs_type.lower() == "heart_rate":
            code = {"coding": [{"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}]}
        else:
            raise Exception(f"Unsupported observation type: {obs_type}")

        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Observation"
        headers = {"Authorization": f"Bearer {get_gcp_token()}", "Content-Type": "application/fhir+json; charset=utf-8"}
        
        payload = {
            "resourceType": "Observation",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs", "display": "Vital Signs"}]}],
            "code": code,
            "subject": {"reference": f"Patient/{fhir_patient_id}"},
            "effectiveDateTime": datetime.datetime.utcnow().isoformat() + "Z",
            "valueQuantity": {
                "value": value,
                "unit": unit,
                "system": "http://unitsofmeasure.org",
                "code": unit
            }
        }

        res = requests.post(fhir_url, headers=headers, json=payload)
        if res.status_code in [200, 201]:
            return res.json().get("id")
        else:
            raise Exception(f"GCP FHIR Error: {res.text}")
    finally:
        cur.close()
        conn.close()
