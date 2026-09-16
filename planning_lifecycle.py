"""Ownership and transitions for the plugin's current planning result."""


class PlanningLifecycle:
    """Keep imported and generated planning state internally consistent."""

    def __init__(self):
        self.request = None
        self.result = None
        self.locations = None
        self.locked = False
        self.dirty = False
        self.unsupported = False

    def record(self, request, result):
        self.request = request
        self.result = result
        self.locations = request.get('locations')

    def plan_failed(self):
        self.request = None
        self.result = None

    def begin_import(self):
        self.locked = True
        self.dirty = False
        self.unsupported = False
        self.locations = None

    def preserve_imported_route(self, *, supported=None):
        self.locked = True
        self.dirty = False
        if supported is not None:
            self.unsupported = not supported

    def settings_changed(self):
        if self.locked:
            self.dirty = True
        return self.dirty

    def begin_preview(self, *, has_saved_route, split_choice_required=False):
        """Choose preview behavior and apply the transition into regeneration."""
        if self.locked and not self.dirty and has_saved_route:
            return 'restore'
        if self.unsupported:
            return 'unsupported'
        if split_choice_required:
            return 'choose_splitting'
        self.locked = False
        self.dirty = False
        return 'regenerate'

    def clear_preview(self):
        self.plan_failed()
        self.settings_changed()

    def reset(self):
        self.__init__()

    def save_requires_regeneration(self):
        return self.unsupported or (self.locked and self.dirty)

    def export_issue(self):
        if self.unsupported:
            return 'unsupported'
        if self.locked and self.dirty:
            return 'regeneration_required'
        if self.result and not self.result['validation']['export_allowed']:
            return 'validation'
        return None
