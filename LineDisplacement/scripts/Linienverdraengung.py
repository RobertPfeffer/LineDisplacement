# -*- coding: utf-8 -*-
# Linienverdraengung.py – eigenständig nutzbar, mit zweisprachigem Logging

import os
import traceback
from datetime import datetime
from qgis.core import (
    QgsProject,
    QgsGeometry,
    QgsPointXY,
    QgsFeature,
)
from qgis.utils import qgsfunction
from qgis.PyQt.QtCore import QSettings  # für Sprachwahl

# ------------------------------
# Einfache Sprachhilfe (lokal!)
# ------------------------------
def t(de: str, en: str) -> str:
    """Gibt DE- oder EN-Text zurück, abhängig von der QGIS-Oberflächensprache."""
    try:
        lang = (QSettings().value('locale/userLocale', 'en') or 'en')[:2].lower()
    except Exception:
        lang = 'en'
    return de if lang == 'de' else en


# ------------------------------
# Logfile auf Desktop
# ------------------------------
LOGFILE = os.path.join(os.path.expanduser("~"), "Desktop", "line_displacement.log")
LOG_ENABLED = False   # Default: aus; Plugin schaltet per Funktionsparameter ein

def log_init():
    if not LOG_ENABLED:
        return
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(f"[line_displacement] {datetime.now():%Y-%m-%d %H:%M:%S}  "
                    f"{t('*** Neuer Lauf gestartet ***', '*** New run started ***')}\n")
    except Exception:
        pass

