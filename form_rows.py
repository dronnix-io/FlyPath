"""Show and hide QFormLayout rows without leaving uneven gaps.

Qt 5 keeps the vertical spacing of a hidden form row, so a run of hidden rows
leaves a wider gap than the 6 px between visible rows (Qt 6 collapses them).
On Qt 5 a tracked form therefore takes a hidden row out of the layout and puts
it back at its original position when it is shown again. Its label is
remembered meanwhile, so label() still finds it.
"""

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QT_VERSION_STR
from qgis.PyQt.QtWidgets import QFormLayout

try:
    _FIELD = QFormLayout.ItemRole.FieldRole
    _SPANNING = QFormLayout.ItemRole.SpanningRole
except AttributeError:
    _FIELD = getattr(QFormLayout, 'FieldRole')
    _SPANNING = getattr(QFormLayout, 'SpanningRole')

COLLAPSE_HIDDEN_ROWS = QT_VERSION_STR.startswith('5.')


def _key(obj):
    return sip.unwrapinstance(obj)


def _field(form, row):
    # Explicit None checks: PyQt makes an empty QComboBox falsy (len() == 0).
    item = form.itemAt(row, _FIELD)
    if item is None:
        item = form.itemAt(row, _SPANNING)
    if item is None:
        return None
    widget = item.widget()
    return widget if widget is not None else item.layout()


def track(form):
    """Start collapsing hidden rows of a fully built form (Qt 5 only).

    Records the row order, then takes out every row whose widget is hidden.
    Rows must be shown and hidden through set_visible() from then on."""
    if not COLLAPSE_HIDDEN_ROWS:
        return
    order = [_key(_field(form, row)) for row in range(form.rowCount())
             if _field(form, row) is not None]
    form._flypath_rows = {'order': order, 'labels': {}}
    for row in reversed(range(form.rowCount())):
        field = _field(form, row)
        if field is not None and hasattr(field, 'isHidden') and field.isHidden():
            form._flypath_rows['labels'][_key(field)] = form.labelForField(field)
            form.takeRow(row)


def label(form, widget):
    """The row's label widget, also while the row is taken out of the form."""
    found = form.labelForField(widget)
    if found is None:
        state = getattr(form, '_flypath_rows', None)
        if state is not None:
            found = state['labels'].get(_key(widget))
    return found


def set_visible(form, widget, visible):
    """Show or hide a form row: its field (or spanning) widget and its label."""
    row_label = label(form, widget)
    state = getattr(form, '_flypath_rows', None)
    if state is not None:
        key = _key(widget)
        row, _role = form.getWidgetPosition(widget)
        if not visible and row >= 0:
            state['labels'][key] = row_label
            form.takeRow(row)
        elif visible and row < 0 and key in state['labels']:
            present = {_key(_field(form, r)) for r in range(form.rowCount())
                       if _field(form, r) is not None}
            index = sum(1 for other in state['order'][:state['order'].index(key)]
                        if other in present)
            row_label = state['labels'].pop(key)
            if row_label is None:
                form.insertRow(index, widget)
            else:
                form.insertRow(index, row_label, widget)
    widget.setVisible(visible)
    if row_label is not None:
        row_label.setVisible(visible)
    if state is not None and form.parentWidget() is not None:
        form.invalidate()
        form.parentWidget().updateGeometry()    # let the section grow or shrink
