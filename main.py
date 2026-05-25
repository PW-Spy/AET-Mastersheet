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

# ==========================================
MASTER_SHEET_ID = os.environ.get("MASTER_SHEET_ID")
MASTER_TARGET_TAB = "AET Planner"  
SOURCE_TARGET_TAB = "AET Planner"    
LINKS_TAB = "Batch Links"
LOG_TAB = "Sync Log"
# ==========================================

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
            print(f"📋 Naya Tab '{LOG_TAB}' bana diya gaya hai.")
            
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

    print("⏳ Data fetching shuru (VIP Direct API Mode)...")
    safe_range = urllib.parse.quote(f"{SOURCE_TARGET_TAB}!A2:M")

    for index, url in enumerate(links_data):
        url_clean = url.strip()
        if not url_clean or url_clean.startswith("Google Sheet Link"): 
            continue
            
        match = re.search(r'/d/([a-zA-Z0-9-_]+)', url_clean)
        if not match:
            log_rows.append([url_clean, "Failed", 0, "Invalid Google Sheet URL Format", current_time])
            continue
            
        sid = match.group(1)
        api_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{safe_range}"
        
        max_retries = 5 # ⚠️ NAYA FIX: 3 se 5 tries kar diye
        success = False
        
        for attempt in range(max_retries):
            try:
                req_obj = urllib.request.Request(api_url)
                req_obj.add_header('Authorization', f'Bearer {access_token}')
                
                response = urllib.request.urlopen(req_obj)
                data_json = json.loads(response.read().decode('utf-8'))
                values = data_json.get('values', [])
                
                if len(values) > 0:
                    filtered_data = []
                    for row in values:
                        if len(row) > 0 and (row[0].strip() != "" or (len(row)>1 and row[1].strip() != "")):
                            padded_row = row + [""] * (13 - len(row))
                            filtered_data.append(padded_row[:13])
                            
                    all_data.extend(filtered_data)
                    rows_count = len(filtered_data)
                    print(f"   -> Batch {index + 1}: {rows_count} rows mili.")
                    log_rows.append([url_clean, "Success", rows_count, "Successfully Synced", current_time])
                else:
                    print(f"   -> Batch {index + 1}: Data khali hai.")
                    log_rows.append([url_clean, "Success", 0, "Sheet is empty", current_time])
                
                success = True
                time.sleep(2.0) # ⚠️ NAYA FIX: Speed limit bachane ke liye safe wait
                break 
                
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # ⚠️ NAYA FIX: Smart Retry Logic (15s, 30s, 45s...)
                    wait_time = 15 * (attempt + 1)
                    print(f"   ⏳ Speed limit hit. Waiting {wait_time} sec... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                elif e.code == 400:
                    print(f"   ⚠️ Warning: Batch {index + 1} mein Tab nahi mila.")
                    log_rows.append([url_clean, "Failed", 0, f"Tab '{SOURCE_TARGET_TAB}' not found in source", current_time])
                    success = True
                    time.sleep(2.0)
                    break
                elif e.code == 403 or e.code == 404:
                    reason = "Permission Denied (Robot not editor) or Sheet Deleted"
                    print(f"   ⚠️ Warning: Batch {index + 1} failed. Reason: {reason}")
                    log_rows.append([url_clean, "Failed", 0, reason, current_time])
                    success = True
                    time.sleep(2.0)
                    break
                else:
                    print(f"   ⚠️ Unknown API Error: {e.code}")
                    time.sleep(10)
            except Exception as e:
                print(f"   ⚠️ Network Error. Waiting 10 sec... Error: {str(e)}")
                time.sleep(10)
                    
        if not success:
            log_rows.append([url_clean, "Failed", 0, "API limit or persistent error. Skipped.", current_time])

    # 1. Master Sheet Data Update
    if len(all_data) > 0:
        print(f"🧹 Purana data saaf kar rahe hain...")
        target_sheet.batch_clear(["A2:M"]) 
        print(f"📝 Naya {len(all_data)} rows ka data likh rahe hain...")
        # ⚠️ NAYA FIX: Warning hatane ke liye (values=..., range_name=...)
        target_sheet.update(values=all_data, range_name=f"A2:M{len(all_data) + 1}")
        print("🎉 SUCCESS: Saara data Master Sheet update ho gaya!")
    else:
        print("ℹ️ Info: Bhejne ke liye koi data nahi mila.")

    # 2. LOG TAB Data Update
    print("📊 Sync Log update kar rahe hain...")
    log_sheet.clear()
    log_headers = ["Google Sheet Link", "Sync Status", "Rows Imported", "Error / Success Reason", "Last Checked Time"]
    
    # ⚠️ NAYA FIX: Warning hatane ke liye (values=..., range_name=...)
    log_sheet.update(values=[log_headers], range_name="A1:E1")
    
    if len(log_rows) > 0:
        padded_logs = [r + [""]*(5-len(r)) for r in log_rows]
        log_sheet.update(values=padded_logs, range_name=f"A2:E{len(log_rows) + 1}")
    print("✅ Sync Log dashboard taiyaar hai!")

if __name__ == "__main__":
    sync_sheets()