def log(msg: str):
    if not LOG_ENABLED:
        return
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(f"[line_displacement] {datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except Exception:
        pass


# ------------------------------
# Hilfen
# ------------------------------
def summarize_source(src):
    """Nur für Logs – technische Kurzangabe (neutral/englisch belassen)."""
    if isinstance(src, QgsGeometry):
        parts = "?"
        try:
            if src.isMultipart():
                parts = len(src.asMultiPolyline())
            else:
                parts = 1
        except Exception:
            pass
        length = None
        try:
            length = src.length()
        except Exception:
            pass
        if length is not None:
            return f"Geom(parts={parts}, len={length:.2f})"
        else:
            return f"Geom(parts={parts})"
    else:
        return str(src)


def substring_by_distance(geom, start_dist, end_dist, tol=1e-9):
    """Fallback für lineSubstring: extrahiert Abschnitt entlang einer einfachen Linie."""
    if geom.isMultipart():
        return QgsGeometry()
    pts = geom.asPolyline()
    if not pts:
        return QgsGeometry()
    seg_lens = [((pts[i+1].x() - pts[i].x())**2 + (pts[i+1].y() - pts[i].y())**2)**0.5 for i in range(len(pts)-1)]
    cum = [0.0]
    for L in seg_lens:
        cum.append(cum[-1] + L)
    total_len = cum[-1]
    d1 = max(0.0, min(start_dist, total_len))
    d2 = max(0.0, min(end_dist, total_len))
    if d2 < d1:
        d1, d2 = d2, d1

    def interp(d):
        for i, L in enumerate(seg_lens):
            if cum[i] <= d <= cum[i+1] + tol:
                if L < tol:
                    return pts[i]
                t_ = (d - cum[i]) / L
                x = pts[i].x() + t_ * (pts[i+1].x() - pts[i].x())
                y = pts[i].y() + t_ * (pts[i+1].y() - pts[i].y())
                return QgsPointXY(x, y)
        return pts[-1]

    p1 = interp(d1)
    p2 = interp(d2)
    segment_pts = [p1]
    for j in range(1, len(cum)-1):
        if cum[j] > d1 + tol and cum[j] < d2 - tol:
            segment_pts.append(pts[j])
    segment_pts.append(p2)
    return QgsGeometry.fromPolylineXY(segment_pts)


def split_boundary_at_endpoints(boundary_geom: QgsGeometry, line_geoms, tol=1e-9):
    """
    Zerschneidet boundary_geom dort, wo line_geoms mit ihren Start-/Endpunkten
    an die Kontur projizieren.
    """
    pts = []
    for g in line_geoms:
        if g.isMultipart():
            for part in g.asMultiPolyline():
                if len(part) >= 1:
                    pts.append(QgsPointXY(part[0]))
                    pts.append(QgsPointXY(part[-1]))
        else:
            line = g.asPolyline()
            if len(line) >= 1:
                pts.append(QgsPointXY(line[0]))
                pts.append(QgsPointXY(line[-1]))

    if boundary_geom.isMultipart():
        all_pts = []
        for part in boundary_geom.asMultiPolyline():
            all_pts += part
        flat_boundary = QgsGeometry.fromPolylineXY(all_pts)
    else:
        flat_boundary = boundary_geom

    total_len = flat_boundary.length()
    if total_len == 0:
        return [flat_boundary]

    dists = [0.0, total_len]
    for p in pts:
        d = flat_boundary.lineLocatePoint(QgsGeometry.fromPointXY(p))
        if d is None:
            continue
        d = max(0.0, min(d, total_len))
        dists.append(d)
    dists = sorted(set([round(d, 9) for d in dists]))

    segments = []
    for i in range(len(dists) - 1):
        d1, d2 = dists[i], dists[i+1]
        if d2 - d1 < tol:
            continue
        if hasattr(flat_boundary, "lineSubstring"):
            seg = flat_boundary.lineSubstring(d1, d2)
        else:
            seg = substring_by_distance(flat_boundary, d1, d2)
        if seg and not seg.isEmpty():
            segments.append(seg)
    if not segments:
        return [flat_boundary]
    return segments


# ------------------------------
# Helfer: Verknüpfen per Netzfragmente_verknuepfen
# ------------------------------
def _merge_by_direction(
        source_obj,
        project,
        logfunc,
        tol_value=None,        # TOLERANCE (float)
        angle_value=None,      # ANGLE_TOL (float, Grad)
        simplify_value=None,   # SIMPLIFY_TOL (float)
        max_iters=None,        # MAX_ITERS (int)
        split_at_nodes=None,   # SPLIT_AT_NODES (bool)
        even_only=None         # EVEN_ONLY (bool)
    ):
    """
    Führt 'Netzfragmente_verknuepfen' aus und gibt die vereinheitlichte
    Liniengeometrie (UnaryUnion) zurück, sonst None.
    """
    try:
        from qgis.core import (
            QgsVectorLayer, QgsFeature,
            QgsProcessingContext, QgsProcessingFeedback, QgsFeatureRequest
        )

        # Quelle bestimmen
        src_layer = None
        temp_layer = None

        if hasattr(source_obj, "getFeatures"):  # Layer
            src_layer = source_obj
        elif isinstance(source_obj, QgsGeometry):  # Geometrie -> temporärer Layer
            try:
                crs_authid = project.crs().authid() if project and project.crs().isValid() else "EPSG:3857"
            except Exception:
                crs_authid = "EPSG:3857"
            temp_layer = QgsVectorLayer(f"LineString?crs={crs_authid}", "tmp_merge", "memory")
            prov = temp_layer.dataProvider()
            temp_layer.updateFields()
            f = QgsFeature()
            f.setGeometry(source_obj)
            prov.addFeatures([f])
            src_layer = temp_layer
        else:
            try:
                cand = project.mapLayersByName(str(source_obj))
                if cand:
                    src_layer = cand[0]
            except Exception:
                pass

        if src_layer is None:
            logfunc(t("MergeByDirection: Keine gültige Quelle; übersprungen.",
                      "MergeByDirection: No valid source; skipped."))
            return None

        # Parameter (mit Defaults)
        try:
            TOLERANCE    = float(tol_value)      if tol_value      is not None else 0.01
        except Exception:
            TOLERANCE    = 0.01
        try:
            ANGLE_TOL    = float(angle_value)    if angle_value    is not None else 90.0
        except Exception:
            ANGLE_TOL    = 90.0
        try:
            SIMPLIFY_TOL = float(simplify_value) if simplify_value is not None else 0.3
        except Exception:
            SIMPLIFY_TOL = 0.3
        try:
            MAX_ITERS    = int(max_iters)        if max_iters      is not None else 0
        except Exception:
            MAX_ITERS    = 0
        if MAX_ITERS < 0:
            MAX_ITERS = 0
        if MAX_ITERS < 1:
            logfunc(t("MergeByDirection: MAX_ITERS < 1 → übersprungen.",
                      "MergeByDirection: MAX_ITERS < 1 → skipped."))
            return None

        SPLIT_AT_NODES = bool(split_at_nodes) if split_at_nodes is not None else False
        EVEN_ONLY      = bool(even_only)      if even_only      is not None else False

        common_params = {
            'INPUT': src_layer,
            'INPUTS': [],
            'FILTER': "",
            'TOLERANCE': TOLERANCE,
            'ANGLE_TOL': ANGLE_TOL,
            'SIMPLIFY_TOL': SIMPLIFY_TOL,
            'MAX_ITERS': MAX_ITERS,
            'SPLIT_AT_NODES': SPLIT_AT_NODES,
            'EVEN_ONLY': EVEN_ONLY,
            'DRY_RUN': False,
            'OUT_REST': False,
            'WRITE_LOG': False,
            'OUTPUT': 'memory:'
        }

        out_layer = None

        try:
            from Netzfragmente_verknuepfen import MergeLinesByDirection
            alg = MergeLinesByDirection()
            params = {
                alg.INPUT:           common_params['INPUT'],
                alg.INPUTS:          common_params['INPUTS'],
                alg.FILTER:          common_params['FILTER'],
                alg.TOLERANCE:       common_params['TOLERANCE'],
                alg.ANGLE_TOL:       common_params['ANGLE_TOL'],
                alg.SIMPLIFY_TOL:    common_params['SIMPLIFY_TOL'],
                alg.MAX_ITERS:       common_params['MAX_ITERS'],
                alg.SPLIT_AT_NODES:  common_params['SPLIT_AT_NODES'],
                alg.EVEN_ONLY:       common_params['EVEN_ONLY'],
                alg.DRY_RUN:         common_params['DRY_RUN'],
                alg.OUT_REST:        common_params['OUT_REST'],
                alg.WRITE_LOG:       common_params['WRITE_LOG'],
                alg.OUTPUT:          common_params['OUTPUT'],
            }
            ctx = QgsProcessingContext()
            fdb = QgsProcessingFeedback()
            results = alg.processAlgorithm(params, ctx, fdb)
            dest_id = results.get(alg.OUTPUT)
            if dest_id:
                out_layer = ctx.getMapLayer(dest_id)
        except Exception as e1:
            # Rückfall: Processing-Algorithmus
            try:
                from qgis import processing
                res = processing.run("script:merge_lines_by_direction", common_params)
                out_layer = res.get('OUTPUT')
            except Exception as e2:
                logfunc(t(f"MergeByDirection nicht verfügbar (Klasse/Processing): {e1} / {e2}",
                          f"MergeByDirection not available (class/processing): {e1} / {e2}"))
                out_layer = None

        if out_layer is None:
            return None

        geoms = []
        for feat in out_layer.getFeatures(QgsFeatureRequest()):
            g = feat.geometry()
            if g and not g.isEmpty():
                geoms.append(g)
        if not geoms:
            return None
        return QgsGeometry.unaryUnion(geoms)

    except Exception as e:
        logfunc(t(f"MergeByDirection fehlgeschlagen: {e}",
                  f"MergeByDirection failed: {e}"))
        return None


@qgsfunction(
    args="auto",
    group=t("Kartografie", "Cartography"),  # Anzeigegruppe im Funktionseditor
    register=True
)
def line_displacement(
    to_move_src,            # 1
    fixed_src,              # 2
    target_layer_name,      # 3
    buf_dist,               # 4
    min_repl_len,           # 5
    pre_params,             # 6
    final_params,           # 7
    debug_stage,            # 8
    log_to_desktop,         # 9
    feature, parent
):
    global LOG_ENABLED
    LOG_ENABLED = bool(log_to_desktop)

    """
    Verdrängt eine zu verschiebende (to_move) Liniengeometrie von einer bleibenden (fixed) Geometrie.
    """

    try:
        # Projekt & Debug
        project = QgsProject.instance()
        dbg = str(debug_stage) if debug_stage is not None else ""

        # Zielebene holen – Sichtbarkeit immer erforderlich; Editierbarkeit nur für finalen Modus
        target_layers = project.mapLayersByName(target_layer_name)
        if not target_layers:
            return QgsGeometry()
        target_layer = target_layers[0]
        node = project.layerTreeRoot().findLayer(target_layer.id())
        if node is None or not node.isVisible():
            return QgsGeometry()
        # Editierbarkeit nur verlangen, wenn NICHT im 'pre_final'-Debugmodus
        if dbg != "pre_final" and not target_layer.isEditable():
            return QgsGeometry()

        # Log neu starten
        log_init()
        log(t(
            f"--- line_displacement: weichend={summarize_source(to_move_src)}, bleibend={summarize_source(fixed_src)}, "
            f"buf={buf_dist}, min_repl_len={min_repl_len}, ziel='{target_layer_name}', debug='{dbg}' ---",
            f"--- line_displacement: to_move={summarize_source(to_move_src)}, fixed={summarize_source(fixed_src)}, "
            f"buf={buf_dist}, min_repl_len={min_repl_len}, target='{target_layer_name}', debug='{dbg}' ---"
        ))

        # 0) Parameterbündel auslesen
        def _read_params(seq_any):
            t0=a=s=i=sn=eo=None
            try:
                seq=list(seq_any) if seq_any is not None else []
            except Exception:
                seq=[]
            if len(seq)>=1: t0 = seq[0]
            if len(seq)>=2: a  = seq[1]
            if len(seq)>=3: s  = seq[2]
            if len(seq)>=4: i  = seq[3]
            if len(seq)>=5: sn = seq[4]
            if len(seq)>=6: eo = seq[5]
            return t0,a,s,i,sn,eo

        (pre_TOL, pre_ANG, pre_SIMPL, pre_MAX, pre_SPLIT, pre_EVEN) = _read_params(pre_params)
        (fin_TOL, fin_ANG, fin_SIMPL, fin_MAX, fin_SPLIT, fin_EVEN) = _read_params(final_params)

        # 1) zu verdrängende Quellgeometrie (Vorverknüpfen → Union)
        if isinstance(to_move_src, QgsGeometry):
            pre = _merge_by_direction(
                to_move_src, project, log,
                tol_value=pre_TOL, angle_value=pre_ANG, simplify_value=pre_SIMPL,
                max_iters=pre_MAX, split_at_nodes=pre_SPLIT, even_only=pre_EVEN
            )
            if pre is not None and not pre.isEmpty():
                union_to_move = pre
                log(t("Weichende Geometrie vorverknüpft; vereinheitlichte Geometrie übernommen.",
                      "To-move geometry pre-merged; unified geometry adopted."))
            else:
                union_to_move = to_move_src
                log(t("Vorverknüpfung übersprungen/fehlgeschlagen – nutze Original-Geometrie.",
                      "Pre-merge skipped/failed – using original geometry."))
        else:
            move_layers = project.mapLayersByName(str(to_move_src))
            if not move_layers:
                log(t(f"Weichender Layer '{to_move_src}' nicht gefunden.",
                      f"To-move layer '{to_move_src}' not found."))
                return QgsGeometry()
            move_layer = move_layers[0]
            pre = _merge_by_direction(
                move_layer, project, log,
                tol_value=pre_TOL, angle_value=pre_ANG, simplify_value=pre_SIMPL,
                max_iters=pre_MAX, split_at_nodes=pre_SPLIT, even_only=pre_EVEN
            )
            if pre is not None and not pre.isEmpty():
                union_to_move = pre
                log(t("Weichender Layer vorverknüpft; vereinheitlichte Geometrie übernommen.",
                      "To-move layer pre-merged; unified geometry adopted."))
            else:
                move_geoms = [f.geometry() for f in move_layer.getFeatures() if f.geometry() and not f.geometry().isEmpty()]
                if not move_geoms:
                    log(t("Keine weichenden Geometrien.", "No to-move geometries."))
                    return QgsGeometry()
                union_to_move = QgsGeometry.unaryUnion(move_geoms)
                if union_to_move is None or union_to_move.isEmpty():
                    log(t("Vereinigte weichende Geometrie leer.",
                          "Unified to-move geometry is empty."))
                    return QgsGeometry()
                log(t("Weichende Geometrie vereinigt (ohne Vorverknüpfung).",
                      "To-move geometry unified (no pre-merge)."))
        if dbg == "union_to_move":
            return union_to_move

        # 2) bleibende Quellgeometrie (Union)
        if isinstance(fixed_src, QgsGeometry):
            union_fixed = fixed_src
            log(t("Bleibende Geometrie direkt übergeben.", "Fixed geometry passed directly."))
        else:
            fixed_layers = project.mapLayersByName(str(fixed_src))
            if not fixed_layers:
                log(t(f"Bleibender Layer '{fixed_src}' nicht gefunden.",
                      f"Fixed layer '{fixed_src}' not found."))
                return QgsGeometry()
            fixed_layer = fixed_layers[0]
            fixed_geoms = [f.geometry() for f in fixed_layer.getFeatures() if f.geometry() and not f.geometry().isEmpty()]
            if not fixed_geoms:
                log(t("Keine bleibenden Geometrien.", "No fixed geometries."))
                return QgsGeometry()
            union_fixed = QgsGeometry.unaryUnion(fixed_geoms)
            if union_fixed is None or union_fixed.isEmpty():
                log(t("Vereinigte bleibende Geometrie leer.", "Unified fixed geometry is empty."))
                return QgsGeometry()
            log(t("Bleibende Geometrie vereinigt.", "Fixed geometry unified."))
        if dbg == "union_fixed":
            return union_fixed

        # 3) Pufferfläche um bleibend
        fixed_buffer_poly = union_fixed.buffer(buf_dist, 1)  # 1 Segment pro Viertelkreis
        if fixed_buffer_poly is None or fixed_buffer_poly.isEmpty():
            log(t("Pufferfläche (bleibend) leer.", "Buffer polygon (fixed) is empty."))
            return QgsGeometry()
        log(t("Pufferfläche (bleibend) erstellt.", "Buffer polygon (fixed) created."))
        if dbg == "fixed_buffer_poly":
            return fixed_buffer_poly

        # 4) Pufferkontur
        if hasattr(fixed_buffer_poly, "boundary"):
            fixed_boundary = fixed_buffer_poly.boundary()
        else:
            lines = []
            if fixed_buffer_poly.isMultipart():
                for poly in fixed_buffer_poly.asMultiPolygon():
                    lines.append(QgsGeometry.fromPolylineXY(poly[0]))
            else:
                lines.append(QgsGeometry.fromPolylineXY(fixed_buffer_poly.asPolygon()[0]))
            fixed_boundary = QgsGeometry.collectGeometry(lines)

        # 5) Pufferkontur vereinfachen und Dubletten entfernen
        try:
            simp_tol = abs(float(buf_dist)) * 0.20  # 20 % des Pufferradius
        except Exception:
            simp_tol = 0.0
        if simp_tol <= 0.0:
            simp_tol = 1e-9
        try:
            fb_simpl = fixed_boundary.simplify(simp_tol)
            if fb_simpl and not fb_simpl.isEmpty():
                fixed_boundary = fb_simpl
        except Exception:
            pass
        try:
            eps = max(simp_tol * 0.5, 1e-12)
            rb = fixed_boundary.removeDuplicateNodes(eps)
            if rb and not rb.isEmpty():
                fixed_boundary = rb
        except Exception:
            pass

        log(t("Pufferkontur extrahiert.", "Buffer boundary extracted."))
        if dbg == "fixed_boundary":
            return fixed_boundary

        # 6) weichende Anteile im Puffer
        to_move_in_buffer = union_to_move.intersection(fixed_buffer_poly)
        if to_move_in_buffer is None or to_move_in_buffer.isEmpty():
            log(t("Keine weichenden Anteile im Puffer; Rest zurückgeben.",
                  "No to-move parts inside the buffer; returning the rest."))
            rest_only = union_to_move.difference(fixed_buffer_poly)
            if dbg in ("to_move_in_buffer", "rest"):
                return rest_only
            return rest_only
        log(t("Weichende Anteile im Puffer extrahiert.",
              "To-move parts inside buffer extracted."))
        if dbg == "to_move_in_buffer":
            return to_move_in_buffer

        # 7) Teilstücke im Puffer (Liste) + Union (nur für Debug)
        loops_list = []
        if to_move_in_buffer.isMultipart():
            for part in to_move_in_buffer.asMultiPolyline():
                if len(part) >= 2:
                    loops_list.append(QgsGeometry.fromPolylineXY(part))
        else:
            part = to_move_in_buffer.asPolyline()
            if len(part) >= 2:
                loops_list.append(QgsGeometry.fromPolylineXY(part))
        if not loops_list:
            log(t("Keine Linien-Teilstücke im Puffer; Rest zurückgeben.",
                  "No line segments inside the buffer; returning the rest."))
            rest_only = union_to_move.difference(fixed_buffer_poly)
            if dbg == "rest":
                return rest_only
            return rest_only

        if dbg == "loops":
            return QgsGeometry.collectGeometry(loops_list) if loops_list else QgsGeometry()

        loops_union = QgsGeometry.unaryUnion(loops_list)
        if dbg == "loops_union":
            return loops_union if loops_union else QgsGeometry()

        # 8) je Teilstück direkt puffern und in Blasen zerlegen (keine Verbundbildung)
        buffer_blobs = []
        for g in loops_list:
            comp_buf = g.buffer(buf_dist * 2.2, 2)
            if comp_buf is None or comp_buf.isEmpty():
                continue
            if comp_buf.isMultipart():
                for poly in comp_buf.asMultiPolygon():
                    buffer_blobs.append(QgsGeometry.fromPolygonXY(poly))
            else:
                buffer_blobs.append(comp_buf)

        log(t(f"{len(buffer_blobs)} Puffer-Blasen (segmentweise) extrahiert.",
              f"{len(buffer_blobs)} buffer blobs (per segment) extracted."))
        if dbg == "loops_buffer":
            return QgsGeometry.collectGeometry(buffer_blobs) if buffer_blobs else QgsGeometry()
        if dbg == "buffer_blobs":
            return QgsGeometry.collectGeometry(buffer_blobs) if buffer_blobs else QgsGeometry()

        # 9) Pufferkontur an Endpunkten zerschneiden
        boundary_segments = split_boundary_at_endpoints(fixed_boundary, loops_list)
        log(t(f"Pufferkontur in {len(boundary_segments)} Segmente zerteilt (an projizierten Endpunkten).",
              f"Buffer boundary split into {len(boundary_segments)} segments (at projected endpoints)."))
        if dbg == "boundary_segments":
            return QgsGeometry.collectGeometry(boundary_segments) if boundary_segments else QgsGeometry()

        # 10) Ersatzsegmente: vollständig innerhalb einer Blase + Mindestlänge
        candidate_segments = []
        for seg in boundary_segments:
            if any(blob.contains(seg) for blob in buffer_blobs):
                candidate_segments.append(seg)

        try:
            min_len = float(min_repl_len)
        except Exception:
            min_len = 0.0
        if min_len < 0:
            min_len = 0.0

        replacement_segments = []
        rejected_replacements = []
        for seg in candidate_segments:
            try:
                L = seg.length()
            except Exception:
                L = None
            if L is not None and L >= min_len:
                replacement_segments.append(seg)
            else:
                rejected_replacements.append(seg)

        log(t(f"{len(replacement_segments)} Ersatzsegmente ≥ {min_len:.4f}; "
              f"{len(rejected_replacements)} verworfen (zu kurz).",
              f"{len(replacement_segments)} replacement segments ≥ {min_len:.4f}; "
              f"{len(rejected_replacements)} rejected (too short)."))
        if dbg == "replacement_segments":
            return QgsGeometry.collectGeometry(replacement_segments) if replacement_segments else QgsGeometry()
        if dbg == "rejected_replacements":
            return QgsGeometry.collectGeometry(rejected_replacements) if rejected_replacements else QgsGeometry()

        # 11) genutzte / verwaiste Blasen
        used_blobs = [blob for blob in buffer_blobs if any(blob.contains(seg) for seg in replacement_segments)]
        unused_blobs = [blob for blob in buffer_blobs if blob not in used_blobs]
        log(t(f"{len(used_blobs)} genutzte Blasen, {len(unused_blobs)} verwaiste Blasen.",
              f"{len(used_blobs)} used blobs, {len(unused_blobs)} orphaned blobs."))
        if dbg == "used_blobs":
            return QgsGeometry.collectGeometry(used_blobs) if used_blobs else QgsGeometry()
        if dbg == "unused_blobs":
            return QgsGeometry.collectGeometry(unused_blobs) if unused_blobs else QgsGeometry()

        # 12) alle weichenden Segmente im Puffer (für Durchgänger-Prüfung)
        segments_to_move = []
        if to_move_in_buffer.isMultipart():
            for part in to_move_in_buffer.asMultiPolyline():
                segments_to_move.append(QgsGeometry.fromPolylineXY(part))
        else:
            segments_to_move.append(QgsGeometry.fromPolylineXY(to_move_in_buffer.asPolyline()))
        log(t(f"{len(segments_to_move)} weichende Teilstücke im Puffer.",
              f"{len(segments_to_move)} to-move segments inside the buffer."))
        if dbg == "segments_to_move":
            return QgsGeometry.collectGeometry(segments_to_move) if segments_to_move else QgsGeometry()

        # 13) Durchgänger = Segmente in verwaisten Blasen
        crossers = []
        for seg in segments_to_move:
            if any(blob.contains(seg) for blob in unused_blobs):
                crossers.append(seg)
        log(t(f"{len(crossers)} Querungs-Segmente (Durchgänger) erkannt.",
              f"{len(crossers)} crossing segments detected."))
        if dbg == "crossers":
            return QgsGeometry.collectGeometry(crossers) if crossers else QgsGeometry()

        # 14) Rest ohne Puffer
        rest = union_to_move.difference(fixed_buffer_poly)
        if dbg == "rest":
            return rest

        # 15) Vorfinale Geometrie sammeln (Rest + Ersatz + Durchgänger)
        pre_final_geom = QgsGeometry.collectGeometry([rest] + replacement_segments + crossers)
        log(t("Vorfinale Geometrie zusammengesetzt (Rest + Ersatz + Durchgänger).",
              "Pre-final geometry assembled (rest + replacement + crossers)."))

        # 16) Schlussverknüpfung per Netzfragmente_verknuepfen
        final_merged = _merge_by_direction(
            pre_final_geom, project, log,
            tol_value=fin_TOL, angle_value=fin_ANG, simplify_value=fin_SIMPL,
            max_iters=fin_MAX, split_at_nodes=fin_SPLIT, even_only=fin_EVEN
        )
        final_geom = final_merged if (final_merged and not final_merged.isEmpty()) else pre_final_geom

        # Debug: pre_final gibt die gesammelte Geometrie zurück, ohne zu schreiben
        if dbg == "pre_final":
            return pre_final_geom

        # 17) Schreiben (nur im finalen Modus)
        if dbg in ("", "final"):
            # ------------------- GUI-Option ermitteln -------------------
            append_new = False  # Default (Standalone/ohne GUI): ersetzen
            try:
                # 1) bevorzugt: Ebene trägt die Entscheidung
                val = target_layer.customProperty("LineDisplacement/append_new", None)
                if val is None:
                    # 2) Fallback: QSettings (falls das Plugin das hier abgelegt hat)
                    try:
                        from qgis.PyQt.QtCore import QSettings
                        val = QSettings().value("LineDisplacement/append_new", "false")
                    except Exception:
                        val = "false"
                append_new = str(val).lower() in ("1", "true", "yes", "on")
            except Exception:
                append_new = False

            # ------------------- Schreiben entsprechend Wahl -------------------
            if append_new:
                # Immer neues Feature anhängen
                new_feat = QgsFeature(target_layer.fields())
                new_feat.setGeometry(final_geom)
                success, added = target_layer.dataProvider().addFeatures([new_feat])
                if success:
                    log(t(f"Neues Feature angehängt, ID(s): {[f.id() for f in added]}.",
                          f"New feature appended, ID(s): {[f.id() for f in added]}."))
                else:
                    log(t("Fehler beim Anhängen eines neuen Features.",
                          "Error appending a new feature."))
            else:
                # Bisheriges Verhalten: erstes Feature überschreiben, sonst neu anlegen
                feats = list(target_layer.getFeatures())
                if feats:
                    target_id = feats[0].id()
                    target_layer.dataProvider().changeGeometryValues({target_id: final_geom})
                    log(t(f"Ursprüngliche Geometrie (ID {target_id}) überschrieben.",
                          f"Original geometry (ID {target_id}) overwritten."))
                else:
                    new_feat = QgsFeature(target_layer.fields())
                    new_feat.setGeometry(final_geom)
                    success, added = target_layer.dataProvider().addFeatures([new_feat])
                    if success:
                        log(t(f"Neues Feature angelegt, ID(s): {[f.id() for f in added]}.",
                              f"New feature created, ID(s): {[f.id() for f in added]}."))
                    else:
                        log(t("Fehler beim Anlegen eines neuen Features.",
                              "Error creating a new feature."))

        return final_geom

    except Exception as e:
        log(t(f"Fehler in line_displacement: {e}",
              f"Error in line_displacement: {e}"))
        log(traceback.format_exc())
        return QgsGeometry()
