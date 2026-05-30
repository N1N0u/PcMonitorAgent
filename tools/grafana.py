"""
tools/grafana.py
Fetches dashboards and panel PromQL queries from Grafana.
"""

import requests
from requests.auth import HTTPBasicAuth

GRAFANA_URL  = "http://localhost:3000"
GRAFANA_USER = "admin"
GRAFANA_PASS = "admin123"
TIMEOUT      = 10


def _auth():
    return HTTPBasicAuth(GRAFANA_USER, GRAFANA_PASS)


def check_grafana() -> bool:
    try:
        r = requests.get(f"{GRAFANA_URL}/api/health", auth=_auth(), timeout=TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False



def list_dashboards() -> list:
    """Returns [{uid, title}, ...] for the dashboard selector."""
    try:
        r = requests.get(
            f"{GRAFANA_URL}/api/search?type=dash-db",
            auth=_auth(), timeout=TIMEOUT
        )
        r.raise_for_status()
        return [{"uid": d["uid"], "title": d["title"]} for d in r.json()]
    except Exception:
        return []


def fetch_panels_for_dashboard(uid: str) -> list:
    """Returns panels (with targets) for a single dashboard UID."""
    try:
        r = requests.get(
            f"{GRAFANA_URL}/api/dashboards/uid/{uid}",
            auth=_auth(), timeout=TIMEOUT
        )
        r.raise_for_status()
        dashboard = r.json()["dashboard"]
    except Exception:
        return []

    panels = []
    for panel in dashboard.get("panels", []):
        sub_panels = panel.get("panels", [])
        items = sub_panels if sub_panels else [panel]
        for p in items:
            targets = []
            for t in p.get("targets", []):
                expr = t.get("expr", "").strip()
                if expr:
                    targets.append({
                        "expr":   expr,
                        "legend": t.get("legendFormat", ""),
                    })
            if targets:
                panels.append({
                    "panel_id":    p.get("id"),
                    "panel_title": p.get("title", "Panel"),
                    "panel_type":  p.get("type", "graph"),
                    "targets":     targets,
                })
    return panels
