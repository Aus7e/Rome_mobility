"""Map-first front-end contract: all original simulation IDs stay wired.

This is a static guard against missing UI elements, duplicate IDs and
accidental removal of research/observed-data workflows in visual redesigns.
"""
from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


class Reader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.files = []
        self.buttons = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
            if tag == "button":
                self.buttons.append(values["id"])
        if tag == "script" and "src" in values:
            self.files.append(values["src"])
        if tag == "link" and "href" in values:
            self.files.append(values["href"])


REQUIRED_IDS = {
    "streetMap", "scene", "runBtn", "mobileRunBtn", "view2DBtn", "view3DBtn",
    "mapSearch", "searchResults", "searchBtn", "mapZoomIn", "mapZoomOut",
    "fitRouteBtn", "signalsToggleBtn", "sourceChip", "routeSource",
    "routeDistance", "segment", "day", "hour", "hourValue", "mode",
    "engine", "rain", "rainValue", "weatherBtn", "speed", "speedValue",
    "demand", "demandValue", "sideTraffic", "sideTrafficValue",
    "estimatedDemand", "trafficSource", "hourlyCoverage",
    "observationsFile", "importObservationsBtn", "observationStatus",
    "cycle", "green", "signalEvidence", "signalSettings", "duration",
    "seed", "researchRuns", "researchWorkers", "researchBtn", "researchStatus",
    "typicalTrafficBtn", "typicalTrafficStatus", "setupSumoBtn", "osmBtn",
    "sumoStatus", "message", "compareBtn", "comparison", "exportBtn",
    "resultsSheet", "analysisDetails", "playPause", "timeline", "clock",
    "playSpeed", "travelKpi", "delayKpi", "queueKpi", "stopsKpi",
    "queueChart", "completionLabel", "networkNote", "renderError", "mapTileStatus",
}


def test_map_first_page_has_unique_comprehensive_controls():
    parser = Reader()
    parser.feed((STATIC / "index.html").read_text())
    assert len(parser.ids) == len(set(parser.ids))
    assert REQUIRED_IDS.issubset(parser.ids), REQUIRED_IDS.difference(parser.ids)
    assert "/static/main.js" in parser.files
    assert "/static/vendor/leaflet/leaflet.js" in parser.files
    assert "/static/vendor/leaflet/leaflet.css" in parser.files


def test_research_and_evidence_are_in_progressive_disclosure():
    html = (STATIC / "index.html").read_text()
    assert '<details id="researchSettings">' in html
    assert '<details id="trafficDataSettings">' in html
    assert '<details id="signalsSettings">' in html
    assert '<details id="analysisDetails">' in html
    assert 'NON LIVE' in html
    assert "OpenStreetMap" in html


def test_map_does_not_require_external_google_services_or_frontend_cdns():
    js = (STATIC / "map.js").read_text()
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "style.css").read_text()
    assert "tile.openstreetmap.org/{z}/{x}/{y}.png" in js
    assert "referrerPolicy" in js
    assert "©" in js
    assert "maps.googleapis.com" not in js + html
    assert "fonts.googleapis" not in css
    assert 'import' in (STATIC / "main.js").read_text()
