"""Hidden form rows collapse and come back in their original place."""

import importlib
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_hidden_rows_are_taken_out_and_restored_in_order():
    try:
        from qgis.PyQt.QtWidgets import QApplication, QComboBox, QFormLayout, QSpinBox, QWidget
        package = Path(__file__).resolve().parents[1].name
        form_rows = importlib.import_module(package + '.form_rows')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc

    app = QApplication.instance() or QApplication([])
    collapse = form_rows.COLLAPSE_HIDDEN_ROWS
    form_rows.COLLAPSE_HIDDEN_ROWS = True       # exercise the Qt 5 path on any Qt
    try:
        host = QWidget()
        form = QFormLayout(host)
        widgets = {name: QSpinBox() for name in ('A', 'B', 'C', 'D')}
        widgets['E'] = QComboBox()              # empty: falsy in PyQt, still a row
        for name, widget in widgets.items():
            form.addRow(name, widget)
        spanning = QWidget()
        form.addRow(spanning)
        widgets['C'].setVisible(False)
        form_rows.track(form)

        def labels():
            names = []
            for row in range(form.rowCount()):
                row_label = form.labelForField(form_rows._field(form, row))
                names.append(row_label.text() if row_label is not None else 'span')
            return names

        assert labels() == ['A', 'B', 'D', 'E', 'span']
        assert form_rows.label(form, widgets['C']).text() == 'C'
        form_rows.set_visible(form, widgets['B'], False)
        form_rows.set_visible(form, widgets['E'], False)
        form_rows.set_visible(form, spanning, False)
        assert labels() == ['A', 'D']
        form_rows.set_visible(form, widgets['E'], True)
        form_rows.set_visible(form, widgets['C'], True)
        form_rows.set_visible(form, spanning, True)
        form_rows.set_visible(form, widgets['B'], True)
        assert labels() == ['A', 'B', 'C', 'D', 'E', 'span']
        assert not widgets['C'].isHidden() and not form_rows.label(form, widgets['C']).isHidden()
        form_rows.set_visible(form, widgets['A'], True)      # already shown: no change
        assert labels() == ['A', 'B', 'C', 'D', 'E', 'span']
    finally:
        form_rows.COLLAPSE_HIDDEN_ROWS = collapse
    app.processEvents()


if __name__ == '__main__':
    test_hidden_rows_are_taken_out_and_restored_in_order()
    print('form row checks passed')
