"""FlyPath website save/load orchestration for the QGIS dialog."""
import datetime

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QInputDialog,
    QLineEdit,
    QMessageBox,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
)

from . import flypath_sync
from . import planning_adapter
from . import preview_layers
from . import survey_geometry
from .flypath_sync import FlypathSyncError
from .grid_planner import measure_route, measure_survey_area
from .hardware import registry

try:
    _WaitCursor = Qt.CursorShape.WaitCursor
    _MB_YES = QMessageBox.StandardButton.Yes
    _MB_NO = QMessageBox.StandardButton.No
except AttributeError:
    _WaitCursor = getattr(Qt, 'WaitCursor')
    _MB_YES = getattr(QMessageBox, 'Yes')
    _MB_NO = getattr(QMessageBox, 'No')


class WebsiteSyncLifecycleMixin:
    """Owns website credentials, linked saves, conflicts, and mission import."""

    def _forget_website_mission(self):
        self._website_link = None
        self._update_web_buttons()

    def _current_website_link(self):
        link = getattr(self, '_website_link', None)
        if not link:
            return None
        try:
            scope = flypath_sync.account_key(flypath_sync.load_base_url(), flypath_sync.load_token())
        except FlypathSyncError:
            self._website_link = None
            return None
        if link and link['account'] != scope:
            self._website_link = None
            return None
        return link

    def _remember_website_mission(self, mission):
        try:
            scope = flypath_sync.account_key(flypath_sync.load_base_url(), flypath_sync.load_token())
        except FlypathSyncError:
            self._website_link = None
            self._update_web_buttons()
            return
        self._website_link = {
            'id': mission.get('id'), 'revision': mission.get('revision'),
            'name': mission.get('name') or 'Mission',
            'account': scope,
        }
        self._update_web_buttons()

    def _update_web_buttons(self):
        library = getattr(self, '_mission_library', None)
        if library is not None:
            library.update_buttons()

    def _web_token(self):
        """The token stored on this machine, asking for it once if there is
        none. Returns None when the pilot cancels the prompt."""
        from .flypath_credentials import unlock_storage
        parent = getattr(self, '_mission_library', None) or self
        try:
            origin = flypath_sync.load_base_url()
            unlock_storage()
            token = flypath_sync.load_token()
            if flypath_sync.load_base_url() != origin:
                raise FlypathSyncError('The FlyPath website changed while connecting. Retry Connect account for the new website.')
        except FlypathSyncError as exc:
            QMessageBox.warning(parent, 'FlyPath credentials', str(exc))
            self._update_web_buttons()
            return None
        if token:
            return token
        try:
            password = QLineEdit.EchoMode.Password
        except AttributeError:
            password = getattr(QLineEdit, 'Password')
        token, ok = QInputDialog.getText(
            getattr(self, '_mission_library', None) or self, 'FlyPath Token',
            'Connect to %s.\n\nChoose Plugin token in this website\'s account menu, generate a '
            'token,\nand paste it here. QGIS will store it in its encrypted '
            'authentication vault.\nIf a token expires or is revoked, reconnect '
            'with a new token;\nyour local plan is kept.\n\nToken:' % origin,
            password)
        token = (token or '').strip()
        if not (ok and token):
            return None
        try:
            if flypath_sync.load_base_url() != origin:
                raise FlypathSyncError('The FlyPath website changed while entering the token. Nothing was saved. Retry Connect account for the new website.')
            flypath_sync.save_token(token)
        except FlypathSyncError as exc:
            QMessageBox.warning(parent, 'FlyPath credentials', str(exc))
            self._update_web_buttons()
            return None
        return token

    def _run_web(self, title, work, *, raise_conflict=False):
        """Run one website call with the token, a wait cursor and the sync
        buttons disabled (a slow connection should look busy, not crashed).
        Returns work()'s value, or None when it failed or was cancelled — the
        error is shown as a message box here so flypath_sync stays Qt-free."""
        try:
            origin = flypath_sync.load_base_url()
        except FlypathSyncError as exc:
            QMessageBox.warning(self, title, str(exc))
            return None
        token = self._web_token()
        if token is None:
            return None
        library = getattr(self, '_mission_library', None)
        if library is not None:
            library.setEnabled(False)
        QApplication.setOverrideCursor(_WaitCursor)
        # ponytail: retain synchronous HTTP; use a QGIS task if request latency disrupts planning.
        QApplication.processEvents()     # paint the busy state before blocking
        try:
            if (flypath_sync.load_base_url() != origin or
                    flypath_sync.load_token() != token):
                raise FlypathSyncError('The FlyPath connection changed before sending. No request was made. Retry with the intended website and account.')
            return work(token)
        except FlypathSyncError as exc:
            if exc.status == 401:
                # The token is gone or was regenerated: forget it so the next
                # attempt asks for the new one instead of failing again.
                self._website_link = None
                try:
                    flypath_sync.save_token('')
                except FlypathSyncError as storage_error:
                    QMessageBox.warning(self, 'FlyPath credentials', str(storage_error))
            if exc.status == 409 and raise_conflict:
                raise
            QMessageBox.warning(self, title, str(exc))
            return None
        finally:
            QApplication.restoreOverrideCursor()
            if library is not None:
                library.setEnabled(True)
            self._update_web_buttons()

    def _on_send_to_website(self, save_as_new=False):
        if self._planning.save_requires_regeneration():
            QMessageBox.warning(
                self, 'Regeneration Required',
                'The saved route no longer matches the editable settings. Choose '
                'Preview to regenerate it before saving to FlyPath.')
            return False
        """Save the linked mission, or explicitly create and link a new one."""
        if not (self._preview_layer_ids and self._missions):
            QMessageBox.information(self, 'Preview First', 'Preview the mission on the map before saving it to FlyPath.')
            return
        link = self._current_website_link()
        updating = bool(link and not save_as_new)
        name = link['name'] if link else 'QGIS ' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        parent = getattr(self, '_mission_library', None) or self
        if not updating:
            name, ok = QInputDialog.getText(
                parent, 'Save as new on FlyPath', 'Mission name:',
                text=name + ' copy' if link else name)
            if not ok or not name.strip():
                return
            name = name.strip()
        try:
            payload = self._website_payload(name)
        except FlypathSyncError as exc:
            QMessageBox.warning(parent, 'Cannot Save Mission', str(exc))
            return
        try:
            base_url = flypath_sync.load_base_url()
        except FlypathSyncError as exc:
            QMessageBox.warning(parent, 'FlyPath credentials', str(exc))
            return
        try:
            result = self._run_web(
                'Save to FlyPath',
                lambda token: flypath_sync.update_mission(base_url, token, link['id'], link['revision'], payload)
                if updating else flypath_sync.push_mission(base_url, token, payload),
                raise_conflict=updating)
        except FlypathSyncError as exc:
            if exc.status != 409:
                raise
            return self._resolve_website_conflict(link)
        if not result:
            return
        self._remember_website_mission(result)
        url = flypath_sync.mission_url(result.get('id'), base_url)
        reply = QMessageBox.question(
            parent, 'Saved to FlyPath',
            ('Changes saved to "%s".' if updating else 'New mission "%s" saved.')
            % (result.get('name') or 'Mission') + '\n\nOpen it in your browser?',
            _MB_YES | _MB_NO, _MB_YES)
        if reply == _MB_YES:
            QDesktopServices.openUrl(QUrl(url))
        return True

    def _resolve_website_conflict(self, link):
        from .flypath_library import conflict_choice
        parent = getattr(self, '_mission_library', None) or self
        choice = conflict_choice(parent)
        if choice == 'copy':
            return self._on_send_to_website(save_as_new=True)
        if choice == 'reload':
            answer = QMessageBox.question(
                parent, 'Load Latest Mission',
                'Discard your local edits and load the latest saved mission?',
                _MB_YES | _MB_NO, _MB_NO)
            if answer == _MB_YES:
                return self._on_load_from_website(link['id'])
        return False

    def _website_payload(self, name):
        """The mission as the website's API expects it. Points travel as
        [lat, lon] pairs (the website's own order), the drone as the website's
        own code. Linked saves address the mission by its API URL."""
        corridor = self._mission_kind() == 'corridor'
        drone = registry.get(self.droneModelCombo.currentText())
        if not drone.website_code:
            raise FlypathSyncError(
                '%s is not one of the drones flypath.io offers, so this mission '
                'cannot be sent there. Choose another drone, or export a KMZ '
                'instead.' % drone.name)
        try:
            area = (survey_geometry.line_vertices(
                        self._survey_line, self._survey_line_crs) if corridor
                    else survey_geometry.polygon_vertices(
                        self._survey_polygon, self._survey_polygon_crs))
        except survey_geometry.MultipartLineError as exc:
            raise FlypathSyncError(
                'FlyPath stores one corridor centre line, but this one has %d '
                'separate lines. Send them as separate missions.' % exc.args[0]) from None
        if not area:
            raise FlypathSyncError('This mission has no survey area to send.')
        if not corridor and self._planning.request:
            survey_area = self._planning.request.get('survey_area', {})
            if survey_area.get('parts') or survey_area.get('holes'):
                raise FlypathSyncError(
                    'FlyPath website sync cannot yet store polygon holes or '
                    'multiple polygon parts without losing geometry. Export the '
                    'mission locally instead.')
        # ponytail: full-automatic routes are pushed as flown, one point per
        # photo, while the website reads waypoints as flight-line endpoints in
        # pairs. The area and settings it saves regenerate the route correctly
        # there; if the drawn route on the website matters, push turn points.
        waypoints = list(self._waypoints or [])
        for label, points in (('survey area', area), ('waypoints', waypoints)):
            if len(points) > flypath_sync.MAX_MISSION_POINTS:
                raise FlypathSyncError(
                    'This mission has %d %s; FlyPath accepts at most %d. Use '
                    'semi-automatic capture, or a smaller area, to send it.'
                    % (len(points), label, flypath_sync.MAX_MISSION_POINTS))
        payload = {
            'name': name,
            'drone_model': drone.website_code,
            'polygon':   [[lat, lon] for lon, lat in area],
            'waypoints': [[lat, lon] for lon, lat in waypoints],
            'settings':  self._website_settings(),
            'estimates': self._website_estimates(),
            'source_product': 'plugin',
        }
        if self._planning.request and self._planning.result:
            payload['planning_request'] = self._planning.request
            payload['planning_result'] = self._planning.result
        return payload

    def _website_settings(self):
        """The mission parameters the website knows, by its own key names.

        Plugin-only settings (launch offset, GSD, photo interval, gimbal angle,
        DEM choice, takeoff-zone tolerance, corridor mission breaks) are left
        out rather than invented as new server-side keys. Values outside the
        website's own ranges are not clamped here: the server rejects them with
        a message the pilot is shown as-is."""
        corridor = self._mission_kind() == 'corridor'
        actions = []
        for label, combo, table in (
                ('Finish Action', self.finishActionCombo,
                 flypath_sync.WEBSITE_FINISH_ACTIONS),
                ('RC Lost Action', self.rcLostActionCombo,
                 flypath_sync.WEBSITE_RC_LOST_ACTIONS)):
            code = table.get(combo.currentText())
            if code is None:
                raise FlypathSyncError(
                    'FlyPath has no website equivalent for %s "%s". Choose '
                    'another one to send this mission.'
                    % (label, combo.currentText()))
            actions.append(code)
        settings = {
            'mapping_style':  self._mission_kind(),
            'capture_mode':   self._mission_type(),
            'flight_path':    'curved' if self._path_curved() else 'straight',
            'finish_action':  actions[0],
            'rc_lost_action': actions[1],
            'altitude':       round(self.altitudeSpin.value(), 1),
            'speed':          round(self.speedSpin.value(), 1),
            'side_overlap':   self.sideOverlapSpin.value(),
            'terrain_follow': bool(self.terrainFollowCheck.isChecked()),
            'split_count':    self.splitSpin.value(),
            'split_enabled':  self.splitCheck.isChecked(),
            'auto_direction': self.autoDirectionBtn.isChecked(),
            'reverse_route':  False,
            'split_max_wp':   self.maxWaypointsSpin.value(),
        }
        if corridor:
            # The plugin's Buffer is the half-width each side; the website's
            # corridor_width is the full mapped width.
            settings['corridor_width'] = round(self.bufferSpin.value() * 2.0, 1)
        else:
            # Flight lines are bidirectional, so a heading and its opposite are
            # the same grid; the website's range stops below 180.
            settings['direction'] = round(self.directionSpin.value() % 180.0, 1)
            settings['margin'] = round(self.marginSpin.value(), 1)
            settings['cross_hatch'] = bool(self.crossHatchCheck.isChecked())
        if self._mission_type() == 'full':
            # Semi-automatic front overlap is derived from speed and interval,
            # not a setting the pilot chose, so it is not sent as one.
            settings['front_overlap'] = self.frontOverlapSpin.value()
        if self.terrainFollowCheck.isChecked():
            settings['terrain_tolerance'] = round(self.terrainToleranceSpin.value(), 1)
        return settings

    def _website_estimates(self):
        """The figures the website's mission card shows, taken from the same
        stats card the pilot just reviewed, so both tools report one number."""
        return {
            'distance_m': int(round(measure_route(self._waypoints or []))),
            'time':       self.flightTimeLabel.text(),
            'distance':   self.distanceLabel.text(),
            'photos':     self.photosLabel.text(),
            'waypoints':  self.waypointsLabel.text(),
            'batteries':  self.batteriesLabel.text(),
            'area':       self.coverageLabel.text(),
            'area_m2':    (measure_survey_area(self._survey_polygon, self._survey_polygon_crs)
                           if self._mission_kind() == '2d' else None),
            'statistics': (self._planning.result['statistics']
                           if self._planning.result else self._live_statistics),
            'flight_count': len(self._missions or []),
        }

    def _restore_imported_route(self, mission, provenance):
        """Show the saved route without regenerating it under installed profiles."""
        request = mission.get('planning_request')
        result = mission.get('planning_result')
        legacy = mission.get('waypoints') or []

        def restore_estimates():
            estimates = mission.get('estimates') or {}
            if not isinstance(estimates, dict):
                return
            for key, label in (
                    ('time', self.flightTimeLabel),
                    ('distance', self.distanceLabel),
                    ('photos', self.photosLabel),
                    ('batteries', self.batteriesLabel),
                    ('area', self.coverageLabel)):
                value = estimates.get(key)
                if isinstance(value, (str, int, float)):
                    label.setText(str(value))
            statistics = estimates.get('statistics')
            strip_count = statistics.get('strip_count') if isinstance(statistics, dict) else None
            if type(strip_count) is int and strip_count >= 0:
                self.linesLabel.setText(str(strip_count))
            self._show_hud()

        self._on_clear_preview(reset_area=False)
        self._clear_stats()
        self.previewBtn.setText('Preview on Map')
        if result:
            supported, flights = provenance
            self._apply_planning_result(request, result, flights=flights)
            self._planning.preserve_imported_route(supported=supported)
        elif legacy and self._mission_type() != 'full':
            route = [(float(lon), float(lat)) for lat, lon in legacy]
            self._waypoints = route
            self._missions = self._split_missions(route)
            self._live_waypoints = route
            self._live_missions = self._missions
            self._shot_spacing_m = max(
                self.speedSpin.value() * self.photoIntervalSpin.value(), 0.5)
            restore_estimates()
            self.waypointsLabel.setText(str(sum(map(len, self._missions))))
            self.linesLabel.setText(str(len(route) // 2))
            self._set_info('Saved route and estimates. Editing settings requires '
                           'regeneration with the installed planning engine.')
        else:
            self._planning.begin_preview(has_saved_route=False)
            if not self._split_choice_required:
                self._on_preview()
                if not self._planning.result:
                    restore_estimates()
                    self.waypointsLabel.setText(str(len(self._waypoints)))
            else:
                self._set_info('Choose Splitting, then Preview on Map to generate '
                               'this older mission’s route.')
            return
        self._planning.preserve_imported_route()
        self.previewBtn.setText('Preview on Map')
        if self.terrainFollowCheck.isChecked():
            self._terrain_failed = True
        self._preview_layer_ids = preview_layers.create(self._missions)
        self._update_web_buttons()
        self.iface.mapCanvas().refresh()

    def _on_load_from_website(self, mission_id):
        """Load a library selection as an independent local copy."""
        try:
            base_url = flypath_sync.load_base_url()
        except FlypathSyncError as exc:
            QMessageBox.warning(self, 'FlyPath credentials', str(exc))
            return
        mission = self._run_web(
            'Load from FlyPath',
            lambda token: flypath_sync.get_mission(base_url, token,
                                                   mission_id))
        if mission is None:
            return
        try:
            adjusted = self._apply_website_mission(mission)
        except FlypathSyncError as exc:
            QMessageBox.warning(self, 'Cannot Load Mission', str(exc))
            return
        self._remember_website_mission(mission)
        note = ''
        if adjusted:
            note = ('\n\nThese settings are outside this plugin\'s own range '
                    'and were adjusted to the nearest value it can fly:\n  '
                    + '\n  '.join(adjusted))
        QMessageBox.information(
            self, 'Loaded from FlyPath',
            '"%s" is now a local mission in QGIS.\n\nIt is an independent '
            'working copy linked to FlyPath. Save changes updates this mission; '
            'Save as new creates a separate mission.%s'
            % (mission.get('name') or 'Mission', note))
        return True

    @staticmethod
    def _web_date(updated_at):
        """The website's UTC update time shown in the computer's local time, e.g.
        '2026-01-02T03:04:05.678+00:00' (UTC) -> '2026-01-02 04:04' at UTC+1.

        The server stores timestamps in UTC; here they are converted to the local
        timezone so the My missions list matches the pilot's clock. Falls back to
        the raw value (trimmed) if it cannot be parsed, so display never fails."""
        raw = (updated_at or '').strip()
        if not raw:
            return 'never saved'
        try:
            # fromisoformat rejects a trailing 'Z' before Python 3.11 (QGIS 3), so
            # normalise it; a timestamp with no zone is treated as UTC.
            dt = datetime.datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone().strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            return raw[:16].replace('T', ' ')

    def _apply_website_mission(self, mission):
        """Rebuild a website mission as a local one. Everything the plugin
        cannot represent is rejected before any map state changes, so a refused
        load leaves the current plan untouched. Returns the settings whose value
        the plugin's own range could not hold, as lines for the caller to show:
        those are adjusted, never silently."""
        flypath_sync.validate_mission(mission)
        settings = mission.get('settings') or {}
        if not isinstance(settings, dict):
            raise FlypathSyncError('This mission\'s settings could not be read.')
        try:
            provenance = planning_adapter.validate_mission_provenance(mission)
        except ValueError as exc:
            raise FlypathSyncError(str(exc)) from None
        if settings.get('reverse_route'):
            raise FlypathSyncError('This mission uses a reversed route, which the plugin cannot edit. Disable Reverse route on the website first.')
        style = settings.get('mapping_style', '2d')
        if style not in ('2d', 'corridor'):
            raise FlypathSyncError(
                'This mission is a "%s" survey, which this plugin cannot plan. '
                'Only 2D and corridor missions can be loaded.' % style)
        corridor = style == 'corridor'

        drone_name = registry.name_for_website_code(mission.get('drone_model'))
        if drone_name is None or drone_name not in registry.names():
            raise FlypathSyncError(
                'This mission is planned for a drone this plugin does not '
                'offer, so its settings would not carry over.')

        capture = settings.get('capture_mode', 'semi')
        if capture not in ('semi', 'full'):
            raise FlypathSyncError(
                'This mission uses a capture mode this plugin does not know '
                '(%s).' % capture)
        finish = flypath_sync.label_for_code(
            flypath_sync.WEBSITE_FINISH_ACTIONS, settings.get('finish_action'))
        rc_lost = flypath_sync.label_for_code(
            flypath_sync.WEBSITE_RC_LOST_ACTIONS, settings.get('rc_lost_action'))
        for label, code, value in (
                ('finish action', settings.get('finish_action'), finish),
                ('RC lost action', settings.get('rc_lost_action'), rc_lost)):
            if code is not None and value is None:
                raise FlypathSyncError(
                    'This mission uses a %s this plugin does not offer (%s).'
                    % (label, code))

        points = mission.get('polygon') or []
        needed = 2 if corridor else 3
        if len(points) < needed:
            raise FlypathSyncError(
                'This mission has no %s yet — draw one on the website first, '
                'or plan it here.'
                % ('centre line' if corridor else 'survey area'))
        try:
            vertices = [QgsPointXY(float(lon), float(lat)) for lat, lon in points]
        except (TypeError, ValueError):
            raise FlypathSyncError('This mission\'s survey area could not be read.')

        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        geom = (QgsGeometry.fromPolylineXY(vertices) if corridor
                else QgsGeometry.fromPolygonXY([vertices]))
        if (geom.isEmpty() or not geom.isGeosValid()
                or (geom.length() <= 0 if corridor else geom.area() <= 0)):
            raise FlypathSyncError('This mission has an invalid survey geometry.')

        # ── Nothing above changed any state; from here the load applies. ──
        self._loading_mission = True
        self._planning.begin_import()
        # Restore Auto only after loading: intermediate control signals must
        # not optimise the imported heading against the previous survey area.
        self.autoDirectionBtn.setChecked(False)
        self.missionTypeCombo.setCurrentText(
            'Corridor Mapping' if corridor else '2D Mapping')
        self.droneModelCombo.setCurrentText(drone_name)
        (self.captureFullRadio if capture == 'full'
         else self.captureSemiRadio).setChecked(True)
        if settings.get('flight_path') in ('curved', 'straight'):
            (self.pathCurvedRadio if settings['flight_path'] == 'curved'
             else self.pathStraightRadio).setChecked(True)
        if finish:
            self.finishActionCombo.setCurrentText(finish)
        if rc_lost:
            self.rcLostActionCombo.setCurrentText(rc_lost)

        # Spin boxes clamp anything outside their own range, so a value the
        # website allows but this plugin does not lands at the nearest one it
        # can fly. Every such change is collected and reported, so the pilot is
        # never handed a plan quietly different from the one on the website.
        adjusted = []
        fields = [('altitude', self.altitudeSpin, settings.get('altitude')),
                  ('speed', self.speedSpin, settings.get('speed')),
                  ('side overlap', self.sideOverlapSpin, settings.get('side_overlap')),
                  ('front overlap', self.frontOverlapSpin, settings.get('front_overlap')),
                  ('margin', self.marginSpin, settings.get('margin')),
                  ('direction', self.directionSpin, settings.get('direction')),
                  ('terrain tolerance', self.terrainToleranceSpin,
                   settings.get('terrain_tolerance')),
                  ('max waypoints', self.maxWaypointsSpin, settings.get('split_max_wp'))]
        if isinstance(settings.get('corridor_width'), (int, float)):
            # The website's corridor_width is the full mapped width; the
            # plugin's Buffer is the half-width each side.
            fields.append(('buffer (half of the corridor width)', self.bufferSpin,
                           float(settings['corridor_width']) / 2.0))
        for label, widget, value in fields:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            bounded = max(widget.minimum(), min(widget.maximum(), value))
            widget.setValue(type(widget.value())(bounded))
            if abs(widget.value() - float(value)) > 1e-6:
                adjusted.append('%s: %g -> %g' % (label, value, widget.value()))
        self.crossHatchCheck.setChecked(bool(settings.get('cross_hatch')))
        self.terrainFollowCheck.setChecked(bool(settings.get('terrain_follow')))

        # A pulled mission is a standalone local copy, like a drawn one: it is
        # not tied to any layer feature, so it uses the Draw source and the same
        # construction path drawing does.
        self.sourceDrawRadio.setChecked(True)
        self._source_mode = 'draw'
        self._apply_source_mode()

        if corridor:
            self._show_drawn_line(geom, wgs84)
            self._set_survey_line(geom, wgs84)
        else:
            self._show_drawn_polygon(geom, wgs84)
            self._set_survey_polygon(geom, wgs84)
        # Geometry establishes the split range. An explicit saved choice must
        # override battery defaults even when setValue would emit no signal.
        self._split_overridden = True
        split_enabled = settings.get('split_enabled')
        self._setting_split = True
        if type(split_enabled) is bool:
            self.splitCheck.setChecked(split_enabled)
            self._split_choice_required = False
        elif mission.get('source_product') == 'plugin':
            self.splitCheck.setChecked(True)
            self._split_choice_required = False
        else:
            self.splitCheck.setChecked(False)
            self._split_choice_required = True
        self._setting_split = False
        self.splitSpin.setEnabled(self.splitCheck.isChecked())
        split_count = settings.get('split_count', 1)
        if split_enabled is False:
            split_count = 1
        if isinstance(split_count, (int, float)) and not isinstance(split_count, bool):
            self.splitSpin.setValue(int(max(self.splitSpin.minimum(),
                                           min(self.splitSpin.maximum(), split_count))))
            if self.splitSpin.value() != split_count:
                adjusted.append('split count: %g -> %g' % (split_count, self.splitSpin.value()))
        self._on_param_changed()
        self.autoDirectionBtn.setChecked(bool(settings.get('auto_direction')))
        self._loading_mission = False
        self._restore_imported_route(mission, provenance)
        self._zoom_to_website_geometry(geom, wgs84)
        return adjusted

    def _zoom_to_website_geometry(self, geom, crs):
        canvas = self.iface.mapCanvas()
        transform = QgsCoordinateTransform(
            crs, canvas.mapSettings().destinationCrs(), QgsProject.instance())
        extent = transform.transformBoundingBox(geom.boundingBox())
        # Pad both axes, including a straight corridor with zero width/height.
        extent.grow(max(extent.width(), extent.height(), 1e-6) * 0.1)
        canvas.setExtent(extent)
        canvas.refresh()
