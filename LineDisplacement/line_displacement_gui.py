# line_displacement_gui.py
import os
from PyQt5.QtCore import QUrl
from qgis.PyQt import QtWidgets, QtCore, QtGui
from qgis.gui import QgsProjectionSelectionWidget
from qgis.core import QgsCoordinateReferenceSystem
from .i18n import t

class LineDisplacementDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Kartografische Linienverdrängung", "Cartographic Line Displacement"))
        self.resize(900, 700)

        # Hauptlayout: alles auf einer senkrechten Achse
        # --- Hauptlayout: links Inhalt, rechts Infofeld ---
        main_h = QtWidgets.QHBoxLayout(self)     # Gesamtlayout des Dialogs
        main_h.setContentsMargins(8, 8, 8, 8)
        main_h.setSpacing(10)

        # Linke Seite: bisheriger Inhalt kommt in dieses VBox-Layout
        main_v = QtWidgets.QVBoxLayout()
        main_v.setSpacing(10)
        main_h.addLayout(main_v, 2)              # links bekommt Verhältnis 2 (breiter)

        # -------- Hilfsfunktion: Block mit Filter + ankreuzbarer Liste --------
        def build_layer_block(titel):
            box = QtWidgets.QGroupBox(titel)
            v = QtWidgets.QVBoxLayout(box)

            # Filterzeile
            fl = QtWidgets.QHBoxLayout()
            fl.addWidget(QtWidgets.QLabel(t("Filter:","Filter")))
            le_filter = QtWidgets.QLineEdit()
            le_filter.setPlaceholderText(t("Ebenenname filtern …","Filter layer name …"))
            fl.addWidget(le_filter, 1)
            v.addLayout(fl)

            # ankreuzbare Liste
            lw = QtWidgets.QListWidget()
            lw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            lw.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            v.addWidget(lw, 1)

            # Auswahlknöpfe
            btnrow = QtWidgets.QHBoxLayout()
            btn_all  = QtWidgets.QPushButton(t("Alle","All"))
            btn_none = QtWidgets.QPushButton(t("Keine","None"))
            btn_inv  = QtWidgets.QPushButton(t("Invertieren","Invert"))
            btnrow.addWidget(btn_all); btnrow.addWidget(btn_none); btnrow.addWidget(btn_inv); btnrow.addStretch(1)
            v.addLayout(btnrow)

            # „Nur gewählte Objekte“-Schalter
            chk_selected = QtWidgets.QCheckBox(t("nur gewählte Objekte (temporäre Teil-Ebene)","Selection only (temporary sub-layer)"))
            v.addWidget(chk_selected)

            # -------- Vor-Verarbeitung: Vereinfachen / Glätten --------
            pre = QtWidgets.QGroupBox(t("Vor-Verarbeitung","Pre-processing"))
            grid = QtWidgets.QGridLayout(pre)
            grid.setContentsMargins(8, 6, 8, 8)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(6)

            # Vereinfachung
            chk_simplify = QtWidgets.QCheckBox(t("Vereinfachen","Simplify"))
            spin_simplify = QtWidgets.QDoubleSpinBox()
            spin_simplify.setDecimals(6)
            spin_simplify.setRange(0.0, 1e9)
            spin_simplify.setValue(0.0)
            spin_simplify.setEnabled(False)

            # Glättung
            chk_smooth = QtWidgets.QCheckBox(t("Glätten","Smooth"))
            spin_smooth_off = QtWidgets.QDoubleSpinBox()
            spin_smooth_off.setDecimals(6)
            spin_smooth_off.setRange(0.0, 1e9)
            spin_smooth_off.setValue(0.0)
            spin_smooth_off.setEnabled(False)

            spin_smooth_iter = QtWidgets.QSpinBox()
            spin_smooth_iter.setRange(0, 999999)
            spin_smooth_iter.setValue(0)
            spin_smooth_iter.setEnabled(False)

            # Layout:
            # Spalte 0 = Checkboxen
            # Spalte 1 = Label (rechtsbündig), direkt VOR dem Feld
            # Spalte 2 = Eingabefelder
            grid.addWidget(chk_simplify, 0, 0)
            lbl_abst = QtWidgets.QLabel(t("Abstand:","Distance:"))
            grid.addWidget(lbl_abst, 0, 1, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(spin_simplify, 0, 2)

            grid.addWidget(chk_smooth, 1, 0)
            lbl_off = QtWidgets.QLabel(t("Offset:","Offset:"))
            grid.addWidget(lbl_off, 1, 1, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(spin_smooth_off, 1, 2)

            lbl_it  = QtWidgets.QLabel(t("Iterationen:","Iterations:"))
            grid.addWidget(lbl_it, 2, 1, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(spin_smooth_iter, 2, 2)

            grid.setColumnStretch(2, 1)

            # Aktivierung koppeln
            chk_simplify.toggled.connect(spin_simplify.setEnabled)
            def _toggle_smooth(on: bool):
                spin_smooth_off.setEnabled(on)
                spin_smooth_iter.setEnabled(on)
            chk_smooth.toggled.connect(_toggle_smooth)

            v.addWidget(pre)

            # Filterlogik
            def apply_filter():
                txt = le_filter.text().strip().lower()
                for i in range(lw.count()):
                    it = lw.item(i)
                    it.setHidden(txt not in it.text().lower())
            le_filter.textChanged.connect(apply_filter)

            # Auswahlknöpfe
            def set_all(state):
                for i in range(lw.count()):
                    it = lw.item(i)
                    if not it.isHidden():
                        it.setCheckState(QtCore.Qt.Checked if state else QtCore.Qt.Unchecked)
            def invert():
                for i in range(lw.count()):
                    it = lw.item(i)
                    if not it.isHidden():
                        it.setCheckState(QtCore.Qt.Unchecked if it.checkState()==QtCore.Qt.Checked else QtCore.Qt.Checked)

            btn_all.clicked.connect(lambda: set_all(True))
            btn_none.clicked.connect(lambda: set_all(False))
            btn_inv.clicked.connect(invert)

            # Doppelklick = Häkchen umschalten
            lw.itemDoubleClicked.connect(lambda it: it.setCheckState(
                QtCore.Qt.Unchecked if it.checkState()==QtCore.Qt.Checked else QtCore.Qt.Checked))

            # Rückgabe: plus alle neuen Widgets
            return (box, lw, chk_selected, le_filter,
                    chk_simplify, spin_simplify,
                    chk_smooth, spin_smooth_off, spin_smooth_iter)

        # -------- zwei Blöcke nebeneinander --------
        fixed_box, self.fixed_list, self.chk_fixed_selected, self.fixed_filter, \
            self.chk_fixed_simplify, self.spin_fixed_simplify, \
            self.chk_fixed_smooth, self.spin_fixed_smooth_off, self.spin_fixed_smooth_iter = build_layer_block(t("Bleibende Geometrie","Fixed geometry"))

        move_box,  self.move_list,  self.chk_move_selected,  self.move_filter, \
            self.chk_move_simplify, self.spin_move_simplify, \
            self.chk_move_smooth, self.spin_move_smooth_off, self.spin_move_smooth_iter = build_layer_block(t("Zu verdrängende Geometrie","Geometry to be displaced"))

        top_h = QtWidgets.QHBoxLayout()
        top_h.addWidget(fixed_box, 1)
        top_h.addWidget(move_box, 1)
        main_v.addLayout(top_h, 2)  # viel Platz oben

        # -------- Verdrängungs-Parameter --------
        displ_box = QtWidgets.QGroupBox(t("Verdrängungs-Parameter","Displacement parameters"))
        form_disp = QtWidgets.QFormLayout(displ_box)

        self.spin_buf = QtWidgets.QDoubleSpinBox()
        self.spin_buf.setDecimals(6)
        self.spin_buf.setRange(0.0, 1e9)
        self.spin_buf.setValue(0.0)
        self.spin_buf.setMinimumWidth(70)

        self.spin_minlen = QtWidgets.QDoubleSpinBox()
        self.spin_minlen.setDecimals(6)
        self.spin_minlen.setRange(0.0, 1e9)
        self.spin_minlen.setValue(0.0)
        self.spin_minlen.setMinimumWidth(70)

        form_disp.addRow(t("Verdrängungs-Abstand:", "Displacement distance:"), self.spin_buf)
        form_disp.addRow(t("Mindestlänge verdrängter Strecken:", "Minimal length of displaced segments:"), self.spin_minlen)

        # -------- Netzverknüpfung zu verdrängender Geometrie --------
        merge_box = QtWidgets.QGroupBox(t("Zu verdrängende Geometrie: Fragmente verknüpfen","Geometry to be discplaced: connect fragments"))
        grid = QtWidgets.QGridLayout(merge_box)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.chk_merge_pre  = QtWidgets.QCheckBox(t("vorher","Before"))
        self.chk_merge_pre.setChecked(True)
        self.chk_merge_post = QtWidgets.QCheckBox(t("nachher","After"))
        bold_font = self.chk_merge_pre.font()
        bold_font.setBold(True)
        self.chk_merge_pre.setFont(bold_font)
        self.chk_merge_post.setFont(bold_font)
        grid.addWidget(QtWidgets.QLabel(""), 0, 0)
        grid.addWidget(self.chk_merge_pre,  0, 1, alignment=QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        grid.addWidget(self.chk_merge_post, 0, 2, alignment=QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        row = 1
        def _mk_row(label_text, widget_pre, widget_post):
            nonlocal row
            lab = QtWidgets.QLabel(label_text)
            grid.addWidget(lab,        row, 0, alignment=QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            grid.addWidget(widget_pre, row, 1)
            grid.addWidget(widget_post,row, 2)
            row += 1

        self.chk_split_pre  = QtWidgets.QCheckBox(); self.chk_split_post = QtWidgets.QCheckBox()
        _mk_row(t("vorab an Kreuzungen zerlegen:", "Initially split at intersections:"), self.chk_split_pre, self.chk_split_post)
        self.chk_even_pre   = QtWidgets.QCheckBox(); self.chk_even_post = QtWidgets.QCheckBox()
        _mk_row(t("ungerade Kreuzungen nicht verknüpfen:", "Dont’t connect uneven intersections:"), self.chk_even_pre, self.chk_even_post)

        self.spin_tol_pre  = QtWidgets.QDoubleSpinBox(); self.spin_tol_pre.setDecimals(6); self.spin_tol_pre.setRange(0.0, 1e9); self.spin_tol_pre.setValue(0.0); self.spin_tol_pre.setMinimumWidth(70)
        self.spin_tol_post = QtWidgets.QDoubleSpinBox(); self.spin_tol_post.setDecimals(6); self.spin_tol_post.setRange(0.0, 1e9); self.spin_tol_post.setValue(0.0); self.spin_tol_post.setMinimumWidth(70)
        _mk_row(t("Toleranz gegenüber Löchern:", "Tolerance for holes:"), self.spin_tol_pre, self.spin_tol_post)

        self.spin_ang_pre  = QtWidgets.QDoubleSpinBox(); self.spin_ang_pre.setDecimals(3); self.spin_ang_pre.setRange(0.0, 180.0); self.spin_ang_pre.setValue(90.0); self.spin_ang_pre.setMinimumWidth(70)
        self.spin_ang_post = QtWidgets.QDoubleSpinBox(); self.spin_ang_post.setDecimals(3); self.spin_ang_post.setRange(0.0, 180.0); self.spin_ang_post.setValue(90.0); self.spin_ang_post.setMinimumWidth(70)
        _mk_row(t("maximale Winkelabweichung (°):", "Maximum angle deviation (°):"), self.spin_ang_pre, self.spin_ang_post)

        self.spin_simpl_pre  = QtWidgets.QDoubleSpinBox(); self.spin_simpl_pre.setDecimals(6); self.spin_simpl_pre.setRange(0.0, 10.0); self.spin_simpl_pre.setValue(0.0); self.spin_simpl_pre.setMinimumWidth(70)
        self.spin_simpl_post = QtWidgets.QDoubleSpinBox(); self.spin_simpl_post.setDecimals(6); self.spin_simpl_post.setRange(0.0, 10.0); self.spin_simpl_post.setValue(0.0); self.spin_simpl_post.setMinimumWidth(70)
        _mk_row(t("vereinfachende Betrachtung:", "Simplified assessment:"), self.spin_simpl_pre, self.spin_simpl_post)

        self.spin_it_pre  = QtWidgets.QSpinBox(); self.spin_it_pre.setRange(0, 999999); self.spin_it_pre.setValue(10); self.spin_it_pre.setMinimumWidth(70)
        self.spin_it_post = QtWidgets.QSpinBox(); self.spin_it_post.setRange(0, 999999); self.spin_it_post.setValue(10); self.spin_it_post.setMinimumWidth(70)
        _mk_row(t("maximale Iterationen:", "Maximum iterations:"), self.spin_it_pre, self.spin_it_post)

        def _set_enabled_col(pre_col: bool, on: bool):
            widgets = [
                (self.chk_split_pre,  self.chk_split_post),
                (self.chk_even_pre,   self.chk_even_post),
                (self.spin_tol_pre,   self.spin_tol_post),
                (self.spin_ang_pre,   self.spin_ang_post),
                (self.spin_simpl_pre, self.spin_simpl_post),
                (self.spin_it_pre,    self.spin_it_post),
            ]
            idx = 0 if pre_col else 1
            for pair in widgets:
                pair[idx].setEnabled(on)

        self.chk_merge_pre.setChecked(True);  _set_enabled_col(True,  True)
        self.chk_merge_post.setChecked(False); _set_enabled_col(False, False)
        self.chk_merge_pre.toggled.connect(lambda on:  _set_enabled_col(True,  on))
        self.chk_merge_post.toggled.connect(lambda on: _set_enabled_col(False, on))

        # -------- Ziel-Ebene (Ihr bisheriger target_box-Block bleibt) --------
        target_box = QtWidgets.QGroupBox(t("Ziel-Ebene","Target layer"))
        v3 = QtWidgets.QVBoxLayout(target_box)
        self.radio_new_temp = QtWidgets.QRadioButton(t("neue temporäre Ebene", "New temporary layer")); self.radio_new_temp.setChecked(True)
        self.proj_selector  = QgsProjectionSelectionWidget()
        self.proj_selector.setMinimumWidth(130)
        self.radio_existing = QtWidgets.QRadioButton(t("vorhandene Ebene","Existing layer"))
        self.cmb_existing   = QtWidgets.QComboBox(); self.cmb_existing.setEnabled(False)

        grid_t = QtWidgets.QGridLayout(); grid_t.setContentsMargins(0,0,0,0); grid_t.setHorizontalSpacing(8); grid_t.setVerticalSpacing(6)
        grid_t.setColumnStretch(2, 1)
        grid_t.addWidget(self.radio_new_temp, 0, 0)
        grid_t.addWidget(QtWidgets.QLabel(t("KBS:","CRS:")), 0, 1, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        grid_t.addWidget(self.proj_selector, 0, 2)
        self.proj_selector.setMinimumWidth(100)   # << hinzugefügt
        grid_t.addWidget(self.radio_existing, 1, 0)
        grid_t.addWidget(QtWidgets.QLabel(""), 1, 1)
        grid_t.addWidget(self.cmb_existing, 1, 2)
        self.cmb_existing.setMinimumWidth(100)    # << hinzugefügt
        v3.addLayout(grid_t)

        self.radio_replace = QtWidgets.QRadioButton(t("vorhandene Geometrie ersetzen","Replace existing geometry")); self.radio_replace.setChecked(True)
        self.radio_append  = QtWidgets.QRadioButton(t("neue Geometrie anhängen","Append new geometry"))
        self.grp_ins_mode = QtWidgets.QWidget()
        h_mode = QtWidgets.QHBoxLayout(self.grp_ins_mode); h_mode.setContentsMargins(0,0,0,0)
        h_mode.addWidget(self.radio_replace); h_mode.addSpacing(16); h_mode.addWidget(self.radio_append); h_mode.addStretch(1)
        v3.addWidget(self.grp_ins_mode)

        def _toggle_target_controls():
            use_new = self.radio_new_temp.isChecked()
            self.proj_selector.setEnabled(use_new)
            self.cmb_existing.setEnabled(self.radio_existing.isChecked())
            self.grp_ins_mode.setEnabled(self.radio_existing.isChecked())
        self.radio_new_temp.toggled.connect(_toggle_target_controls)
        self.radio_existing.toggled.connect(_toggle_target_controls)
        _toggle_target_controls()

        # -------- Zwei-Spalten-Anordnung --------
        middle_h = QtWidgets.QHBoxLayout()
        left_v   = QtWidgets.QVBoxLayout()
        left_v.addWidget(displ_box)   # oben links: Verdrängung
        left_v.addWidget(target_box)  # darunter:   Ziel-Ebene
        middle_h.addLayout(left_v, 1) # linke Spalte
        middle_h.addWidget(merge_box, 1)  # rechte Spalte: Netzverknüpfung
        main_v.addLayout(middle_h, 1)

        # -------- Fortgeschrittene Optionen --------
        adv = QtWidgets.QGroupBox(t("Fortgeschrittene Optionen","Advanced options"))
        v4 = QtWidgets.QVBoxLayout(adv)

        # Debug-Stufen
        v4.addWidget(QtWidgets.QLabel(t("Debug-Stufe:","Debug stage")))
        self.cmb_debug = QtWidgets.QComboBox()

        # Monospace-Schrift nur für die Popup-Liste setzen
        view = QtWidgets.QListView()
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        view.setFont(mono)
        self.cmb_debug.setView(view)

        debug_items = [
            ("final",                t("Endergebnis", "Final result")),
            ("union_to_move",        t("Vereinigung der zu verdrängenden Geometrie", "Union of geometry to be displaced")),
            ("union_fixed",          t("Vereinigung der bleibenden Geometrie", "Union of fixed geometry")),
            ("fixed_buffer_poly",    t("Pufferfläche um die bleibende Geometrie", "Buffer area around fixed geometry")),
            ("fixed_boundary",       t("Pufferkontur um die bleibende Geometrie", "Buffer boundary around fixed geometry")),
            ("to_move_in_buffer",    t("Teile der zu verdrängenden Geometrie im Puffer", "Parts of the geometry-to-be-displaced in the buffer")),
            ("loops",                t("mutmaßliche Schlaufen im Puffer", "Assumed loops in the buffer")),
            ("loops_union",          t("Vereinigung der Schlaufen im Puffer", "Union of loops in the buffer")),
            ("loops_buffer",         t("verbundweise gebildeter Schlaufenpuffer", "Loop buffer per connected component")),
            ("buffer_blobs",         t("Einzelne Schlaufenpuffer-Blasen", "Individual loop buffer blobs")),
            ("used_blobs",           t("Blasen mit Ersatzsegment", "Blobs with replacement segment")),
            ("unused_blobs",         t("Blasen ohne Ersatzsegment", "Blobs without replacement segment")),
            ("boundary_segments",    t("zerschnittene Pufferkontur-Segmente", "Split buffer-boundary segments")),
            ("segments_to_move",     t("alle zu verdrängenden Segmente im Puffer", "All segments-to-be-displaced in the buffer")),
            ("replacement_segments", t("akzeptierte Ersatzsegmente", "Accepted replacement segments")),
            ("rejected_replacements",t("verworfenene Ersatzsegmente", "Rejected replacement segments")),
            ("crossers",             t("als Durchgänger erkannte Segmente", "Segments identified as crossers")),
            ("rest",                 t("ursprüngliche Geometrie ohne Pufferanteile", "Original geometry without buffer parts")),
            ("pre_final",            t("wie final, nur ohne Schreiben", "Like final, but without writing")),
        ]
        # Breite der ersten „Spalte“ ermitteln und links auffüllen
        pad = max(len(k) for k, _ in debug_items)
        for key, desc in debug_items:
            display = f"{key.ljust(pad)} {desc}"
            self.cmb_debug.addItem(display, key)

        self.cmb_debug.setCurrentIndex(0)
        v4.addWidget(self.cmb_debug)

        # Danach die Optionen (bleibt links)
        self.chk_leave_symbol = QtWidgets.QCheckBox(
            t("Symbolebene hinterlassen, deren Geometriegenerator die Verdrängung auch ohne Plugin steuern kann", "Leave symbol layer whose geometry generator can control displacement even without plugin")
        )
        self.chk_leave_symbol.setChecked(True)
        v4.addWidget(self.chk_leave_symbol)

        self.chk_log = QtWidgets.QCheckBox(t("Logdatei auf Desktop ablegen","Write log file on desktop"))
        v4.addWidget(self.chk_log)

        # Fortgeschritten-Box zum linken Container
        main_v.addWidget(adv)

        # -------- Start / Abbruch (links, unter alles andere) --------
        btnrow = QtWidgets.QHBoxLayout()
        self.btn_ok = QtWidgets.QPushButton(t("Los","Run"))
        self.btn_cancel = QtWidgets.QPushButton(t("Abbrechen","Cancel"))
        btnrow.addStretch(1)
        btnrow.addWidget(self.btn_ok)
        btnrow.addWidget(self.btn_cancel)
        main_v.addLayout(btnrow)

        # -------- Infofeld rechts anlegen und zum Hauptlayout hinzufügen --------
        info_box = QtWidgets.QTextBrowser()
        info_box.setOpenExternalLinks(True)
        info_box.setMinimumWidth(260)      # gewünschte Breite rechts
        info_box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        hilfe_pfad = os.path.join(os.path.dirname(__file__), t("hilfe.html", "help.html"))
        info_box.setSource(QUrl.fromLocalFile(hilfe_pfad))

        # Rechts zum Hauptlayout
        main_h.addWidget(info_box, 1)      # rechts, schmaler (Stretch 1)

        # Signale
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        # --- Ganz am Ende: das horizontale Hauptlayout setzen ---
        self.setLayout(main_h)
