import os
from datetime import datetime

from qgis.PyQt import QtWidgets, QtCore, QtGui
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsWkbTypes,
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsApplication, QgsLineSymbol, QgsSingleSymbolRenderer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransformContext
)
from qgis.utils import iface

from .line_displacement_gui import LineDisplacementDialog
from .expression_builder import build_line_displacement_call
from . import registrar
from .i18n import t


# ------------ gemeinsames Logging (nur wenn aktiviert) ------------
MAIN_LOG_ENABLED = False  # wird in run() anhand der GUI-Option gesetzt

def _logfile_path():
    home = os.path.expanduser("~")
    return os.path.join(home, "Desktop", "line_displacement.log")

def _log_to_file(text: str):
    if not MAIN_LOG_ENABLED:
        return
    try:
        with open(_logfile_path(), "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"[main] {datetime.now().isoformat(timespec='seconds')}  {text}\n")
    except Exception:
        pass
    # immer auch in die Python-Konsole
    print(f"[main] {text}")

def _bar(self, level, msg):
    _log_to_file(t(f"MITTEILUNGSLEISTE[{level}] {msg}", f"BAR[{level}] {msg}"))
    if level == "info":
        self.iface.messageBar().pushInfo(t("Linienverdrängung", "Line Displacement"), msg)
    elif level == "warn":
        self.iface.messageBar().pushWarning(t("Linienverdrängung", "Line Displacement"), msg)
    elif level == "crit":
        self.iface.messageBar().pushCritical(t("Linienverdrängung", "Line Displacement"), msg)
    else:
        self.iface.messageBar().pushSuccess(t("Linienverdrängung", "Line Displacement"), msg)

def _dump_expr(expr_text: str):
    trunc = (expr_text[:1500] + " …") if len(expr_text) > 1500 else expr_text
    _log_to_file(t("AUSDRUCK = \n", "EXPR = \n") + trunc.replace("\r", ""))


# ------------ weitere Definitionen ------------
def _pick_ref_crs(self, moving, fixed):
    """Wählt ein Referenz-CRS für die neue Zielebene und meldet, ob Eingaben gemischt sind."""
    prj = QgsProject.instance()

    def _crs_of(name):
        lst = prj.mapLayersByName(name)
        return lst[0].crs() if lst else None

    cands = []
    for L in moving:
        c = _crs_of(L['name'])
        if c and c.isValid():
            cands.append(c)
    if not cands:
        for L in fixed:
            c = _crs_of(L['name'])
            if c and c.isValid():
                cands.append(c)

    if cands:
        first = cands[0]
        mixed = any(c.authid() != first.authid() for c in cands)
        return first, mixed

    return prj.crs(), False

def _set_layer_visible(layer, visible: bool):
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(bool(visible))

def _wait(ms: int):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()

def _add_geomgen_symbol(layer: QgsVectorLayer, expr_text: str) -> bool:
    """
    Hängt einen Geometriegenerator (Linie) als zusätzliche Symbolebene an
    und lässt ihn aktiv (sichtbar), damit der Ausdruck sofort ausgewertet wird.
    Umsetzung über die Symbol-Registry von QgsApplication.
    """
    try:
        base_symbol = layer.renderer().symbol().clone() if layer.renderer() else QgsLineSymbol()

        reg = QgsApplication.symbolLayerRegistry()
        md = reg.symbolLayerMetadata("GeometryGenerator")
        if md is None:
            _log_to_file(t("Geometriegenerator-FEHLER: Metadata 'GeometryGenerator' nicht gefunden.", "Geometry generator ERROR: Metadata 'GeometryGenerator' not found."))
            return False

        # Basis-GG erzeugen (Expression setzen wir gleich explizit)
        gg = md.createSymbolLayer({'geometryType': 'LineString'})
        if gg is None:
            _log_to_file(t("Geometriegenerator-FEHLER: createSymbolLayer lieferte None.", "Geometry generator ERROR: createSymbolLayer returned None."))
            return False

        # --- Expression explizit setzen (API-Namen variieren je nach QGIS-Version) ---
        set_ok = False
        for meth in ("setGeometryExpression", "setExpression"):
            if hasattr(gg, meth):
                getattr(gg, meth)(expr_text)
                set_ok = True
                _log_to_file(t(f"Geometriegenerator-Ausdruck gesetzt via {meth} (len={len(expr_text)})", f"Geometry generator expression set via {meth} (len={len(expr_text)})"))
                break
        if not set_ok:
            _log_to_file(t("WARN: Konnte keine Methode zum Setzen des Ausdrucks finden.", "WARN: Could not find a method to set the expression."))

        # Unter-Symbol (einfache Linie)
        inner = QgsLineSymbol.createSimple({})
        # QGIS 3.x: Unter-Symbol via setSubSymbol
        if hasattr(gg, "setSubSymbol"):
            gg.setSubSymbol(inner)
        else:
            _log_to_file(t("WARN: setSubSymbol nicht vorhanden – Unter-Symbol konnte nicht gesetzt werden.", "WARN: setSubSymbol not available – could not set sub-symbol."))

        gg.setEnabled(True)  # während der Berechnung aktiv

        base_symbol.appendSymbolLayer(gg)
        layer.setRenderer(QgsSingleSymbolRenderer(base_symbol))
        layer.triggerRepaint()

        _log_to_file(t("Geometriegenerator hinzugefügt (aktiv; Expression explizit gesetzt).", "Geometry generator added (active; expression set explicitly)."))
        return True
    except Exception as e:
        _log_to_file(t(f"Geometriegenerator-FEHLER: {e}", f"Geometry generator ERROR: {e}"))
        return False

def _cleanup_geomgen(layer: QgsVectorLayer, leave_symbol: bool):
    """
    Entfernt die Geometriegenerator-Symbolebenen absturzfrei (oder lässt sie deaktiviert stehen).
    Vorgehen:
      - immer auf einem KLON des Symbols arbeiten
      - rückwärts löschen
      - den bereinigten Klon als Renderer setzen
    """
    try:
        r = layer.renderer()
        if not r:
            _log_to_file(t("Aufräumen: Kein Renderer vorhanden.", "Cleanup: No renderer present."))
            return
        orig = r.symbol()
        if not orig:
            _log_to_file(t("Aufräumen: Kein Symbol vorhanden.", "Cleanup: No symbol present."))
            return

        # immer erst klonen – nie am "lebenden" Symbol schneiden
        sym = orig.clone()

        if leave_symbol:
            # nur deaktivieren (im Klon), Optik bleibt 1:1 erhalten
            changed = 0
            for i in range(sym.symbolLayerCount()):
                sl = sym.symbolLayer(i)
                if getattr(sl, "layerType", lambda: "")() == "GeometryGenerator":
                    sl.setEnabled(False)
                    changed += 1
            layer.setRenderer(QgsSingleSymbolRenderer(sym))
            layer.triggerRepaint()
            _log_to_file(t(f"Aufräumen: Geometriegenerator deaktiviert hinterlassen (count={changed}).", f"Cleanup: Geometry generator left disabled (count={changed})."))
        else:
            # rückwärts löschen im Klon
            removed = 0
            for i in range(sym.symbolLayerCount() - 1, -1, -1):
                sl = sym.symbolLayer(i)
                if getattr(sl, "layerType", lambda: "")() == "GeometryGenerator":
                    sym.deleteSymbolLayer(i)
                    removed += 1
            # bereinigten Klon setzen
            layer.setRenderer(QgsSingleSymbolRenderer(sym))
            layer.triggerRepaint()
            _log_to_file(t(f"Aufräumen: Geometriegenerator entfernt (count={removed}).", f"Cleanup: Geometry generator removed (count={removed})."))
    except Exception as e:
        _log_to_file(t(f"Aufräum-Fehler: {e}", f"Cleanup error: {e}"))

class LineDisplacement:
    def __init__(self, iface_):
        self.iface = iface_
        self.action = None
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QtGui.QIcon(icon_path)
        self.action = QtWidgets.QAction(icon, t("Linienverdrängung", "Line Displacement"), self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(t("Linienverdrängung", "Line Displacement"), self.action)
        ok, msg = registrar.import_scripts_session(self.plugin_dir)
        if not ok:
            self.iface.messageBar().pushWarning(t("Linienverdrängung", "Line Displacement"), msg)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu(t("Linienverdrängung", "Line Displacement"), self.action)

    def _ensure_temp_subset_layer(self, lyr, only_selected):
        """
        Erzeugt bei Bedarf eine temporäre Teil-Ebene mit nur den ausgewählten Objekten.
        Wichtig: Geometrietyp-String (z. B. 'LineString') statt WKB-Enum-Zahl verwenden.
        """
        if not only_selected:
            return lyr

        sel = lyr.selectedFeatureIds()
        if not sel:
            return lyr

        crs = lyr.crs().authid() if lyr.crs().isValid() else "EPSG:3857"

        # Primär mit exaktem WKB-String
        geom_str = QgsWkbTypes.displayString(lyr.wkbType())  # z. B. "LineString", "MultiPolygon", ...
        out = QgsVectorLayer(f"{geom_str}?crs={crs}", f"_ld_tmp_{lyr.name()}", "memory")

        # Fallback, falls displayString nicht akzeptiert wird
        if not out.isValid():
            base = {
                QgsWkbTypes.LineGeometry: "LineString",
                QgsWkbTypes.PolygonGeometry: "Polygon",
                QgsWkbTypes.PointGeometry: "Point"
            }.get(lyr.geometryType(), "LineString")
            out = QgsVectorLayer(f"{base}?crs={crs}", f"_ld_tmp_{lyr.name()}", "memory")

        prov = out.dataProvider()
        out.updateFields()

        feats = []
        for f in lyr.getFeatures():
            if f.id() in sel and f.geometry() and not f.geometry().isEmpty():
                nf = QgsFeature()
                nf.setGeometry(f.geometry())
                feats.append(nf)
        if feats:
            prov.addFeatures(feats)

        # Nicht in die Legende aufnehmen
        QgsProject.instance().addMapLayer(out, addToLegend=False)
        return out

    def _collect_layers_from_list(self, names, selected_only,
                                  simplify=None,
                                  smooth_enabled=False,
                                  smooth_offset=None,
                                  smooth_iter=None):
        prj = QgsProject.instance()
        result = []
        for name in names:
            lst = prj.mapLayersByName(name)
            if not lst:
                continue
            lyr = lst[0]
            lyr2 = self._ensure_temp_subset_layer(lyr, selected_only)
            result.append({
                'name': lyr2.name(),
                'simplify': simplify,
                'smooth_enabled': bool(smooth_enabled),
                'smooth_offset': smooth_offset,
                'smooth_iter': smooth_iter,
                'crs_authid': lyr.crs().authid() if lyr.crs().isValid() else None
            })
        return result

    def _create_target_layer(self, name=None, crs=None):
        if name is None:
            name = t("verdrängte Geometrie", "displaced geometry")
        prj = QgsProject.instance()
        crs_used = crs if (crs and crs.isValid()) else (prj.crs() if prj.crs().isValid() else QgsCoordinateReferenceSystem("EPSG:4326"))
        auth = crs_used.authid() if crs_used.isValid() else "EPSG:4326"

        vlyr = QgsVectorLayer(f"LineString?crs={auth}", name, "memory")
        prov = vlyr.dataProvider()

        # kleines Dummy-Feature (damit der Geometriegenerator sofort rendern kann)
        f = QgsFeature()
        if crs_used.isGeographic():
            geom_wkt = "LINESTRING(0 0, 0 0.000001)"
        else:
            geom_wkt = "LINESTRING(0 0, 0 1)"
        f.setGeometry(QgsGeometry.fromWkt(geom_wkt))
        prov.addFeatures([f])

        QgsProject.instance().addMapLayer(vlyr, True)
        return vlyr

    def _pack_merge_params(self, enabled, tol, ang, simpl, iters, split, evenonly):
        """
        Baut das 6-Elemente-Array in der geforderten Reihenfolge.
        Wenn 'enabled' False ist, werden die Iterationen auf 0 gesetzt.
        """
        it = 0 if not enabled else int(iters)
        # Reihenfolge: tol, angle, simplify_factor, iterations, split_before, only_even_endpoints
        return [float(tol), float(ang), float(simpl), it, bool(split), bool(evenonly)]

    def _materialize(self, target_layer, expr_text, replace=True):
        _log_to_file(t("Materialisierung starten", "Materialize START"))
        #_dump_expr(expr_text)

        # Sichtbarkeit / Bearbeitbarkeit
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(target_layer.id())
        if node:
            node.setItemVisibilityChecked(False)
        target_layer.startEditing()
        if node:
            node.setItemVisibilityChecked(True)

        # Platzhalter-Feature sicherstellen
        prov = target_layer.dataProvider()
        if replace:
            ids = [f.id() for f in target_layer.getFeatures()]
            if ids:
                _log_to_file(t(f"Objekte löschen: {len(ids)}", f"deleteFeatures: {len(ids)}"))
                prov.deleteFeatures(ids)
        if target_layer.featureCount() == 0:
            _log_to_file(t("Platzhalter-Objekt hinzufügen", "add placeholder feature"))
            prov.addFeatures([QgsFeature()])

        feat = next(target_layer.getFeatures(), None)
        if feat is None:
            _log_to_file(t("Platzhalter fehlt, nochmals hinzufügen", "placeholder missing, add again"))
            prov.addFeatures([QgsFeature()])
            feat = next(target_layer.getFeatures(), None)

        # Ausdruck auswerten
        expr = QgsExpression(expr_text)
        if expr.hasParserError():
            perr = expr.parserErrorString()
            _log_to_file(t("Parserfehler: ", "parserError: ") + perr)
            _bar(self, "crit", t("Parserfehler im Ausdruck (Details im Log).", "Parser error in expression (see log for details)."))
            target_layer.rollBack()
            return False

        ctx = QgsExpressionContext()
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(target_layer))
        if feat is not None:
            ctx.setFeature(feat)

        _log_to_file(t("Ausdruck auswerten …", "evaluate expr() …"))
        _ = expr.evaluate(ctx)
        if expr.hasEvalError():
            e = expr.evalErrorString()
            _log_to_file(t("Auswertungsfehler: " + e, "evalError: " + e))
            _bar(self, "crit", t("Auswertung fehlgeschlagen (Details im Log).", "Evaluation failed (see log for details)."))
            target_layer.rollBack()
            return False

        # Commit
        _log_to_file(t("Änderungen schreiben()", "commitChanges()"))
        target_layer.commitChanges()
        _log_to_file(t("Materialisierung fertig", "Materialize DONE"))
        return True

    def run(self):
        _log_to_file(t("Plugin run() starten", "Plugin run() START"))
        d = LineDisplacementDialog(self.iface.mainWindow())

        # vorhandene Vektorebenen in die beiden Listen übernehmen (mit Geometrietyp-Filter)
        prj = QgsProject.instance()
        for lyr in prj.mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue

            gtype = QgsWkbTypes.geometryType(lyr.wkbType())

            # ---- Bleibende Geometrie: nur Linien ODER Flächen ----
            if gtype in (QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry):
                it_fixed = QtWidgets.QListWidgetItem(lyr.name())
                it_fixed.setFlags(it_fixed.flags() | QtCore.Qt.ItemIsUserCheckable)
                it_fixed.setCheckState(QtCore.Qt.Unchecked)
                d.fixed_list.addItem(it_fixed)

            # ---- Zu verdrängende Geometrie: nur Linien ----
            if gtype == QgsWkbTypes.LineGeometry:
                it_move = QtWidgets.QListWidgetItem(lyr.name())
                it_move.setFlags(it_move.flags() | QtCore.Qt.ItemIsUserCheckable)
                it_move.setCheckState(QtCore.Qt.Unchecked)
                d.move_list.addItem(it_move)

            # Ziel-Layer-Kombobox: nur Linienebenen
            if gtype == QgsWkbTypes.LineGeometry:
                d.cmb_existing.addItem(lyr.name())

        d.fixed_list.setToolTip(t("Es werden nur Linien- und Flächenebenen angeboten.", "Only line and polygon layers are offered."))
        d.move_list.setToolTip(t("Es werden nur Linienebenen angeboten.", "Only line layers are offered."))

        # --- Dynamische CRS-Vorbelegung für den Projektwähler (sichtbar im Dialog) ---
        crs_candidate = None

        # vorhandene Vektorebenen ins Auswahlfeld (ankreuzbar) übernehmen
        prj = QgsProject.instance()

        # 1) Alle Vektorebenen einsammeln und ALPHABETISCH sortieren
        all_vec = [lyr for lyr in prj.mapLayers().values() if isinstance(lyr, QgsVectorLayer)]
        all_vec.sort(key=lambda L: L.name().lower())

        # 2) Widgets leeren und mit Geometrietyp-Filtern befüllen
        d.fixed_list.clear()
        d.move_list.clear()
        d.cmb_existing.clear()

        for lyr in all_vec:
            gtype = QgsWkbTypes.geometryType(lyr.wkbType())

            # ---- „Bleibende Geometrie“: Linien ODER Flächen ----
            if gtype in (QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry):
                it1 = QtWidgets.QListWidgetItem(lyr.name())
                it1.setFlags(it1.flags() | QtCore.Qt.ItemIsUserCheckable)
                it1.setCheckState(QtCore.Qt.Unchecked)
                d.fixed_list.addItem(it1)

            # ---- „Zu verdrängende Geometrie“: NUR Linien ----
            if gtype == QgsWkbTypes.LineGeometry:
                it2 = QtWidgets.QListWidgetItem(lyr.name())
                it2.setFlags(it2.flags() | QtCore.Qt.ItemIsUserCheckable)
                it2.setCheckState(QtCore.Qt.Unchecked)
                d.move_list.addItem(it2)

                # Ziel-Ebene: ebenfalls nur Linien anbieten
                d.cmb_existing.addItem(lyr.name())

        # 3) CRS-Vorwahl im Widget setzen (sichtbar im Dialog):
        #    1) erste „zu verdrängende“ Linie, 2) erste „bleibende“ (Linie/Fläche), 3) Projekt-CRS
        crs_candidate = None
        try:
            if d.move_list.count() > 0:
                nm = d.move_list.item(0).text()
                mlst = prj.mapLayersByName(nm)
                if mlst and mlst[0].isValid():
                    crs_candidate = mlst[0].crs()

            if not crs_candidate or not crs_candidate.isValid():
                if d.fixed_list.count() > 0:
                    nm = d.fixed_list.item(0).text()
                    flst = prj.mapLayersByName(nm)
                    if flst and flst[0].isValid():
                        crs_candidate = flst[0].crs()

            if not crs_candidate or not crs_candidate.isValid():
                crs_candidate = prj.crs()

            if crs_candidate and crs_candidate.isValid():
                d.proj_selector.setCrs(crs_candidate)
        except Exception:
            # Sichtbare Vorbelegung ist „nice to have“ – nicht kritisch
            pass

        # Dialog anzeigen
        if not d.exec_():
            _log_to_file(t("Dialog abgebrochen", "Dialog cancelled"))
            return

        # Nutzerwahl zwischenspeichern
        leave_symbol = d.chk_leave_symbol.isChecked()

        # Logging-Schalter aus GUI setzen (für main & registrar)
        global MAIN_LOG_ENABLED
        MAIN_LOG_ENABLED = d.chk_log.isChecked()
        registrar.set_logging_enabled(MAIN_LOG_ENABLED)

        _log_to_file(t(f"Symbolebene hinterlassen (zwischengespeichert) = {leave_symbol}", f"leave_symbol (cached) = {leave_symbol}"))

        # Skripte registrieren/laden
        if leave_symbol:
            ok, msg = registrar.install_scripts_permanent(self.plugin_dir)
            _log_to_file(t("Skripte dauerhaft installieren: ", "install_scripts_permanent: ") + msg)
            _bar(self, "info", msg)
            # Scripts-Provider refreshen
            try:
                prov = QgsApplication.processingRegistry().providerById('script')
                if prov and hasattr(prov, 'refreshAlgorithms'):
                    prov.refreshAlgorithms()
                    _log_to_file(t("Processing-Provider 'script' refreshAlgorithms() aufgerufen.", "Processing provider 'script' refreshAlgorithms() called."))
                else:
                    import importlib
                    try:
                        from processing.script import ScriptUtils
                        importlib.reload(ScriptUtils)
                        ScriptUtils.reloadScripts()
                        _log_to_file(t("Processing ScriptUtils.reloadScripts() aufgerufen.", "Processing ScriptUtils.reloadScripts() called."))
                    except Exception as e2:
                        _log_to_file(t(f"Auffrischen der Skripte nicht möglich: {e2}", f"Could not refresh scripts: {e2}"))
            except Exception as e:
                _log_to_file(t(f"Fehler beim Auffrischen der Processing-Skripte: {e}", f"Error refreshing processing scripts: {e}"))
            ok2, msg2 = registrar.import_scripts_session(self.plugin_dir)
            _log_to_file(t("Skripte für aktuelle Sitzung importieren (nach Installation): ", "import_scripts_session (after install): ") + msg2)
            if not ok2:
                _bar(self, "warn", msg2)
        else:
            ok, msg = registrar.import_scripts_session(self.plugin_dir)
            _log_to_file(t("Skripte für aktuelle Sitzung importieren: ", "import_scripts_session: ") + msg)
            if not ok:
                _bar(self, "warn", msg)

        # Markierte Ebenen einsammeln
        def checked_names(list_widget):
            names = []
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                if it.checkState() == QtCore.Qt.Checked and not it.isHidden():
                    names.append(it.text())
            return names

        fixed_names = checked_names(d.fixed_list)
        move_names  = checked_names(d.move_list)

        if not fixed_names or not move_names:
            _bar(self, "warn", t("Bitte wählen Sie je mindestens eine Ebene aus.", "Please select at least one layer of each type."))
            _log_to_file(t(f"Namen der bleibenden Ebenen={fixed_names}, Namen der zu verdrängenden Ebenen={move_names}", f"fixed_names={fixed_names}, move_names={move_names}"))
            return

        # In Layer-Diktlisten umsetzen
        fixed = self._collect_layers_from_list(
            fixed_names,
            d.chk_fixed_selected.isChecked(),
            simplify=(d.spin_fixed_simplify.value() if d.chk_fixed_simplify.isChecked() else None),
            smooth_enabled=d.chk_fixed_smooth.isChecked(),
            smooth_offset=(d.spin_fixed_smooth_off.value() if d.chk_fixed_smooth.isChecked() else None),
            smooth_iter=(d.spin_fixed_smooth_iter.value()  if d.chk_fixed_smooth.isChecked() else None),
        )

        moving = self._collect_layers_from_list(
            move_names,
            d.chk_move_selected.isChecked(),
            simplify=(d.spin_move_simplify.value() if d.chk_move_simplify.isChecked() else None),
            smooth_enabled=d.chk_move_smooth.isChecked(),
            smooth_offset=(d.spin_move_smooth_off.value() if d.chk_move_smooth.isChecked() else None),
            smooth_iter=(d.spin_move_smooth_iter.value()  if d.chk_move_smooth.isChecked() else None),
        )

        # Ziel-CRS bestimmen
        target_crs = None
        if d.radio_new_temp.isChecked():
            try:
                sel = d.proj_selector.crs()
                if sel and sel.isValid():
                    target_crs = sel
            except Exception:
                target_crs = None

        auto_crs, crs_mixed = _pick_ref_crs(self, moving, fixed)
        if target_crs is None:
            target_crs = auto_crs

        # Zielebene erzeugen/verwenden
        if d.radio_new_temp.isChecked():
            tlyr = self._create_target_layer(t("verdrängte Geometrie", "displaced geometry"), crs=target_crs)
        else:
            name = d.cmb_existing.currentText().strip()
            lst = prj.mapLayersByName(name)
            if not lst:
                _bar(self, "warn", t("Ziel-Ebene nicht gefunden.", "Target layer not found."))
                _log_to_file(t("Ziel nicht gefunden: ", "target not found: ") + name)
                return
            tlyr = lst[0]
            target_crs = tlyr.crs()

        if crs_mixed and d.radio_new_temp.isChecked():  # falls Ihr Editor 'and' verlangt: and
            _bar(self, "warn",
                 t("Eingabee­benen haben unterschiedliche KBS. Geometrien werden automatisch in das KBS der Ziel-Ebene transformiert.", "Input layers have different CRS. Geometries will be transformed automatically to the CRS of the target layer."))

        # --- Netzverknüpfungs-Parameter einsammeln ---
        pre_params = self._pack_merge_params(
            enabled = d.chk_merge_pre.isChecked(),
            tol     = d.spin_tol_pre.value(),
            ang     = d.spin_ang_pre.value(),
            simpl   = d.spin_simpl_pre.value(),
            iters   = d.spin_it_pre.value(),
            split   = d.chk_split_pre.isChecked(),
            evenonly= d.chk_even_pre.isChecked()
        )

        fin_params = self._pack_merge_params(
            enabled = d.chk_merge_post.isChecked(),
            tol     = d.spin_tol_post.value(),
            ang     = d.spin_ang_post.value(),
            simpl   = d.spin_simpl_post.value(),
            iters   = d.spin_it_post.value(),
            split   = d.chk_split_post.isChecked(),
            evenonly= d.chk_even_post.isChecked()
        )

        # --- Debug-Schlüssel aus der ComboBox holen (Anzeigetext ≠ Wert) ---
        dbg_key = d.cmb_debug.currentData()
        if dbg_key is None:  # Fallback, falls UserData nicht gesetzt ist
            txt = d.cmb_debug.currentText()
            dbg_key = txt.split(" – ", 1)[0].strip()

        # --- Insert/Append-Option aus dem GUI an die Zielebene hängen ---
        tlyr.setCustomProperty("LineDisplacement/append_new", d.radio_append.isChecked())

        # Ausdruck bauen
        target_authid = target_crs.authid() if target_crs and target_crs.isValid() else None
        expr = build_line_displacement_call(
            to_move_layers=moving,
            fixed_layers=fixed,
            target_layer_name=tlyr.name(),
            buf_dist=d.spin_buf.value(),
            min_repl_len=d.spin_minlen.value(),
            pre_params=pre_params,      # unverändert
            fin_params=fin_params,      # unverändert
            debug_stage=dbg_key,        # << nur der Schlüssel (z. B. "pre_final")
            log_to_desktop=d.chk_log.isChecked(),
            target_authid=target_authid
        )
        _dump_expr(expr)

        # Geometriegenerator anhängen
        _add_geomgen_symbol(tlyr, expr)

        # Materialisieren
        ok2 = False
        try:
            replace = d.radio_replace.isChecked()
            ok2 = self._materialize(tlyr, expr, replace=replace)
        except Exception as e:
            _log_to_file(t(f"Fehler in _materialize(): {e}", f"_materialize() Exception: {e}"))
            ok2 = False

        if ok2:
            def _do_cleanup():
                try:
                    _cleanup_geomgen(tlyr, leave_symbol=leave_symbol)
                finally:
                    if leave_symbol:
                        _log_to_file(t("Skripte verbleiben (Geometriegenerator hinterlassen).", "Scripts remain (geometry generator left)."))
                    else:
                        ok_un, msg_un = registrar.uninstall_scripts(remove_expr=True, remove_proc=True)
                        _log_to_file(t("Deinstallation: ", "Uninstall: ") + msg_un)

            QtCore.QTimer.singleShot(500, _do_cleanup)
            _bar(self, "ok", t("Ausgabe erstellt.", "Output created."))
        else:
            _bar(self, "warn", t("Ausgabe fehlgeschlagen.", "Output failed."))
