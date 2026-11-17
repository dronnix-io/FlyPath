"""Low-altitude flight: an opt-in mode that lowers the minimum altitude to 5 m.

A mixin for FlyPathDialog, in the same style as OrbitMixin. Below each mission
kind's usual minimum (30 m for mapping, 10 m for orbit and panorama) the pilot
must first accept responsibility once, then sees a warning under Altitude
while the plan is low, and confirms again before each export. flypath.io does
not accept such missions yet, so saving them there is refused with a message.
"""

import sys

from qgis.PyQt.QtWidgets import QCheckBox, QLabel, QMessageBox

try:
    from .flypath_sync import FlypathSyncError
    from .orbit_controller import MAPPING_MIN_ALTITUDE_M, ORBIT_MIN_ALTITUDE_M
    from .panorama_controller import PANORAMA_MIN_ALTITUDE_M
except ImportError:  # standalone tests import modules as top-level packages
    from flypath_sync import FlypathSyncError
    from orbit_controller import MAPPING_MIN_ALTITUDE_M, ORBIT_MIN_ALTITUDE_M
    from panorama_controller import PANORAMA_MIN_ALTITUDE_M


LOW_ALTITUDE_MIN_M = 5.0
ACCEPTED_KEY = 'low_altitude_accepted'
ACCEPT_TEXT = (
    'Flying below the usual minimum brings the drone close to trees, buildings, '
    'poles, power lines, people and crops.\n\n'
    'Obstacle avoidance is not a guarantee, and terrain follow uses elevation data '
    'that is too coarse to keep a safe clearance at a few metres.\n\n'
    'You are responsible for checking the site and for flying safely and legally, '
    'including local minimum-height rules.')
try:
    _MB_YES = QMessageBox.StandardButton.Yes
    _MB_NO = QMessageBox.StandardButton.No
except AttributeError:
    _MB_YES = getattr(QMessageBox, 'Yes')
    _MB_NO = getattr(QMessageBox, 'No')


