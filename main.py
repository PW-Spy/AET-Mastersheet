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
from datetime import datetime  # ⚠️ NAYA IMPORT: Time calculate karne ke liye
import pytz                    # ⚠️ NAYA IMPORT: India (IST) timezone ke liye

# ==========================================
MASTER_SHEET_ID = os.environ.get("MASTER_SHEET_ID")
TARGET_TAB = "AET Planner 2"
LINKS_TAB = "Batch Links 2"
LOG_TAB = "Sync Log"
# ==========================================

def sync_sheets():
    print("🚀 System Start: Google se connect kar rahe hain...")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    creds_info = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)
    
    # VIP Direct API ke liye Token generate karna
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    access_token = creds.token
    
    try:
        master_ss = client.open_by_key(MASTER_SHEET_ID)
        links_sheet = master_ss.worksheet(LINKS_TAB)
        target_sheet = master_ss.worksheet(TARGET_TAB)
        
        try:
            log_sheet = master_ss.worksheet(LOG_TAB)
        except gspread.exceptions.WorksheetNotFound:
            log_sheet = master_ss.add_worksheet(title=LOG_TAB, rows="1000", cols="5")
            print(f"📋 Naya Tab '{LOG_TAB}' bana diya gaya hai.")
            
    except Exception as e:
        print(f"❌ Error: Master Sheet open nahi hui. Error: {e}")
        return

    print("🔍 Batch Links dhundh rahe hain...")
    links_data = links_sheet.col_values(1)
    
    all_data = []
    log_rows = []
    
    # 🛠️ TIMEZONE FIX: Server time ko hata kar exact India (IST) time lagaya gaya hai
    ist_timezone = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist_timezone).strftime('%Y-%m-%d %H:%M:%S')

    print("⏳ Data fetching shuru (VIP Direct API Mode - Anti Quota Limit)...")
    
    # Tab ke naam ko URL ke liye safe banana
    safe_range = urllib.parse.quote(f"{TARGET_TAB}!A2:M")

    for index, url in enumerate(links_data):
        url_clean = url.strip()
        if not url_clean:
            continue
            
        match = re.search(r'/d/([a-zA-Z0-9-_]+)', url_clean)
        if not match:
            log_rows.append([url_clean, "Failed", 0, "Invalid Google Sheet URL Format", current_time])
            continue
            
        sid = match.group(1)
        api_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{safe_range}"
        
        max_retries = 3
        success = False
        
        for attempt in range(max_retries):
            try:
                # Direct Google API Call (1 request instead of 3)
                req_obj = urllib.request.Request(api_url)
                req_obj.add_header('Authorization', f'Bearer {access_token}')
                
                response = urllib.request.urlopen(req_obj)
                data_json = json.loads(response.read().decode('utf-8'))
                values = data_json.get('values', [])
                
                if len(values) > 0:
                    filtered_data = []
                    for row in values:
                        # Empty rows hata rahe hain
                        if len(row) > 0 and (row[0].strip() != "" or (len(row)>1 and row[1].strip() != "")):
                            padded_row = row + [""] * (13 - len(row)) # A se M tak barabar size
                            filtered_data.append(padded_row[:13])
                            
                    all_data.extend(filtered_data)
                    rows_count = len(filtered_data)
                    print(f"   -> Batch {index + 1}: {rows_count} rows mili.")
                    log_rows.append([url_clean, "Success", rows_count, "Successfully Synced", current_time])
                else:
                    print(f"   -> Batch {index + 1}: Data khali hai.")
                    log_rows.append([url_clean, "Success", 0, "Sheet is empty", current_time])
                
                success = True
                
                # Yeh 1.2 second ka strict wait humein hamesha 60 req/min ki limit se bachaega
                time.sleep(1.2) 
                break 
                
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print(f"   ⏳ Speed limit hit. Waiting 15 sec... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(15)
                elif e.code == 400:
                    print(f"   ⚠️ Warning: Batch {index + 1} mein Tab nahi mila.")
                    log_rows.append([url_clean, "Failed", 0, f"Tab '{TARGET_TAB}' not found", current_time])
                    success = True
                    time.sleep(1.2)
                    break
                elif e.code == 403 or e.code == 404:
                    reason = "Permission Denied (Robot not editor) or Sheet Deleted"
                    print(f"   ⚠️ Warning: Batch {index + 1} failed. Reason: {reason}")
                    log_rows.append([url_clean, "Failed", 0, reason, current_time])
                    success = True
                    time.sleep(1.2)
                    break
                else:
                    print(f"   ⚠️ Unknown API Error: {e.code}")
                    time.sleep(5)
            except Exception as e:
                print(f"   ⚠️ Network Error. Waiting 5 sec... Error: {str(e)}")
                time.sleep(5)
                    
        if not success:
            log_rows.append([url_clean, "Failed", 0, "API limit or persistent error. Skipped.", current_time])

    # 1. Master Sheet Data Update
    if len(all_data) > 0:
        print(f"🧹 Purana data saaf kar rahe hain...")
        target_sheet.batch_clear(["A2:M"]) 
        print(f"📝 Naya {len(all_data)} rows ka data likh rahe hain...")
        target_sheet.update(f"A2:M{len(all_data) + 1}", all_data)
        print("🎉 SUCCESS: Saara data Master Sheet update ho gaya!")
    else:
        print("ℹ️ Info: Bhejne ke liye koi data nahi mila.")

    # 2. LOG TAB Data Update
    print("📊 Sync Log update kar rahe hain...")
    log_sheet.clear()
    log_headers = ["Google Sheet Link", "Sync Status", "Rows Imported", "Error / Success Reason", "Last Checked Time"]
    log_sheet.update("A1:E1", [log_headers])
    
    if len(log_rows) > 0:
        padded_logs = [r + [""]*(5-len(r)) for r in log_rows]
        log_sheet.update(f"A2:E{len(log_rows) + 1}", padded_logs)
    print("✅ Sync Log dashboard taiyaar hai!")

if __name__ == "__main__":
    sync_sheets()
