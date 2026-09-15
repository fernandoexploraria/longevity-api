import os
import requests
import psycopg2
import concurrent.futures
from datetime import datetime, timezone, timedelta

def get_db_connection():
    db_user = os.environ.get("DB_USER", "postgres")
    db_pass = os.environ.get("DB_PASS")
    db_name = os.environ.get("DB_NAME", "longevity_app")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")
    return psycopg2.connect(
        dbname=db_name, user=db_user, password=db_pass, host=f"/cloudsql/{instance_connection_name}"
    )

def _get_valid_whoop_token(phone_number: str, force_refresh: bool = False) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT w.access_token, w.refresh_token, w.token_expires_at, w.whoop_user_id
            FROM whoop_connections w
            JOIN users u ON u.id = w.user_id
            WHERE u.phone_number = %s;
        """, (phone_number,))
        row = cur.fetchone()
        
        if not row:
            raise ValueError("not_connected")
            
        access_token, refresh_token, expires_at, whoop_user_id = row
        now = datetime.now(timezone.utc)
        
        if not force_refresh and expires_at and expires_at.replace(tzinfo=timezone.utc) > (now + timedelta(seconds=60)):
            return access_token

        client_id = os.environ.get("WHOOP_CLIENT_ID")
        client_secret = os.environ.get("WHOOP_CLIENT_SECRET")
        
        res = requests.post("https://api.prod.whoop.com/oauth/oauth2/token", data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "offline"
        })
        
        if res.status_code in [400, 401]:
            raise ValueError("whoop_reauth_required")
            
        res.raise_for_status()
        token_data = res.json()
        
        new_access = token_data["access_token"]
        new_refresh = token_data["refresh_token"]
        expires_in = token_data["expires_in"]
        
        cur.execute("""
            UPDATE whoop_connections 
            SET access_token = %s, refresh_token = %s, token_expires_at = NOW() + %s * INTERVAL '1 second'
            FROM users
            WHERE whoop_connections.user_id = users.id AND users.phone_number = %s;
        """, (new_access, new_refresh, expires_in, phone_number))
        conn.commit()
        return new_access
    finally:
        cur.close()
        conn.close()

def get_whoop_status(phone_number: str) -> dict:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT w.whoop_user_id, w.scopes, w.token_expires_at
            FROM whoop_connections w
            JOIN users u ON u.id = w.user_id
            WHERE u.phone_number = %s;
        """, (phone_number,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "reason": "not_connected"}
            
        whoop_user_id, scopes, expires_at = row
        now = datetime.now(timezone.utc)
        is_expired = expires_at.replace(tzinfo=timezone.utc) <= now if expires_at else True
        
        return {
            "status": "success",
            "phone_number": phone_number,
            "connected": True,
            "expired": is_expired,
            "whoop_user_id": whoop_user_id,
            "scopes": scopes,
            "token_expires_at": expires_at.isoformat() if expires_at else None
        }
    finally:
        cur.close()
        conn.close()

def force_whoop_refresh(phone_number: str) -> dict:
    try:
        new_token = _get_valid_whoop_token(phone_number, force_refresh=True)
        headers = {"Authorization": f"Bearer {new_token}", "User-Agent": "Longevity-Console/1.0"}
        res = requests.get("https://api.prod.whoop.com/developer/v2/user/profile/basic", headers=headers)
        if res.status_code in [400, 401]:
            raise ValueError("whoop_reauth_required")
        return get_whoop_status(phone_number)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}

# --- NEW DATA FETCHING LOGIC BELOW ---

