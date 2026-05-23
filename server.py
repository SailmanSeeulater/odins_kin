from flask import Flask, jsonify, render_template_string

from database import get_all_sessions, get_session_events, init_db

app = Flask(__name__)
init_db()


@app.route("/")
def index():
    return render_template_string(open("templates/index.html").read())


@app.route("/api/sessions")
def api_sessions():
    sessions = get_all_sessions()
    return jsonify(sessions)


@app.route("/api/sessions/<int:session_id>/events")
def api_session_events(session_id):
    events = get_session_events(session_id)
    return jsonify(events)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
