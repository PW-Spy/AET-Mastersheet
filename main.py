import gspread
from google.oauth2.service_account import Credentials
import google.auth.transport.requests
import re
import time
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime  
import pytz                     
import concurrent.futures  
import random

# ==========================================
MASTER_SHEET_ID = os.environ.get("MASTER_SHEET_ID")
MASTER_TARGET_TAB = "AET Planner"  
SOURCE_TARGET_TAB = "AET Planner"    
LINKS_TAB = "Batch Links"
LOG_TAB = "Sync Log"
# ==========================================

def process_single_sheet(index, url, access_token, safe_range, current_time):
    url_clean = url.strip()
    if not url_clean or url_clean.startswith("Google Sheet Link"):
        return None
        
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url_clean)
    if not match:
        return {'data': [], 'log': [url_clean, "Failed", 0, "Invalid URL Format", current_time]}
        
    sid = match.group(1)
    api_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{safe_range}"
    
    # 🛡️ FIX 1: Retries 3 se badhakar 10 kar diye (Data miss nahi hoga)
    max_retries = 10
    time.sleep(random.uniform(0.5, 2.0))
    
    for attempt in range(1, max_retries + 1):
        try:
            req_obj = urllib.request.Request(api_url)
            req_obj.add_header('Authorization', f'Bearer {access_token}')
            
            response = urllib.request.urlopen(req_obj)
            data_json = json.loads(response.read().decode('utf-8'))
            values = data_json.get('values', [])
            
            filtered_data = []
            if len(values) > 0:
                for row in values:
                    if len(row) > 0 and (row[0].strip() != "" or (len(row)>1 and row[1].strip() != "")):
                        padded_row = row + [""] * (13 - len(row))
                        filtered_data.append(padded_row[:13])
                        
            rows_count = len(filtered_data)
            print(f"   ⚡ Batch {index + 1} Done: {rows_count} rows mili.")
            
            status_msg = "Successfully Synced" if rows_count > 0 else "Sheet is empty"
            return {'data': filtered_data, 'log': [url_clean, "Success", rows_count, status_msg, current_time]}
            
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # 🛡️ Smart Backoff: Har try ke sath wait time badhega (10s, 20s, 30s...)
                wait_time = 10 * attempt 
                print(f"   ⏳ Batch {index + 1} Limit hit. {wait_time}s ruk kar retry... (Attempt {attempt}/{max_retries})")
                time.sleep(wait_time)
            elif e.code in [400, 403, 404]:
                reason = "Tab not found" if e.code == 400 else "Permission Denied/Deleted"
                print(f"   ⚠️ Batch {index + 1} Failed: {reason}")
                return {'data': [], 'log': [url_clean, "Failed", 0, reason, current_time]}
            else:
                time.sleep(5)
        except Exception as e:
            time.sleep(5)
            
    print(f"   ❌ Batch {index + 1} Skipped after {max_retries} retries. (CRITICAL)")
    return {'data': [], 'log': [url_clean, "Failed", 0, "API limit or Error. Skipped.", current_time]}

def sync_sheets():
    print("🚀 System Start: Google se connect kar rahe hain...")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    if not os.environ.get("GOOGLE_CREDENTIALS") or not os.environ.get("MASTER_SHEET_ID"):
        print("❌ Error: GitHub Secrets missing hain!")
        return

    creds_info = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)
    
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    access_token = creds.token
    
    try:
        master_ss = client.open_by_key(MASTER_SHEET_ID)
        links_sheet = master_ss.worksheet(LINKS_TAB)
        target_sheet = master_ss.worksheet(MASTER_TARGET_TAB) 
        
        try:
            log_sheet = master_ss.worksheet(LOG_TAB)
        except gspread.exceptions.WorksheetNotFound:
            log_sheet = master_ss.add_worksheet(title=LOG_TAB, rows="1000", cols="5")
            
    except Exception as e:
        print(f"❌ Error: Master Sheet open nahi hui. Error: {e}")
        return

    print("🔍 Batch Links dhundh rahe hain...")
    try:
        links_data = links_sheet.col_values(1)
    except Exception as e:
        print(f"❌ Error: {LINKS_TAB} se data nahi padh paaye. Error: {e}")
        return
        
    all_data = []
    log_rows = []
    
    ist_timezone = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist_timezone).strftime('%Y-%m-%d %H:%M:%S')

    print("⏳ Data fetching shuru (🚀 STABLE MULTI-THREADING)...")
    safe_range = urllib.parse.quote(f"{SOURCE_TARGET_TAB}!A2:M")

    # 🛡️ FIX 2: 5 ki jagah 3 workers kar diye (Google limit hit nahi hogi)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for index, url in enumerate(links_data):
            futures.append(executor.submit(process_single_sheet, index, url, access_token, safe_range, current_time))
            
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                if result['data']:
                    all_data.extend(result['data'])
                if result['log']:
                    log_rows.append(result['log'])

    if len(all_data) > 0:
        print(f"🧹 Purana data saaf kar rahe hain...")
        target_sheet.batch_clear(["A2:M"]) 
        print(f"📝 Naya {len(all_data)} rows ka data likh rahe hain...")
        
        write_success = False
        for write_attempt in range(3):
            try:
                target_sheet.update(values=all_data, range_name=f"A2:M{len(all_data) + 1}")
                print("🎉 SUCCESS: Saara data Master Sheet update ho gaya!")
                write_success = True
                break
            except Exception as e:
                print(f"⚠️ Google Server Busy (503). 10 sec wait... (Attempt {write_attempt + 1}/3)")
                time.sleep(10)
        
        if not write_success:
            print("❌ Error: Data update nahi hua (Servers down).")
            log_rows.append(["Master Sheet", "Failed", 0, "Google 503 Server Error during write", current_time])
    else:
        print("ℹ️ Info: Bhejne ke liye koi data nahi mila.")

    print("📊 Sync Log update kar rahe hain...")
    log_sheet.clear()
    log_headers = ["Google Sheet Link", "Sync Status", "Rows Imported", "Error Reason", "Last Checked Time"]
    log_sheet.update(values=[log_headers], range_name="A1:E1")
    
    if len(log_rows) > 0:
        padded_logs = [r + [""]*(5-len(r)) for r in log_rows]
        log_sheet.update(values=padded_logs, range_name=f"A2:E{len(log_rows) + 1}")
    print("✅ Dashboard taiyaar hai!")

if __name__ == "__main__":
    sync_sheets()
