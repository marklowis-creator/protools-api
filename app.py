from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def home():
    return "API OK", 200

@app.route("/oauth/xero/callback")
def xero_callback():
    return "Xero callback received", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
