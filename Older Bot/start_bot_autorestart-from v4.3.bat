@echo off
echo Starting Atlas Alpha Pro v4.9 (atlas_alpha_pro_v4.4j.py) with Auto-Restart...

REM === Step 1: Go to your bot folder ===
cd /d "C:\Users\naiti\Desktop\atlas_alpha_pro_v4.4j"

REM === Step 2: Activate virtual environment ===
call bot_env\Scripts\activate.bat

REM === Step 3: Install all Python dependencies ===
REM (this covers ib_insync, data libs, timezone, Google Sheets, TA indicator)
pip install --quiet -r requirements.txt

REM In case anything’s missing, force-install core libs:
pip install --quiet ib_insync pandas numpy pytz ta gspread oauth2client

REM === Step 4: Start Main Bot with auto-restart loop ===
:mainLoop
echo Starting atlas_alpha_pro_v4.4j.py...
python atlas_alpha_pro_v4.4j.py
echo Bot exited or crashed. Restarting in 5 seconds...
timeout /t 5
goto mainLoop
