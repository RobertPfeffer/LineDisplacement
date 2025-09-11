# -*- coding: utf-8 -*-
# 1) Standardbibliothek
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 2) QGIS / PyQt
from qgis.PyQt.QtCore import QVariant
from qgis.core import (   # Ihre strukturierte Liste von oben
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExpression,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFeatureRequest,
    QgsProcessingException,
    QgsSettings,
)

# 3) QGIS utils (UI-spezifisch)
from qgis.utils import iface

class MergeLinesByDirection(QgsProcessingAlgorithm):
    """
    siehe unten bei shortHelpString
    see below at shortHelpString
    """

    # ---------------- Sprachhilfen / Language helpers ----------------
    # Einfache Umschaltung anhand der QGIS-Locale. / Simple switch based on QGIS locale.
    @staticmethod
    def _lang():
        try:
            loc = QgsSettings().value("locale/userLocale", "en")
            return str(loc)[:2].lower()
        except Exception:
            return "en"

    def _t(self, de: str, en: str) -> str:
        return de if self._lang() == "de" else en

    # ---------------- Parameter-Schlüssel / Parameter keys ----------------
    INPUT = 'INPUT'
    SELECTED_ONLY = 'SELECTED_ONLY'
    FILTER = 'FILTER'
    TOLERANCE = 'TOLERANCE'
    ANGLE_TOL = 'ANGLE_TOL'
    EVEN_ONLY = 'EVEN_ONLY'
    SIMPLIFY_TOL = 'SIMPLIFY_TOL'
    MAX_ITERS = 'MAX_ITERS'
    OUTPUT = 'OUTPUT'
    SPLIT_AT_NODES = 'SPLIT_AT_NODES'
    OUT_REST = 'OUT_REST'
    DRY_RUN = 'DRY_RUN'
    WRITE_LOG = 'WRITE_LOG'
    PRUNE_SHORT = 'PRUNE_SHORT'

    # ---------------- Metadaten / Metadata ----------------
    def name(self):
        return 'merge_lines_by_direction'

    def displayName(self):
        return self._t(
            'Netzfragmente geradeaus verknüpfen (iterativ)',
            'Connect net fragments straight ahead (iterative)'
        )

    def group(self):
        return self._t('Netz-Bereinigung', 'Network cleaning')

    def groupId(self):
        return 'netz_bereinigung'

    def shortHelpString(self):
        return self._t(
            "Verknüpft fragmentierte Linien eines Netzes so, dass möglichst viele geradlinig durchgehende Linien entstehen. Z.B. werden an mehrfingrigen Kreuzungen eines Straßennetzes bevorzugt diejenigen Straßen verbunden, die am geradesten durchgehen. Das geschieht in mehreren Durchläufen.\nDer Prüfung kann eine generalisierte Betrachtung zu Grunde gelegt werden, die die Linien zunächst vereinfacht. Diese Vereinfachung ist temporär und wird nicht geschrieben.",
            "Connects fragmented lines of a network so that as many straight lines as possible are created. For example, at multi-finger crossings in a road network, preference is given to linking those roads that run in the most straight line. This is done in several iterations.\nThe analysis can be based on a generalised view that initially simplifies the lines. This simplification is temporary and will not be saved."
        )

    def createInstance(self):
        return MergeLinesByDirection()

    # ---------------- Bedienoberfläche / GUI definition ----------------
    def initAlgorithm(self, config=None):
        # Aktive Linienebene als Default ermitteln (falls vorhanden)
        default_layer = None
        try:
            al = iface.activeLayer()
            if isinstance(al, QgsVectorLayer) and al.geometryType() == QgsWkbTypes.LineGeometry:
                default_layer = al
        except Exception:
            pass

        p_in = QgsProcessingParameterVectorLayer(
            self.INPUT,
            self._t('Linien-Ebene', 'Line layer'),
            [QgsProcessing.TypeVectorLine],
            optional=True,
            defaultValue=default_layer   # ← hier die Vorauswahl
        )
        self.addParameter(p_in)

        self.addParameter(QgsProcessingParameterBoolean(
            self.SELECTED_ONLY,
            self._t('Nur gewählte Objekte', 'Only selected features'),
            defaultValue=False
        ))

        self.addParameter(QgsProcessingParameterExpression(
            self.FILTER,
            self._t('Ausdrucksfilter', 'Expression filter'),
            parentLayerParameterName=self.INPUT,
            optional=True
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCE,
            self._t('Toleranz gegenüber Löchern (Karteneinheiten)',
                    'Tolerance for holes (map units)'),
            QgsProcessingParameterNumber.Double,
            defaultValue=0.01, minValue=0.0
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.ANGLE_TOL,
            self._t('Maximale Winkelabweichung von der Gegenrichtung (Grad)',
                    'Maximum angle deviation from opposite direction (degrees)'),
            QgsProcessingParameterNumber.Double,
            defaultValue=90.0, minValue=0.0, maxValue=180.0
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SIMPLIFY_TOL,
            self._t('Temporäre Vereinfachung vor Betrachtung der Richtung (Karteneinheiten)',
                    'Temporary simplification before assessment of direction (map units)'),
            QgsProcessingParameterNumber.Double,
            defaultValue=0.3, minValue=0.0
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_ITERS,
            self._t('Maximale Iterationen', 'Maximum iterations'),
            QgsProcessingParameterNumber.Integer,
            defaultValue=50, minValue=1
        ))

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SPLIT_AT_NODES,
                self._t(
                    "Vorab an Kreuzungen zerlegen (um sie dann ordentlich neu zu verknüpfen)",
                    "Initially split at intersections (in order to reconnect them properly)"
                ),
                defaultValue=False
            )
        )