def _fetch_whoop_paginated(token: str, base_url: str, max_pages: int = 6):
    """Helper to fetch up to max_pages of WHOOP data."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Longevity-Console/1.0"}
    records = []
    next_token = None
    pages = 0
    
    while pages < max_pages:
        url = base_url if not next_token else f"{base_url}&nextToken={next_token}"
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()
        records.extend(data.get("records", []))
        
        next_token = data.get("next_token")
        if not next_token:
            break
        pages += 1
    return records

def get_whoop_latest(phone_number: str) -> dict:
    """Fetches the single most recent cycle and recovery."""
    try:
        token = _get_valid_whoop_token(phone_number)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}

    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Longevity-Console/1.0"}
    
    def fetch_one(endpoint):
        try:
            res = requests.get(f"https://api.prod.whoop.com/developer/v2/{endpoint}?limit=1", headers=headers)
            res.raise_for_status()
            records = res.json().get("records", [])
            return records[0] if records else None
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_cycle = executor.submit(fetch_one, "cycle")
        f_recovery = executor.submit(fetch_one, "recovery")
        raw_cycle = f_cycle.result()
        raw_recovery = f_recovery.result()

    cycle_data = None
    if raw_cycle:
        c_score = raw_cycle.get("score", {})
        cycle_data = {
            "id": raw_cycle.get("id"), "start": raw_cycle.get("start"), "end": raw_cycle.get("end"),
            "strain": c_score.get("strain"), "average_heart_rate": c_score.get("average_heart_rate"),
            "max_heart_rate": c_score.get("max_heart_rate"), "kilojoule": c_score.get("kilojoule"),
            "score_state": raw_cycle.get("score_state")
        }

    recovery_data = None
    if raw_recovery:
        r_score = raw_recovery.get("score", {})
        recovery_data = {
            "recovery_score": r_score.get("recovery_score"), "resting_heart_rate": r_score.get("resting_heart_rate"),
            "hrv_rmssd_milli": r_score.get("hrv_rmssd_milli"), "spo2_percentage": r_score.get("spo2_percentage"),
            "skin_temp_celsius": r_score.get("skin_temp_celsius"), "created_at": raw_recovery.get("created_at")
        }

    return {
        "status": "success",
        "phone_number": phone_number,
        "cycle": cycle_data,
        "recovery": recovery_data
    }

def get_whoop_summary(phone_number: str, days: int = 30) -> dict:
    """Fetches full historical context across 6 endpoints in parallel."""
    try:
        token = _get_valid_whoop_token(phone_number)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}

    # Clamp days between 1 and 90
    days = max(1, min(days, 90))
    now = datetime.now(timezone.utc)
    start_time = (now - timedelta(days=days)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    end_time = now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Longevity-Console/1.0"}
    errors = []

    def safe_fetch(url_type, url, is_list=False):
        try:
            if is_list:
                return _fetch_whoop_paginated(token, url, max_pages=6)
            res = requests.get(url, headers=headers)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            errors.append(f"{url_type} failed: {str(e)}")
            return [] if is_list else None

    # Fetch all endpoints concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        f_prof = executor.submit(safe_fetch, "profile", "https://api.prod.whoop.com/developer/v2/user/profile/basic")
        f_body = executor.submit(safe_fetch, "body", "https://api.prod.whoop.com/developer/v2/user/measurement/body")
        f_cyc = executor.submit(safe_fetch, "cycles", f"https://api.prod.whoop.com/developer/v2/cycle?start={start_time}&end={end_time}&limit=25", True)
        f_rec = executor.submit(safe_fetch, "recoveries", f"https://api.prod.whoop.com/developer/v2/recovery?start={start_time}&end={end_time}&limit=25", True)
        f_slp = executor.submit(safe_fetch, "sleeps", f"https://api.prod.whoop.com/developer/v2/activity/sleep?start={start_time}&end={end_time}&limit=25", True)
        f_wkt = executor.submit(safe_fetch, "workouts", f"https://api.prod.whoop.com/developer/v2/activity/workout?start={start_time}&end={end_time}&limit=25", True)

        raw_prof = f_prof.result() or {}
        raw_body = f_body.result() or {}
        raw_cyc = f_cyc.result()
        raw_rec = f_rec.result()
        raw_slp = f_slp.result()
        raw_wkt = f_wkt.result()

    # Mappers
    def m2m(val): return round(val / 60000) if val else 0

    cycles = [{"id": c.get("id"), "start": c.get("start"), "end": c.get("end"), "strain": c.get("score",{}).get("strain"), "average_heart_rate": c.get("score",{}).get("average_heart_rate"), "max_heart_rate": c.get("score",{}).get("max_heart_rate"), "kilojoule": c.get("score",{}).get("kilojoule"), "score_state": c.get("score_state")} for c in raw_cyc]
    
    recoveries = [{"cycle_id": r.get("cycle_id"), "sleep_id": r.get("sleep_id"), "created_at": r.get("created_at"), "recovery_score": r.get("score",{}).get("recovery_score"), "resting_heart_rate": r.get("score",{}).get("resting_heart_rate"), "hrv_rmssd_milli": r.get("score",{}).get("hrv_rmssd_milli"), "spo2_percentage": r.get("score",{}).get("spo2_percentage"), "skin_temp_celsius": r.get("score",{}).get("skin_temp_celsius"), "user_calibrating": r.get("user_calibrating")} for r in raw_rec]

    sleeps = []
    for s in raw_slp:
        sc = s.get("score", {})
        st = sc.get("stage_summary", {})
        nd = sc.get("sleep_needed", {})
        needed_ms = nd.get("baseline_milli", 0) + nd.get("need_from_sleep_debt_milli", 0) + nd.get("need_from_recent_naps_milli", 0)
        sleeps.append({
            "id": s.get("id"), "start": s.get("start"), "end": s.get("end"), "nap": s.get("nap"),
            "in_bed_minutes": m2m(st.get("total_in_bed_time_milli", 0)), "awake_minutes": m2m(st.get("total_awake_time_milli", 0)),
            "light_minutes": m2m(st.get("total_light_sleep_time_milli", 0)), "rem_minutes": m2m(st.get("total_rem_sleep_time_milli", 0)),
            "deep_minutes": m2m(st.get("total_slow_wave_sleep_time_milli", 0)), "needed_minutes": m2m(needed_ms),
            "respiratory_rate": sc.get("respiratory_rate"), "performance_percentage": sc.get("sleep_performance_percentage"),
            "efficiency_percentage": sc.get("sleep_efficiency_percentage"), "consistency_percentage": sc.get("sleep_consistency_percentage")
        })

    workouts = []
    for w in raw_wkt:
        sc = w.get("score", {})
        zd = sc.get("zone_durations", {})
        zm = [m2m(zd.get("zone_zero_milli", 0)), m2m(zd.get("zone_one_milli", 0)), m2m(zd.get("zone_two_milli", 0)),
              m2m(zd.get("zone_three_milli", 0)), m2m(zd.get("zone_four_milli", 0)), m2m(zd.get("zone_five_milli", 0))]
        workouts.append({
            "id": w.get("id"), "start": w.get("start"), "end": w.get("end"), "sport_name": w.get("sport_name", ""),
            "strain": sc.get("strain"), "average_heart_rate": sc.get("average_heart_rate"), "max_heart_rate": sc.get("max_heart_rate"),
            "kilojoule": sc.get("kilojoule"), "distance_meter": sc.get("distance_meter"), "altitude_gain_meter": sc.get("altitude_gain_meter"),
            "zone_minutes": zm
        })

    # Sort newest first and cap arrays
    def date_sort(x): return x.get("start") or x.get("created_at") or ""
    cycles = sorted(cycles, key=date_sort, reverse=True)[:30]
    recoveries = sorted(recoveries, key=date_sort, reverse=True)[:30]
    sleeps = sorted(sleeps, key=date_sort, reverse=True)[:40]
    workouts = sorted(workouts, key=date_sort, reverse=True)[:60]

    return {
        "status": "success",
        "phone_number": phone_number,
        "window": {"start": start_time, "end": end_time, "days": days},
        "profile": {"first_name": raw_prof.get("first_name"), "last_name": raw_prof.get("last_name"), "email": raw_prof.get("email")},
        "body": {"height_meter": raw_body.get("height_meter"), "weight_kilogram": raw_body.get("weight_kilogram"), "max_heart_rate": raw_body.get("max_heart_rate")},
        "cycles": cycles,
        "recoveries": recoveries,
        "sleeps": sleeps,
        "workouts": workouts,
        "errors": errors
    }