class LowAltitudeMixin:
    """Low-altitude controls, warnings and export checks for the FlyPath panel."""

    def _settings(self):
        """The panel's FlyPath settings (looked up on the panel's module, so
        tests that isolate the settings also isolate this one)."""
        return sys.modules[type(self).__module__].QSettings('FlyPath', 'FlyPath')

    # ── Building the controls ─────────────────────────────────────────────

    def _build_low_altitude_rows(self, form):
        """The opt-in checkbox and the warning, right under Altitude."""
        self.lowAltitudeCheck = QCheckBox('Allow low altitude (down to %g m)' % LOW_ALTITUDE_MIN_M)
        self._tip(self.lowAltitudeCheck,
            'Lets the Altitude go below the usual minimum (30 m for mapping, 10 m '
            'for orbit and panorama), down to %g m, for example for plant counting '
            'on trial plots. You confirm once that you take responsibility for '
            'flying this low, and FlyPath asks again before each export.'
            % LOW_ALTITUDE_MIN_M)
        form.addRow(self.lowAltitudeCheck)

        self.lowAltitudeWarning = QLabel()
        self.lowAltitudeWarning.setObjectName('frontOverlapWarnLabel')   # the panel's warning colour
        self.lowAltitudeWarning.setWordWrap(True)
        form.addRow(self.lowAltitudeWarning)
        self._set_row_visible(form, self.lowAltitudeWarning, False)

    def _low_altitude_connect_signals(self):
        self.lowAltitudeCheck.toggled.connect(self._on_low_altitude_toggled)
        for signal in (self.altitudeSpin.valueChanged, self.speedSpin.valueChanged,
                       self.captureFullRadio.toggled, self.terrainFollowCheck.toggled,
                       self.missionTypeCombo.currentIndexChanged):
            signal.connect(self._refresh_low_altitude_warning)

    # ── Minimum altitude ──────────────────────────────────────────────────

    def _normal_minimum_altitude(self):
        """The usual minimum for the current mission kind."""
        kind = self._mission_kind()
        return (ORBIT_MIN_ALTITUDE_M if kind == 'orbit' else
                PANORAMA_MIN_ALTITUDE_M if kind == 'panorama' else
                MAPPING_MIN_ALTITUDE_M)

    def _apply_altitude_minimum(self):
        """Set the Altitude minimum for the mission kind and the low-altitude
        choice. A value below the new minimum is raised to it."""
        minimum = (LOW_ALTITUDE_MIN_M if self.lowAltitudeCheck.isChecked()
                   else self._normal_minimum_altitude())
        before = self.altitudeSpin.value()
        blocked = self.altitudeSpin.blockSignals(True)
        self.altitudeSpin.setMinimum(minimum)
        self.altitudeSpin.blockSignals(blocked)
        return self.altitudeSpin.value() != before

    def _on_low_altitude_toggled(self, checked):
        if checked and not self._accept_low_altitude():
            blocked = self.lowAltitudeCheck.blockSignals(True)
            self.lowAltitudeCheck.setChecked(False)
            self.lowAltitudeCheck.blockSignals(blocked)
            return
        if self._apply_altitude_minimum():
            # Turning it off raised a low Altitude to the usual minimum.
            self._update_gsd()
            self._on_param_changed()
        self._refresh_low_altitude_warning()

    def _accept_low_altitude(self):
        """Ask once for the pilot's acceptance; remember a yes."""
        settings = self._settings()
        if str(settings.value(ACCEPTED_KEY, 'false')).lower() == 'true':
            return True
        reply = QMessageBox.question(
            self, 'Allow Low Altitude?',
            ACCEPT_TEXT + '\n\nDo you understand and accept this responsibility?',
            _MB_YES | _MB_NO, _MB_NO)
        if reply != _MB_YES:
            return False
        settings.setValue(ACCEPTED_KEY, True)
        return True

    # ── Warnings ──────────────────────────────────────────────────────────

    def _lowest_planned_altitude(self):
        """The lowest altitude the mission flies, every orbit ring included."""
        if self._mission_kind() == 'orbit':
            return min(altitude for _radius, altitude, _tilt in self._orbit_rings_from_ui())
        return self.altitudeSpin.value()

    def _is_low_altitude(self):
        return self._lowest_planned_altitude() < self._normal_minimum_altitude()

    def _refresh_low_altitude_warning(self, *_args):
        low = self._is_low_altitude()
        self._set_row_visible(self._flight_form, self.lowAltitudeWarning, low)
        if not low:
            return
        lines = ['⚠ Low altitude (%g m): check the site for obstacles. You are '
                 'responsible for flying safely and legally.' % self._lowest_planned_altitude()]
        if self._mission_kind() in ('2d', 'oblique', 'corridor') and self._mission_type() == 'semi':
            lines.append('Semi capture may not keep the front overlap this low; Full '
                         'capture takes a photo at every waypoint.')
        if self.terrainFollowCheck.isChecked():
            lines.append('Terrain follow data is too coarse to keep a safe clearance '
                         'at this height.')
        self.lowAltitudeWarning.setText('\n'.join(lines))

    def _confirm_low_altitude_export(self):
        """Before an export, ask again when the mission flies low."""
        if not self._is_low_altitude():
            return True
        reply = QMessageBox.question(
            self, 'Low-Altitude Mission',
            'This mission flies at %g m above the takeoff point, below the usual '
            'minimum of %g m.\n\nCheck the site for obstacles before flying. Export '
            'anyway?' % (self._lowest_planned_altitude(), self._normal_minimum_altitude()),
            _MB_YES | _MB_NO, _MB_NO)
        return reply == _MB_YES

    def _check_low_altitude_sync(self):
        """flypath.io does not accept low-altitude missions yet."""
        if self._is_low_altitude():
            raise FlypathSyncError(
                'flypath.io does not support low-altitude missions yet (below %g m). '
                'Export the mission as a KMZ or send it to the DJI RC instead.'
                % self._normal_minimum_altitude())