#        self.addParameter(QgsProcessingParameterBoolean(
#            self.PRUNE_SHORT,
#            self._t('Sehr kurze Segmente nach der Zerlegung entfernen (< Toleranz)',
#                    'Remove very short segments after split (< tolerance)'),
#            defaultValue=True
#        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.EVEN_ONLY,
            self._t('Nicht verknüpfen bei Zusammentreffen ungerader Anzahl Linien',
                    'Don’t connect where uneven numbers of lines meet'),
            defaultValue=False
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.DRY_RUN,
            self._t('Probelauf ohne Änderung', 'Dry run without changes'),
            defaultValue=False
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.OUT_REST,
            self._t('Ebene mit unverknüpften Restpunkten ausgeben', 'Output layer with unconnected, leftover endpoints '),
            defaultValue=False
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.WRITE_LOG,
            self._t('Logdatei auf Desktop schreiben', 'Write log file to desktop'),
            defaultValue=False
        ))
        
        self.addParameter(QgsProcessingParameterEnum(
            'DEBUG_STAGE',
            self._t('Debug-Stufe (früher abbrechen)',
                    'Debug stage (stop early)'),
            options=[
                self._t('0 – kein Debug (vollständig laufen lassen)', '0 – no debug (full run)'),
                self._t('1 – nur Eingabegeometrien schreiben', '1 – write only input geometries'),
                self._t('2 – nach Netzzerlegung ausgeben', '2 – after network splitting'),
                self._t('3 – nach erstem Iterationsschritt ausgeben', '3 – after first iteration'),
            ],
            defaultValue=0
        ))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            self._t('Verknüpftes Netz', 'Connected Network'),
            type=QgsProcessing.TypeVectorLine,
            createByDefault=True
        ))

    # ---------------- Hilfsfunktionen / Helper functions ----------------
    @staticmethod
    def _as_lines(geom: QgsGeometry):
        if geom is None or geom.isEmpty():
            return []
        if geom.isMultipart():
            return [list(pl) for pl in geom.asMultiPolyline()]
        else:
            pl = geom.asPolyline()
            return [list(pl)] if pl else []

    @staticmethod
    def _simplify_points(points, tol):
        """Temporäre Vereinfachung für die Richtungsbestimmung (Original bleibt unberührt).
        Temporary simplification for direction checking (original remains unchanged)."""
        if tol <= 0.0 or len(points) < 3:
            return list(points)
        try:
            g = QgsGeometry.fromPolylineXY(points).simplify(tol)
            pl = g.asPolyline()
            if pl and len(pl) >= 2:
                return list(pl)
        except Exception:
            pass
        return list(points)

    @staticmethod
    def _angle_deg(p_from: QgsPointXY, p_to: QgsPointXY) -> float:
        dx = p_to.x() - p_from.x()
        dy = p_to.y() - p_from.y()
        ang = math.degrees(math.atan2(dy, dx))
        return (ang + 360.0) % 360.0

    @staticmethod
    def _end_angle(points, endflag: str) -> float:
        """Winkel am Ende; für 'start' zeigt der Vektor vom zweiten Punkt zum ersten.
        End-angle; for 'start' the vector points from second to first point."""
        if len(points) < 2:
            return 0.0
        if endflag == 'start':
            return MergeLinesByDirection._angle_deg(points[1], points[0])
        else:
            return MergeLinesByDirection._angle_deg(points[-2], points[-1])

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        d = abs(a - b) % 360.0
        return d if d <= 180.0 else 360.0 - d

    @staticmethod
    def _connect_lines(a_pts, a_end, b_pts, b_end):
        # auf 'A_end' an 'B_start' normalisieren / normalize to 'A_end' to 'B_start'
        if a_end == 'start':
            a_pts = list(reversed(a_pts))
            a_end = 'end'
        if b_end == 'end':
            b_pts = list(reversed(b_pts))
            b_end = 'start'
        if not a_pts or not b_pts:
            return a_pts or b_pts
        if a_pts[-1] == b_pts[0]:
            return a_pts + b_pts[1:]
        else:
            return a_pts + b_pts

    @staticmethod
    def _dedupe_consecutive(points, eps):
        if not points:
            return points
        out = [points[0]]
        for p in points[1:]:
            if (abs(p.x() - out[-1].x()) > eps) or (abs(p.y() - out[-1].y()) > eps):
                out.append(p)
        if len(out) == 1:
            out.append(QgsPointXY(out[0].x(), out[0].y()))
        return out

    @staticmethod
    def _polyline_length(points):
        if not points or len(points) < 2:
            return 0.0
        s = 0.0
        for i in range(1, len(points)):
            dx = points[i].x() - points[i - 1].x()
            dy = points[i].y() - points[i - 1].y()
            s += math.hypot(dx, dy)
        return s

    def _cluster_points_hash(self, items, tol):
        """
        Cluster Punkte deterministisch nach Koordinaten (mit Toleranz).
        items: Liste von Dicts mit 'pt' (QgsPointXY).
        """
        clusters = defaultdict(list)
        if tol and tol > 0:
            for v in items:
                p = v['pt']
                kx = int(round(p.x() / tol))
                ky = int(round(p.y() / tol))
                clusters[(kx, ky)].append(v)
        else:
            ROUND = 12
            for v in items:
                p = v['pt']
                kx = round(p.x(), ROUND)
                ky = round(p.y(), ROUND)
                clusters[(kx, ky)].append(v)
        return clusters

    def _cluster_endpoints(self, stubs, tol):
        """
        Radiusbasiertes Clustering (setzt in-place s['cluster']).
        Wird in der Iteration und für den Restpunkte-Layer genutzt.
        """
        if not stubs:
            return
        # Ohne/mit sehr kleiner Toleranz: jeder Punkt eigener Cluster
        if tol <= 0:
            for i, s in enumerate(stubs):
                s['cluster'] = i
            return

        from math import floor, hypot
        # Gitterzellen -> Liste von Cluster-IDs
        grid = defaultdict(list)   # (ix,iy) -> [cluster_ids]
        clusters = {}              # cluster_id -> {'sumx','sumy','n'}
        next_id = 0

        def cell_key(p):
            return (int(floor(p.x() / tol)), int(floor(p.y() / tol)))

        def neighbors(k):
            x, y = k
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    yield (x + dx, y + dy)

        for s in stubs:
            p = s['pt']
            ck = cell_key(p)

            # Kandidaten-Cluster in 3×3 Nachbarschaft prüfen
            candidate_ids = []
            for nk in neighbors(ck):
                candidate_ids.extend(grid.get(nk, []))

            best = None
            bestdist = None
            for cid in candidate_ids:
                c = clusters[cid]
                cx = c['sumx'] / c['n']
                cy = c['sumy'] / c['n']
                d = hypot(p.x() - cx, p.y() - cy)
                if d <= tol and (best is None or d < bestdist):
                    best = cid
                    bestdist = d

            if best is None:
                cid = next_id
                next_id += 1
                clusters[cid] = {'sumx': p.x(), 'sumy': p.y(), 'n': 1}
                grid[ck].append(cid)
                s['cluster'] = cid
            else:
                c = clusters[best]
                c['sumx'] += p.x()
                c['sumy'] += p.y()
                c['n'] += 1
                s['cluster'] = best

    # ---------------- Protokoll / Logging helpers ----------------
    @staticmethod
    def _desktop_dir():
        home = Path.home()
        kandidaten = [
            home / "Desktop",
            Path(os.environ.get('USERPROFILE', '')) / "Desktop",
        ]
        xdg = os.environ.get('XDG_DESKTOP_DIR')
        if xdg:
            kandidaten.append(Path(xdg.replace("$HOME", str(home))))
        for p in kandidaten:
            if p and p.exists() and p.is_dir():
                return p
        if home.exists():
            return home
        return Path(tempfile.gettempdir())

    # ---------------- Hauptablauf / Main processing ----------------
    def processAlgorithm(self, parameters, context, feedback):
        # Parameter einlesen / Read parameters
        src = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        selected_only = self.parameterAsBoolean(parameters, self.SELECTED_ONLY, context)
        expr = self.parameterAsExpression(parameters, self.FILTER, context)
        tol = self.parameterAsDouble(parameters, self.TOLERANCE, context)
        ang_tol = self.parameterAsDouble(parameters, self.ANGLE_TOL, context)
        simp_tol = self.parameterAsDouble(parameters, self.SIMPLIFY_TOL, context)
        max_iters = self.parameterAsInt(parameters, self.MAX_ITERS, context)
        split_at_nodes = self.parameterAsBoolean(parameters, self.SPLIT_AT_NODES, context)
        prune_short = True #self.parameterAsBoolean(parameters, self.PRUNE_SHORT, context)
        even_only = self.parameterAsBoolean(parameters, self.EVEN_ONLY, context)
        out_points = self.parameterAsBoolean(parameters, self.OUT_REST, context)
        dry_run = self.parameterAsBoolean(parameters, self.DRY_RUN, context)
        write_log = self.parameterAsBoolean(parameters, self.WRITE_LOG, context)
        debug_stage = self.parameterAsEnum(parameters, 'DEBUG_STAGE', context)

        # Protokoll / Log file
        logf = None
        log_path = None
        if write_log:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            desktop = self._desktop_dir()
            log_path = desktop / f"MergeLines_Debug_{ts}.txt"
            try:
                logf = open(log_path, "w", encoding="utf-8")
            except Exception:
                log_path = Path(tempfile.gettempdir()) / f"MergeLines_Debug_{ts}.txt"
                logf = open(log_path, "w", encoding="utf-8")

        def log(msg_de, msg_en=None):
            """Schreibt mehrsprachige Meldungen; fällt auf DE/EN zurück.
            Writes bilingual messages; falls back to DE/EN."""
            if not logf:
                return
            try:
                text = msg_de if self._lang() == "de" else (msg_en if msg_en is not None else msg_de)
                logf.write(str(text) + "\n")
            except Exception:
                pass

        # Kopfzeile / Header
        log("==== Linienenden nach Richtung verschmelzen (iterativ) ====",
            "==== Merge line ends by direction (iterative) ====")
        if log_path:
            log(f"Protokoll: {log_path}", f"Log file: {log_path}")
            feedback.pushInfo(self._t(f"Protokoll: {str(log_path)}",
                                      f"Log file: {str(log_path)}"))
        else:
            feedback.pushInfo(self._t("Protokoll: deaktiviert", "Log file: disabled"))
        log(
            f"Parameter: Toleranz={tol}, Winkelabweichung<= {ang_tol}°, Vereinfachung={simp_tol}, "
            f"max. Durchläufe={max_iters}, nur gerade Knoten={even_only}, "
            f"an Knotenpunkten auftrennen={split_at_nodes}",
            f"Parameters: tolerance={tol}, deviation<= {ang_tol}°, simplification={simp_tol}, "
            f"max. iterations={max_iters}, even-only nodes={even_only}, "
            f"split at junction nodes={split_at_nodes}"
        )

        # Eingabe prüfen / Check input
        if src is None:
            if logf:
                logf.close()
            raise QgsProcessingException(self._t(
                "Es wurde kein Eingabe-Layer angegeben.",
                "No input layer specified."
            ))

        # Eingabelinien sammeln (Originalpunkte) / Collect input polylines (original points)
        chains = {}  # chain_id -> list of QgsPointXY
        next_chain_id = 0

        def add_feature_geometry(g):
            nonlocal next_chain_id
            for pl in self._as_lines(g):
                if len(pl) >= 2:
                    chains[next_chain_id] = list(pl)
                    next_chain_id += 1

        # Einzel-Ebene / Single layer
        if src is not None:
            req = QgsFeatureRequest()
            # Auswahl filtern?
            if selected_only:
                try:
                    sel_ids = src.selectedFeatureIds()
                except Exception:
                    sel_ids = []
                if not sel_ids:
                    # Freundlicher, früher Abbruch:
                    raise QgsProcessingException(self._t(
                        "‚Nur gewählte Objekte‘ ist aktiv, aber es ist nichts ausgewählt.",
                        "‘Only selected features’ is enabled, but no features are selected."
                    ))
                req.setFilterFids(sel_ids)
            # Ausdrucksfilter zusätzlich (wirken zusammen als UND)
            if expr and str(expr).strip():
                req.setFilterExpression(str(expr))
            count_src = 0
            for f in src.getFeatures(req):
                add_feature_geometry(f.geometry())
                count_src += 1
            log(f"Eingabe-Ebene (einzeln): {count_src} Objekte gelesen.",
                f"Input layer (single): read {count_src} features.")

        if not chains:
            log("Keine Liniengeometrien gefunden.", "No line geometries found.")
            if logf:
                logf.close()
            raise QgsProcessingException(self._t(
                "Keine Liniengeometrien gefunden.",
                "No line geometries found."
            ))

        log(f"Startketten: {len(chains)}", f"Initial chains: {len(chains)}")

        # --- Ausgabe-Senken anlegen / Create output sinks ---
        out_fields = QgsFields()

        # CRS ermitteln / Determine CRS
        crs = None
        try:
            if src is not None:
                crs = src.crs()
        except Exception:
            crs = None
        try:
            if crs and crs.isValid():
                log(f"Ausgabe-CRS: {crs.authid()}", f"Output CRS: {crs.authid()}")
            else:
                log("Ausgabe-CRS: None/ungültig", "Output CRS: None/invalid")
        except Exception:
            pass

        # Liniensenke / Line sink
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, out_fields, QgsWkbTypes.LineString, crs)

        # Restpunkte-Senke (ggf.) / Leftover endpoints sink (optional)
        rest_sink = None
        rest_id = None
        if out_points:
            rest_fields = QgsFields()
            rest_fields.append(QgsField('cluster', QVariant.Int))
            rest_sink, rest_id = self.parameterAsSink(
                parameters, self.OUTPUT + '_POINTS', context, rest_fields,
                QgsWkbTypes.Point, crs)

        if debug_stage == 1:
            for pts in chains.values():
                if len(pts) >= 2:
                    feat = QgsFeature(out_fields)
                    feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
                    sink.addFeature(feat, QgsFeatureSink.FastInsert)
            return {self.OUTPUT: dest_id}

        # ---------- Initiale Netzzerlegung (OHNE Vereinfachung: Originalgeometrie) ----------
        if split_at_nodes:
            # 1) Alle Stützpunkte der Originalgeometrie sammeln
            verts = []  # {'chain_id','idx','pt','is_end'}
            for cid, pts in chains.items():
                n = len(pts)
                if n < 2:
                    continue
                for i, p in enumerate(pts):
                    verts.append({
                        'chain_id': cid,
                        'idx': i,
                        'pt': p,
                        'is_end': (i == 0 or i == n - 1)
                    })

            # 2) Cluster bilden
            clusters = self._cluster_points_hash(verts, tol)

            # 3) Split-Indizes sammeln:
            #    Regel (vereinfacht und robust):
            #      - Cluster muss mind. 2 Punkte enthalten (Treffpunkt),
            #      - mind. 1 Binnenpunkt ist beteiligt,
            #      - gesplittet werden ALLE Binnenpunkte in diesem Cluster.
            split_indices_per_chain = defaultdict(set)
            considered = 0
            matches = 0

            for cl_id, members in clusters.items():
                if len(members) < 2:
                    continue  # kein Treffpunkt
                # Ist irgendwo ein Binnenpunkt beteiligt?
                has_inner = any(
                    0 < m['idx'] < len(chains[m['chain_id']]) - 1
                    for m in members
                )
                if not has_inner:
                    continue  # nur Endpunkte -> jetzt nicht zerlegen

                considered += 1
                for m in members:
                    if 0 < m['idx'] < len(chains[m['chain_id']]) - 1:
                        split_indices_per_chain[m['chain_id']].add(m['idx'])
                        matches += 1

            # 4) Splits anwenden (einmalig vor der Iteration)
            if split_indices_per_chain:
                new_chains = {}
                new_id = 0
                chains_splitted = 0
                segments_created = 0

                for cid, pts in chains.items():
                    idxs = sorted(i for i in split_indices_per_chain.get(cid, set())
                                  if 0 < i < len(pts) - 1)
                    if not idxs:
                        new_chains[new_id] = pts
                        new_id += 1
                        continue

                    chains_splitted += 1
                    start = 0
                    for i in idxs:
                        seg = pts[start:i+1]
                        if len(seg) >= 2:
                            new_chains[new_id] = list(seg)
                            new_id += 1
                            segments_created += 1
                        start = i
                    # Restsegment
                    seg = pts[start:]
                    if len(seg) >= 2:
                        new_chains[new_id] = list(seg)
                        new_id += 1
                        segments_created += 1

                chains = new_chains
                next_chain_id = new_id
                log(
                    f"Vorverarbeitung (ohne Vereinfachung): {chains_splitted} Ketten aufgetrennt; "
                    f"{segments_created} Segmente; geprüfte Cluster={considered}, Treffer={matches}",
                    f"Pre-processing (no simplification): split {chains_splitted} chains; "
                    f"{segments_created} segments; clusters checked={considered}, matches={matches}"
                )
            else:
                log("Vorverarbeitung: keine Ketten zu trennen.",
                    "Pre-processing: nothing to split.")

        # ---- Nachbearbeitung Zerlegung: sehr kurze Segmente entfernen ----
        if split_at_nodes and prune_short:
            removed_cnt = 0
            new_chains = {}
            new_id = 0
            for cid, pts in chains.items():
                if self._polyline_length(pts) < tol:
                    removed_cnt += 1
                    continue
                new_chains[new_id] = pts
                new_id += 1
            if removed_cnt > 0:
                chains = new_chains
                next_chain_id = new_id
                msg_de = (f"Nachbearbeitung: {removed_cnt} sehr kurze Segmente "
                          f"(< {tol}) entfernt; verbleibend: {len(chains)}.")
                msg_en = (f"Post-processing: removed {removed_cnt} very short segments "
                          f"(< {tol}); remaining: {len(chains)}.")
                log(msg_de, msg_en)
                feedback.pushInfo(self._t(msg_de, msg_en))

        # DEBUG-Stufe 2: nach Zerlegung ausgeben und beenden
        if debug_stage == 2:
            feedback.pushInfo(self._t("DEBUG-Stop: Ausgabe nach Netzzerlegung.",
                                      "DEBUG stop: output after network split."))
            for pts in chains.values():
                if len(pts) >= 2:
                    feat = QgsFeature(out_fields)
                    feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
                    sink.addFeature(feat, QgsFeatureSink.FastInsert)
            return {self.OUTPUT: dest_id}
        # ---------- Ende der Netzzerlegung ----------

        # Iteration
        merges_total = 0
        iters_done = 0
        eps = max(tol * 0.1, 1e-12)

        while iters_done < max_iters:
            iters_done += 1

            # Endpunkte sammeln; Winkel aus vereinfachter Geometrie / Collect endpoints; angles from simplified geometry
            stubs = []
            for cid, pts in list(chains.items()):
                if len(pts) < 2:
                    continue
                sim = self._simplify_points(pts, simp_tol)
                stubs.append({
                    'chain_id': cid, 'end': 'start', 'pt': pts[0],
                    'angle': self._end_angle(sim, 'start')
                })
                stubs.append({
                    'chain_id': cid, 'end': 'end', 'pt': pts[-1],
                    'angle': self._end_angle(sim, 'end')
                })

            if not stubs:
                log("Keine Endpunkte mehr vorhanden.", "No endpoints left.")
                break

            # Cluster bilden / Build clusters
            self._cluster_endpoints(stubs, tol)

            # Knoten -> Stubs und Cluster-Mittelpunkte / Node clusters -> stubs and cluster centers
            clusters = defaultdict(list)
            for s in stubs:
                clusters[s['cluster']].append(s)

            cluster_centers = {}
            for cl_id, lst in clusters.items():
                sx = sum(s['pt'].x() for s in lst)
                sy = sum(s['pt'].y() for s in lst)
                n = len(lst)
                cluster_centers[cl_id] = QgsPointXY(sx / n, sy / n)

            log(f"-- Durchlauf {iters_done} --", f"-- Iteration {iters_done} --")
            log(f"Cluster gesamt: {len(clusters)}", f"Total clusters: {len(clusters)}")

            planned_pairs = []  # (stubA, stubB)

            # Phase 1: Zweierknoten – nur wenn Winkel passt / Two-end clusters – only if angle fits
            two_ct = 0
            for cl_id, lst in clusters.items():
                # nur bei gerader Anzahl Endpunkte / only with even number of endpoints
                if even_only and (len(lst) % 2 == 1):
                    log(f"Parität: Cluster {cl_id} hat {len(lst)} Endpunkte (ungerade) – übersprungen.",
                        f"Parity: cluster {cl_id} has {len(lst)} endpoints (odd) – skipped.")
                    continue
                if len(lst) == 2:
                    a, b = lst[0], lst[1]
                    delta = self._angle_diff(a['angle'], b['angle'])
                    deviation = abs(180.0 - delta)
                    if deviation <= ang_tol:
                        planned_pairs.append((a, b))
                        two_ct += 1
                    else:
                        log(f"Zweierknoten übersprungen: Cluster {cl_id}, Abweichung {deviation:.2f}° > {ang_tol}°",
                            f"Two-end cluster skipped: cluster {cl_id}, deviation {deviation:.2f}° > {ang_tol}°")
            log(f"Zweifingerige Knoten (verbunden): {two_ct}",
                f"Two-end clusters (merged): {two_ct}")

            # Phase 2: Mehrfachknoten – bestes Paar / Multi-end clusters – best pair
            multi_ct = 0
            chosen_ct = 0
            for cl_id, lst in clusters.items():
                # nur bei gerader Anzahl Endpunkte / only with even number of endpoints
                if even_only and (len(lst) % 2 == 1):
                    log(f"Parität: Cluster {cl_id} hat {len(lst)} Endpunkte (ungerade) – übersprungen.",
                        f"Parity: cluster {cl_id} has {len(lst)} endpoints (odd) – skipped.")
                    continue
                if len(lst) >= 3:
                    multi_ct += 1
                    best_pair = None
                    best_dev = None
                    n = len(lst)
                    best_descr_de = None
                    best_descr_en = None
                    for i in range(n):
                        for j in range(i + 1, n):
                            a_ang = lst[i]['angle']
                            b_ang = lst[j]['angle']
                            delta = self._angle_diff(a_ang, b_ang)
                            deviation = abs(180.0 - delta)
                            if (best_dev is None) or (deviation < best_dev):
                                best_dev = deviation
                                best_pair = (lst[i], lst[j])
                                best_descr_de = (f"Cluster {cl_id}: Winkel A={a_ang:.2f}°, B={b_ang:.2f}°, "
                                                 f"Δ={delta:.2f}°, Abweichung von 180°={deviation:.2f}°")
                                best_descr_en = (f"Cluster {cl_id}: angle A={a_ang:.2f}°, B={b_ang:.2f}°, "
                                                 f"Δ={delta:.2f}°, deviation from 180°={deviation:.2f}°")
                    if best_pair is not None:
                        if best_dev <= ang_tol:
                            planned_pairs.append(best_pair)
                            chosen_ct += 1
                            log(f"Gewählt: {best_descr_de} (≤ {ang_tol}°)",
                                f"Chosen: {best_descr_en} (≤ {ang_tol}°)")
                        else:
                            log(f"Übersprungen (zu „un-gerade“): {best_descr_de} (> {ang_tol}°)",
                                f"Skipped (not straight enough): {best_descr_en} (> {ang_tol}°)")
            log(f"Mehrfingrige Knoten: {multi_ct}, davon verbindbar: {chosen_ct}",
                f"Multi-end clusters: {multi_ct}, connectable: {chosen_ct}")

            if not planned_pairs or dry_run:
                if dry_run:
                    log("Probelauf: Es wurden keine Geometrien verändert.",
                        "Dry run: no geometries were modified.")
                else:
                    log("Keine geplanten Verschmelzungen – Ende.",
                        "No planned merges – stopping.")
                break

            # Geplante Verschmelzungen anwenden (Distanzbremse + Snapping) / Apply planned merges (distance gate + snapping)
            merges_this_round = 0
            used_stub = set()

            for a, b in planned_pairs:
                keyA = (a['chain_id'], a['end'])
                keyB = (b['chain_id'], b['end'])
                if keyA in used_stub or keyB in used_stub:
                    log(f"Konflikt: Stub bereits verwendet, überspringe Paar {keyA} – {keyB}.",
                        f"Conflict: stub already used, skipping pair {keyA} – {keyB}.")
                    continue
                if a['chain_id'] == b['chain_id']:
                    log(f"Selbstverbindung ignoriert: Kette {a['chain_id']} an sich selbst.",
                        f"Self-connection ignored: chain {a['chain_id']} to itself.")
                    continue
                if a['chain_id'] not in chains or b['chain_id'] not in chains:
                    log(f"Nicht mehr vorhanden: {a['chain_id']} oder {b['chain_id']}.",
                        f"Not present anymore: {a['chain_id']} or {b['chain_id']}.")
                    continue

                ptsA = chains[a['chain_id']]
                ptsB = chains[b['chain_id']]

                # Endpunkte / End points
                pA = ptsA[0] if a['end'] == 'start' else ptsA[-1]
                pB = ptsB[0] if b['end'] == 'start' else ptsB[-1]

                # Distanzbremse / Distance gate
                gap = math.hypot(pA.x() - pB.x(), pA.y() - pB.y())
                if gap > tol:
                    log(f"Übersprungen wegen Distanz: {gap:.6f} > Toleranz {tol}",
                        f"Skipped due to distance: {gap:.6f} > tolerance {tol}")
                    continue

                # Snap auf Cluster-Mittelpunkt / Snap to cluster center
                cpt = cluster_centers.get(a.get('cluster'))
                if (cpt is None) or (a.get('cluster') != b.get('cluster')):
                    cpt = QgsPointXY((pA.x() + pB.x()) / 2.0, (pA.y() + pB.y()) / 2.0)

                if a['end'] == 'start':
                    ptsA[0] = cpt
                else:
                    ptsA[-1] = cpt
                if b['end'] == 'start':
                    ptsB[0] = cpt
                else:
                    ptsB[-1] = cpt

                lenA = self._polyline_length(ptsA)
                lenB = self._polyline_length(ptsB)

                new_pts = self._connect_lines(ptsA, a['end'], ptsB, b['end'])
                new_pts = self._dedupe_consecutive(new_pts, eps)
                new_len = self._polyline_length(new_pts)

                chains[a['chain_id']] = new_pts
                del chains[b['chain_id']]

                used_stub.add(keyA)
                used_stub.add(keyB)
                merges_this_round += 1
                merges_total += 1

                log(
                    f"Verbunden: Kette {a['chain_id']} ({a['end']}, {lenA:.3f}) + "
                    f"Kette {b['chain_id']} ({b['end']}, {lenB:.3f}) -> neu {new_len:.3f} ; "
                    f"Lücke vor dem Snapping: {gap:.6f}",
                    f"Merged: chain {a['chain_id']} ({a['end']}, {lenA:.3f}) + "
                    f"chain {b['chain_id']} ({b['end']}, {lenB:.3f}) -> new {new_len:.3f} ; "
                    f"gap before snapping: {gap:.6f}"
                )

            log(f"Verschmelzungen in diesem Durchlauf: {merges_this_round}",
                f"Merges in this iteration: {merges_this_round}")
            feedback.pushInfo(self._t(
                f"Durchlauf {iters_done}: {merges_this_round} Verschmelzungen.",
                f"Iteration {iters_done}: {merges_this_round} merges."
            ))
            if merges_this_round == 0:
                log("Keine Änderungen in diesem Durchlauf – Ende.",
                    "No changes in this iteration – stopping.")
                break
            if debug_stage == 3 and iters_done >= 1:
                for pts in chains.values():
                    if len(pts) >= 2:
                        feat = QgsFeature(out_fields)
                        feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
                        sink.addFeature(feat, QgsFeatureSink.FastInsert)
                return {self.OUTPUT: dest_id}

        # Ausgabe schreiben / Write output
        for pts in chains.values():
            if len(pts) >= 2:
                feat = QgsFeature(out_fields)
                feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
                sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Restpunkte (optional) / Leftover endpoints (optional)
        unpaired_count = 0
        if out_points and (rest_sink is not None):
            stubs_final = []
            for cid, pts in chains.items():
                if len(pts) < 2:
                    continue
                sim = self._simplify_points(pts, simp_tol)
                stubs_final.append({'chain_id': cid, 'end': 'start', 'pt': pts[0],
                                    'angle': self._end_angle(sim, 'start')})
                stubs_final.append({'chain_id': cid, 'end': 'end', 'pt': pts[-1],
                                    'angle': self._end_angle(sim, 'end')})
            self._cluster_endpoints(stubs_final, tol)
            for s in stubs_final:
                f = QgsFeature(rest_sink.fields())
                f.setGeometry(QgsGeometry.fromPointXY(s['pt']))
                f.setAttributes([int(s['cluster'])])
                rest_sink.addFeature(f, QgsFeatureSink.FastInsert)
                unpaired_count += 1

        # Statistik / Summary
        log("—— Zusammenfassung ——", "—— Summary ——")
        log(f"Durchläufe: {iters_done}", f"Iterations: {iters_done}")
        log(f"Verschmolzene Paare: {merges_total}", f"Merged pairs: {merges_total}")
        log(f"Ausgabeketten: {len(chains)}", f"Output chains: {len(chains)}")
        if out_points:
            log(f"Rest-Endpunkte: {unpaired_count}", f"Leftover endpoints: {unpaired_count}")

        feedback.pushInfo(self._t("—— Statistik ——", "—— Statistics ——"))
        feedback.pushInfo(self._t(f"Durchläufe: {iters_done}", f"Iterations: {iters_done}"))
        feedback.pushInfo(self._t(f"Verschmolzene Paare: {merges_total}",
                                  f"Merged pairs: {merges_total}"))
        feedback.pushInfo(self._t(f"Ausgabeketten: {len(chains)}",
                                  f"Output chains: {len(chains)}"))
        if out_points:
            feedback.pushInfo(self._t(f"Rest-Endpunkte: {unpaired_count}",
                                      f"Leftover endpoints: {unpaired_count}"))

        if logf:
            try:
                logf.flush()
                logf.close()
            except Exception:
                pass

        results = {self.OUTPUT: dest_id}
        if out_points and (rest_sink is not None):
            results[self.OUTPUT + '_POINTS'] = rest_id
        return results


# Registrierung als Skript in QGIS / How to register script in QGIS:
# 1) Als Skript-Algorithmus im Werkzeugkasten speichern. / Save as a script algorithm in the Processing Toolbox.
# 2) Danach erscheint es unter „Netz-Bereinigung“ / It will then appear under “Network cleaning”.
