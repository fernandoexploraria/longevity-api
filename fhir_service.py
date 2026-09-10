import os
import requests
import psycopg2
import google.auth
from google.auth.transport.requests import Request

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "longevity-agent-507715")
LOCATION = "us-central1"
DATASET_ID = "longevity-clinical-data"
STORE_ID = "user-vitals-store"

def get_db_connection():
    db_user = os.environ.get("DB_USER", "postgres")
    db_pass = os.environ.get("DB_PASS")
    db_name = os.environ.get("DB_NAME", "longevity_app")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")
    return psycopg2.connect(
        dbname=db_name, user=db_user, password=db_pass, host=f"/cloudsql/{instance_connection_name}"
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
            raise Exception(f"User '{phone_number}' not found.")
        
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
            raise Exception(f"GCP FHIR Error: {res.text}")
    finally:
        cur.close()
        conn.close()

def get_fhir_patient_record(phone_number: str) -> dict:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT fhir_patient_id FROM users WHERE phone_number = %s;", (phone_number,))
        row = cur.fetchone()
        if not row or not row[0]:
            raise Exception("No FHIR Patient record found.")

        fhir_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{STORE_ID}/fhir/Patient/{row[0]}"
        headers = {"Authorization": f"Bearer {get_gcp_token()}", "Accept": "application/fhir+json"}
        
        res = requests.get(fhir_url, headers=headers)
        if res.status_code == 200:
            return res.json()
        else:
            raise Exception(f"GCP FHIR Error: {res.text}")
    finally:
        cur.close()
        conn.close()
