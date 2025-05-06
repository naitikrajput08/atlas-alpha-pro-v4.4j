from flask import Flask, request
import subprocess

app = Flask(__name__)

@app.route('/deploy', methods=['POST'])
def deploy():
    # 1) pull the latest commit
    subprocess.run(['git','pull'], cwd=r'C:\Users\naiti\Desktop\atlas_alpha_pro_v4.4j')
    # 2) kill python so your auto-restart .bat will relaunch the bot
    subprocess.run(['taskkill','/F','/IM','python.exe'])
    return '', 204

if __name__ == "__main__":
    # listen on port 9000 on all interfaces
    app.run(host='0.0.0.0', port=9000)
