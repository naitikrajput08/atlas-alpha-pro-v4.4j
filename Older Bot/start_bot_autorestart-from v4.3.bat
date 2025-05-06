@echo off
echo Starting Atlas Alpha Pro v4.4j...

REM Change directory to your bot folder
cd /d "C:\Users\naiti\Desktop\atlas_alpha_pro_v4.4j"

:loop
REM Activate virtual environment
call "bot_env\Scripts\activate.bat"

REM Auto-install all requirements (if missing)
pip install -r requirements.txt

REM Start the bot
python atlas_alpha_pro_v4.4j.py

REM If it exits or crashes, restart after 5 seconds
echo Bot exited or crashed. Restarting in 5 seconds...
timeout /t 5
goto loop
