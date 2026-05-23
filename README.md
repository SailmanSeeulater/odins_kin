# Odins Kin - Screen Activiry Tracker

A window desktop app that tracks which applications you use and for how long. Record events and where it was focused on, while also stpring them into a local database, and displays session history in a web UI.

## Stack

| Desktop GUI | Python, tkinter |
| Window detection | pywin32 + psutil |
| Database | SQLite 
| Web Server | Flask |
| Frontend | HTML + CSS + Vanilla JS |


## Setup

**1. Install dependencies**
```bash
pip install pywin32 psutil flask
```

**2. Run the tracker**
```bash
python app.py
```
- Hit **START** to begin recording
- Switch between apps
- Hit **STOP** to save the session to the database and export a JSON file

**3. View session history**
```bash
python server.py
```
Then open your browser at `http://localhost:5000`
