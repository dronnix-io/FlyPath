"""The About tab: what FlyPath is for, how it is built, and what comes next."""

import configparser
import os

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

try:
    from .flypath_engine import __version__ as ENGINE_VERSION
except ImportError:  # the engine is generated; a source checkout may lack it
    ENGINE_VERSION = 'not installed'

REPO_URL = 'https://github.com/dronnix-io/FlyPath'
BUG_URL = REPO_URL + '/issues/new?template=bug_report.yml'
FEATURE_URL = REPO_URL + '/issues/new?template=feature_request.yml'
DOCS_URL = REPO_URL + '#readme'
CONTRIB_URL = REPO_URL + '/blob/main/CONTRIBUTING.md'
SITE_URL = 'https://flypath.io/?utm_source=qgis&utm_medium=plugin&utm_campaign=about'
LINK = 'color:#7FB3E8;'

BUILT = (
    'An open-source QGIS plugin (GPL v3), developed in the open on '
    '<a href="%s" style="%s">GitHub</a> with feedback from pilots.' % (REPO_URL, LINK),
    'Routes come from the FlyPath planning engine (Apache 2.0), the same engine '
    'FlyPath.io uses, so a mission is identical in QGIS and in the browser.',
    "It writes DJI's own WPML mission files, so no conversion tool is needed.",
)

AVAILABLE = (
    '<b>2D Mapping</b>: a grid over a survey area, with automatic direction and '
    'optional cross-hatch.',
    '<b>Corridor Mapping</b>: parallel passes along a centre line, for roads, '
    'pipelines and rivers.',
    '<b>Orbit (3D Model)</b>: up to 3 rings around an object, each with its own '
    'radius, altitude and camera tilt.',
    'Semi- or full-automatic capture, and a curved or straight flight path.',
    'Terrain follow with online elevation data or your own DEM, plus the '
    'Takeoff Zone and contours.',
    'Splitting into flights by battery and waypoint limits.',
    'Export as DJI WPML KMZ files, or straight onto the DJI RC.',
    'Saving and opening missions on FlyPath.io.',
    '<b>Windows and Linux</b>, on QGIS 3.34 or later and QGIS 4.',
)

COMING = (
    '<b>Panoramic missions</b>.',
    '<b>Oblique mapping</b>: grids flown with a tilted camera.',
    '<b>Cloud 2D maps and 3D models</b> on FlyPath.io, for the missions you have flown.',
    '<b>Enterprise drone support</b>.',
    '<b>Large survey area splitting</b>.',
    '<b>A mobile app</b>.',
    '<b>AI features</b>.',
)

_STYLE = """
QPushButton#aboutAction {
    background-color: #262A33; color: #E6EAF0; font-weight: bold;
    border: 1px solid #3F4450; border-radius: 5px; padding: 6px 8px;
}
QPushButton#aboutAction:hover { background-color: #2E333E; border-color: #6A9BD8; }
QPushButton#aboutLink {
    background-color: transparent; color: #9CC4F2; font-weight: bold;
    border: 1px solid #2F4A6E; border-radius: 5px; padding: 5px 8px;
}
QPushButton#aboutLink:hover { background-color: #1C2836; color: #FFFFFF; border-color: #3F9BFF; }
QTextBrowser {
    background-color: #1E2128; border: none; color: #D6DCE6;
    padding: 2px 4px;
}
"""

# Two rows: tell us about a problem or an idea, then where to read and take part.
_ACTIONS = (
    (('Report a bug', BUG_URL, 'aboutAction',
      'Open a bug report on GitHub: what you did, what happened, and your QGIS '
      'version and drone.'),
     ('Request a feature', FEATURE_URL, 'aboutAction',
      'Suggest a feature on GitHub.')),
    (('Documentation', DOCS_URL, 'aboutLink',
      'The FlyPath user guide: workflow, parameters, export and supported drones.'),
     ('Contribute', CONTRIB_URL, 'aboutLink',
      'How to contribute code, tests or documentation to FlyPath.'),
     ('GitHub repository', REPO_URL, 'aboutLink',
      'The FlyPath project on GitHub: source code, releases and issues.')),
)


def plugin_version():
    """The version in metadata.txt, as shown by the QGIS plugin manager."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(os.path.join(os.path.dirname(__file__), 'metadata.txt'), encoding='utf-8')
        return parser.get('general', 'version')
    except (configparser.Error, OSError):
        return 'unknown'


def _items(rows, color, mark='●'):
    """Rows with a mark: a bullet, a ticked box (available) or an empty box
    (coming)."""
    return ''.join(
        '<tr><td valign="top" style="color:%s; padding-right:6px;">%s</td>'
        '<td style="padding-bottom:4px;">%s</td></tr>' % (color, mark, row) for row in rows)


def about_html():
    heading = 'color:#9CC4F2; font-size:13px; font-weight:bold; margin-top:14px;'
    return """
<p style="font-size:18px; font-weight:bold; color:#FFFFFF; margin-bottom:2px;">FlyPath</p>
<p style="color:#8FA0B8; margin-top:0;">Version %(version)s · planning engine %(engine)s
 · made by <a href="https://www.dronnix.com" style="%(link)s">Dronnix</a></p>

<p style="%(heading)s">Our mission</p>
<p>Survey cameras got cheap. Survey flying didn't. Almost anyone can afford a drone
with a survey-grade camera, yet almost nobody can fly it as a survey aircraft.
FlyPath closes that gap, starting with DJI: plan survey missions where your GIS data
lives, and fly them in DJI's own apps. It is free and open source.</p>

<p style="%(heading)s">How it is built</p>
<table cellspacing="0">%(built)s</table>

<p style="%(heading)s">Available now</p>
<table cellspacing="0">%(available)s</table>

<p style="%(heading)s">Coming next</p>
<table cellspacing="0">%(coming)s</table>

<p style="%(heading)s">Get involved</p>
<p>Ideas, bug reports and field results shape what comes next. Use the buttons above
to report a bug or ask for a feature, read the documentation, or contribute to the
project on GitHub. You can also plan online at
<a href="%(site)s" style="%(link)s">FlyPath.io</a>.</p>
""" % {
        'version': plugin_version(), 'engine': ENGINE_VERSION, 'link': LINK,
        'heading': heading, 'site': SITE_URL,
        'built': _items(BUILT, '#7FB3E8'),
        'available': _items(AVAILABLE, '#7FD69C', '☑'),
        'coming': _items(COMING, '#F0A500', '☐'),
    }


class AboutTab(QWidget):
    """About FlyPath: its mission, how it is built, what it covers and what's next."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(6)
        # Report a bug or ask for a feature, then the guide, how to take
        # part, and the project itself.
        for row in _ACTIONS:
            actions = QHBoxLayout()
            actions.setSpacing(6)
            for text, url, name, tip in row:
                button = QPushButton(text)
                button.setObjectName(name)
                button.setToolTip(tip)
                button.clicked.connect(
                    lambda _checked=False, link=url: QDesktopServices.openUrl(QUrl(link)))
                actions.addWidget(button, 1)
            layout.addLayout(actions)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setHtml(about_html())
        layout.addWidget(self.text)
